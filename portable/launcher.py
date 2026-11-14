"""Offline Windows launcher. All mutable state belongs to this bundle's data/."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


class LaunchError(Exception):
    """A recoverable startup failure suitable for display in the console."""


class StopRequested(Exception):
    pass


def safe_directory(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise LaunchError(f"Unsafe data path outside this application: {relative}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_json(path, value):
    """Replace a small file atomically; do not leave partially written secrets."""
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise LaunchError(f"Cannot read {path.name}. Restore the original file; saved data was not changed.") from error
    if not isinstance(result, dict):
        raise LaunchError(f"Invalid {path.name}. Restore the original file; saved data was not changed.")
    return result


def available_port(preferred, excluded=()):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            if preferred in excluded:
                raise OSError("Port is already reserved")
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def load_config(root):
    data = safe_directory(root, "data")
    path = data / "config.json"
    if path.exists():
        config = read_json(path)
        for name in ("django_secret", "database_password", "superuser_password", "admin_password"):
            if not isinstance(config.get(name), str) or len(config[name]) < 32:
                raise LaunchError(f"Invalid {name} in data/config.json. Restore the original configuration.")
        for name in ("app_port", "database_port"):
            if type(config.get(name)) is not int or not 1024 <= config[name] <= 65535:
                raise LaunchError(f"Invalid {name} in data/config.json.")
        if config.get("version") != 1 or config.get("admin_username") != "admin":
            raise LaunchError("Unsupported data/config.json. Use the matching application version.")
        return config
    database = data / "postgres"
    if database.exists() and any(database.iterdir()):
        raise LaunchError("The database already exists but data/config.json is missing. Restore its original config.json; a new password cannot open the existing database.")
    app_port = available_port(8080)
    config = {
        "version": 1, "app_port": app_port,
        "database_port": available_port(55432, (app_port,)),
        "django_secret": secrets.token_hex(32), "database_password": secrets.token_hex(32),
        "superuser_password": secrets.token_hex(32), "admin_username": "admin",
        "admin_password": secrets.token_hex(32),
    }
    write_json(path, config)
    return config


def select_ports(root, config):
    selected = config | {"app_port": available_port(config["app_port"])}
    selected["database_port"] = available_port(config["database_port"], (selected["app_port"],))
    if selected != config:
        write_json(Path(root) / "data/config.json", selected)
    return selected


def process_environment(root, config, inherited=None):
    root = Path(root).resolve()
    inherited = os.environ if inherited is None else inherited
    # Neither another PostgreSQL installation nor a development .env may change
    # this bundle's connection, authentication, interpreter, or data directory.
    env = {name: value for name, value in inherited.items()
           if not name.upper().startswith(("PG", "POSTGRES", "DJANGO", "PYTHON", "PORTABLE", "ADMIN_"))
           and name.upper() not in {"USE_SQLITE_FOR_TESTS", "APP_PORT", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    temporary = safe_directory(root, "data/tmp")
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1",
        "DJANGO_PORTABLE": "1", "DJANGO_DEBUG": "0", "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": config["django_secret"], "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "DJANGO_SECURE_COOKIES": "0",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f'http://localhost:{config["app_port"]},http://127.0.0.1:{config["app_port"]}',
        "PORTABLE_FRONTEND_ROOT": str(root / "frontend/dist"),
        "DJANGO_MEDIA_ROOT": str(root / "data/media"), "DJANGO_STATIC_ROOT": str(root / "data/static"),
        "POSTGRES_DB": "photo_service", "POSTGRES_USER": "photo_service",
        "POSTGRES_PASSWORD": config["database_password"], "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(config["database_port"]), "APP_PORT": str(config["app_port"]),
        "ADMIN_USERNAME": config["admin_username"], "ADMIN_PASSWORD": config["admin_password"],
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
    })
    return env


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        if self.stream is not None:
            return True
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def close(self):
        if self.stream is None:
            return
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def request_stop(root):
    data = Path(root) / "data"
    if not data.is_dir():
        return False
    lock = InstanceLock(data / "run.lock")
    try:
        if lock.acquire():
            return False
        state = read_json(data / "running.json")
        token = state.get("token")
        if not isinstance(token, str) or not token:
            raise LaunchError("The application is still starting. Try stopping it again in a moment.")
        write_json(data / "stop-request.json", {"token": token})
        return True
    finally:
        lock.close()


def stop_requested(root, token):
    path = Path(root) / "data/stop-request.json"
    if not path.is_file():
        return False
    try:
        return read_json(path).get("token") == token
    except LaunchError:
        return False


class ChildProcesses:
    """Own handles, never kill an arbitrary PID read from disk.

    On Windows a kill-on-close Job Object also cleans up descendants if the
    console is closed or the launcher is terminated unexpectedly.
    """
    def __init__(self):
        self.processes = []
        self.job = None
        self.kernel = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.job = kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(self.job)
            self.job = None
            raise error

    def spawn(self, arguments, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen([str(argument) for argument in arguments], **kwargs)
        self.processes.append(process)
        if self.job and not self.kernel.AssignProcessToJobObject(self.job, int(process._handle)):
            process.terminate()
            process.wait(timeout=10)
            raise LaunchError("Windows could not supervise the application processes. Start it in a normal desktop session.")
        return process

    @staticmethod
    def stop(process, timeout=10):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def close(self):
        for process in reversed(self.processes):
            self.stop(process)
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None


def database_needs_initialization(root):
    database = safe_directory(root, "data/postgres")
    if (database / "PG_VERSION").is_file():
        return False
    if any(database.iterdir()):
        raise LaunchError("data/postgres is incomplete or contains other files. It was preserved. Restore a database backup or extract a fresh application into a new folder.")
    return True


def check_bundle(root):
    required = ("runtime/python/python.exe", "runtime/postgresql/bin/initdb.exe",
                "runtime/postgresql/bin/postgres.exe", "runtime/postgresql/bin/pg_ctl.exe",
                "backend/manage.py", "frontend/dist/index.html", "portable/start_server.py")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise LaunchError("Incomplete portable application: " + ", ".join(missing)
                          + ". Extract the complete Windows portable ZIP into a writable folder; a GitHub source ZIP does not include its runtime.")


def log_tail(path):
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:])
    except OSError:
        return ""


def run_command(group, arguments, root, env, log, timeout=120):
    with log.open("ab", buffering=0) as output:
        process = group.spawn(arguments, cwd=root, env=env, stdin=subprocess.DEVNULL,
                              stdout=output, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            group.stop(process)
            raise LaunchError(f"Command timed out. See data/logs/{log.name}.") from error
    if code:
        raise LaunchError(f"A startup command failed (exit {code}). See data/logs/{log.name}.\n{log_tail(log)}")


def initialize_database(root, config, group, env):
    if not database_needs_initialization(root):
        return
    print("Creating the local PostgreSQL database...", flush=True)
    descriptor, filename = tempfile.mkstemp(prefix=".pg-password-", dir=root / "data")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(config["superuser_password"] + "\n")
        run_command(group, [root / "runtime/postgresql/bin/initdb.exe", "-D", root / "data/postgres",
                           "--username=portable_admin", "--encoding=UTF8", "--locale=C",
                           "--auth-local=scram-sha-256", "--auth-host=scram-sha-256", "--pwfile", filename],
                    root, env, root / "data/logs/initdb.log")
    finally:
        Path(filename).unlink(missing_ok=True)


def database_connection(config, database="postgres"):
    try:
        import psycopg
    except ImportError as error:
        raise LaunchError("The bundled PostgreSQL Python driver is missing. Extract the complete portable ZIP again.") from error
    return psycopg.connect(dbname=database, user="portable_admin", password=config["superuser_password"],
                           host="127.0.0.1", port=config["database_port"], connect_timeout=2, autocommit=True)


def wait_for_database(root, config, process, token, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_requested(root, token):
            raise StopRequested()
        if process.poll() is not None:
            raise LaunchError("PostgreSQL could not start. See data/logs/postgresql.log.\n" + log_tail(root / "data/logs/postgresql.log"))
        try:
            with database_connection(config):
                return
        except Exception as error:
            if isinstance(error, LaunchError):
                raise
            time.sleep(0.3)
    raise LaunchError("PostgreSQL did not become ready. See data/logs/postgresql.log. Saved data was preserved.")


def postmaster_pid(raw):
    # The first line is always ASCII. Later Windows path lines can be encoded
    # in the operating-system code page rather than the database's UTF-8.
    first = bytes(raw).partition(b"\n")[0].strip()
    if not first.isdigit():
        raise ValueError("Invalid postmaster PID")
    return int(first)


def local_postmaster_pid(root):
    with (root / "data/postgres/postmaster.pid").open("rb") as stream:
        return postmaster_pid(stream.read(64))


def verify_database_owner(root, owned_pid, cursor):
    message = "The database port belongs to another installation. Its data was not changed."
    try:
        if local_postmaster_pid(root) != owned_pid:
            raise LaunchError(message)
        cursor.execute("SELECT pg_read_binary_file('postmaster.pid', 0, 64)")
        remote_pid = postmaster_pid(cursor.fetchone()[0])
        if remote_pid != owned_pid or local_postmaster_pid(root) != owned_pid:
            raise LaunchError(message)
    except (OSError, ValueError, TypeError, IndexError) as error:
        raise LaunchError(message) from error


def prepare_database(root, config, owned_pid):
    from psycopg import sql
    with database_connection(config) as connection:
        with connection.cursor() as cursor:
            verify_database_owner(root, owned_pid, cursor)
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("photo_service",))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier("photo_service"), sql.Literal(config["database_password"])))
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("photo_service",))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
                    sql.Identifier("photo_service"), sql.Identifier("photo_service")))


def wait_for_http(root, url, process, database, token, timeout=60):
    deadline = time.monotonic() + timeout
    # Do not inherit an operating-system HTTP proxy for local readiness probes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if stop_requested(root, token):
            raise StopRequested()
        if process.poll() is not None or database.poll() is not None:
            raise LaunchError("The local server stopped during startup. See data/logs/server.log and postgresql.log.\n" + log_tail(root / "data/logs/server.log"))
        try:
            with opener.open(url + "/api/health/", timeout=2) as response:
                if response.status == 200 and json.load(response).get("database") == "ok":
                    return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise LaunchError("The local web server did not become ready. See data/logs/server.log.")


def stop_database(root, process, group, env):
    if process is None or process.poll() is not None:
        return
    # Verify the live child against this data directory before asking pg_ctl to
    # signal it. Never act on a stale PID or an unrelated PostgreSQL server.
    try:
        pid = local_postmaster_pid(root)
    except (OSError, ValueError, IndexError):
        pid = None
    if pid == process.pid:
        try:
            run_command(group, [root / "runtime/postgresql/bin/pg_ctl.exe", "-D", root / "data/postgres",
                                "stop", "-m", "fast", "-w", "-t", "20"],
                        root, env, root / "data/logs/shutdown.log", timeout=25)
            process.wait(timeout=5)
            return
        except (LaunchError, subprocess.TimeoutExpired) as error:
            print(f"PostgreSQL shutdown: {error}", file=sys.stderr, flush=True)
    group.stop(process)


def open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        print("Open this address in your browser: " + url, flush=True)


def is_loopback_url(url):
    if not isinstance(url, str):
        return False
    try:
        address = urllib.parse.urlsplit(url)
        return (address.scheme == "http" and address.hostname in {"127.0.0.1", "localhost"}
                and address.username is None and address.password is None
                and address.port is not None and 1024 <= address.port <= 65535
                and address.path in {"", "/"} and not address.query and not address.fragment)
    except ValueError:
        return False


def stop_application(root):
    if not request_stop(root):
        print("The portable application is already stopped.", flush=True)
        return 0
    print("Stopping the portable application...", flush=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        lock = InstanceLock(root / "data/run.lock")
        try:
            if lock.acquire():
                print("Stopped. Photos, database, and passwords were preserved.", flush=True)
                return 0
        finally:
            lock.close()
        time.sleep(0.25)
    raise LaunchError("Shutdown is still in progress. Keep the application's console open and check data/logs.")


def launch(root, no_browser=False, headless=False):
    check_bundle(root)
    try:
        data = safe_directory(root, "data")
        descriptor, probe = tempfile.mkstemp(prefix=".write-check-", dir=data)
        os.close(descriptor)
        Path(probe).unlink()
    except OSError as error:
        raise LaunchError("This folder is read-only. Extract the complete ZIP into your Documents folder, then start again.") from error
    lock = InstanceLock(data / "run.lock")
    if not lock.acquire():
        state = read_json(data / "running.json")
        url = state.get("url", "")
        print("The application is already running. " + url, flush=True)
        if state.get("status") == "ready" and is_loopback_url(url) and not no_browser and not headless:
            open_browser(url)
        return 0
    group = None
    database = None
    server = None
    env = None
    token = secrets.token_hex(24)
    state = None
    try:
        for relative in ("data/media", "data/static", "data/logs", "data/tmp"):
            safe_directory(root, relative)
        config = select_ports(root, load_config(root))
        env = process_environment(root, config)
        state = {"token": token, "url": f'http://127.0.0.1:{config["app_port"]}', "status": "starting"}
        (data / "stop-request.json").unlink(missing_ok=True)
        write_json(data / "running.json", state)
        group = ChildProcesses()
        initialize_database(root, config, group, env)
        if stop_requested(root, token):
            raise StopRequested()
        with (data / "logs/postgresql.log").open("ab", buffering=0) as output:
            database = group.spawn([root / "runtime/postgresql/bin/postgres.exe", "-D", data / "postgres",
                                    "-h", "127.0.0.1", "-p", str(config["database_port"]),
                                    "-c", "shared_buffers=64MB", "-c", "max_connections=20",
                                    "-c", "password_encryption=scram-sha-256", "-c", "logging_collector=off"],
                                   cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        wait_for_database(root, config, database, token)
        prepare_database(root, config, database.pid)
        python = root / "runtime/python/python.exe"
        entry = root / "portable/start_server.py"
        for command in (("migrate", "--noinput"), ("collectstatic", "--noinput"), ("bootstrap_admin", "--load-demo")):
            if stop_requested(root, token):
                raise StopRequested()
            print("Preparing application: " + command[0] + "...", flush=True)
            run_command(group, [python, "-I", "-B", entry, "--manage", *command], root, env, data / "logs/setup.log")
        with (data / "logs/server.log").open("ab", buffering=0) as output:
            server = group.spawn([python, "-I", "-B", entry], cwd=root, env=env, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT)
        wait_for_http(root, state["url"], server, database, token)
        state["status"] = "ready"
        write_json(data / "running.json", state)
        print("Ready: " + state["url"], flush=True)
        if not headless:
            print(f'Initial administrator: {config["admin_username"]}\nInitial password: {config["admin_password"]}', flush=True)
            print("Credentials: data/config.json. Existing account passwords are never reset on startup.", flush=True)
        print("Keep this window open. Press Ctrl+C or run stop-portable.cmd to stop safely.", flush=True)
        if not no_browser and not headless:
            open_browser(state["url"])
        while not stop_requested(root, token):
            if database.poll() is not None or server.poll() is not None:
                raise LaunchError("An application process stopped unexpectedly. See data/logs/server.log and postgresql.log.")
            time.sleep(0.3)
        return 0
    except (KeyboardInterrupt, StopRequested):
        print("Stopping...", flush=True)
        return 0
    finally:
        # Ignore further Ctrl+C while PostgreSQL completes its fast shutdown.
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if group:
                group.stop(server)
                stop_database(root, database, group, env)
                group.close()
            (data / "running.json").unlink(missing_ok=True)
            (data / "stop-request.json").unlink(missing_ok=True)
        finally:
            lock.close()
            signal.signal(signal.SIGINT, previous_handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true", help="Request shutdown of this bundle only")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--headless", action="store_true", help="Do not open a browser or display credentials")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    try:
        if os.name != "nt" or sys.maxsize <= 2**32:
            raise LaunchError("This portable application requires Windows 10/11 x64.")
        return stop_application(root) if args.stop else launch(root, args.no_browser, args.headless)
    except (LaunchError, OSError) as error:
        print("Startup failed: " + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
