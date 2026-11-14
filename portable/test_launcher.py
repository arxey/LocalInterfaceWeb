"""Native-launcher checks that need neither PostgreSQL nor installed packages."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import launcher
except ModuleNotFoundError:
    launcher = None


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(launcher, "The native portable launcher has not been implemented")
        self.directory = tempfile.TemporaryDirectory(prefix="Фото архив ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "data").mkdir()

    def test_configuration_survives_restart_and_relocation_without_rotating_passwords(self):
        config = launcher.load_config(self.root)
        saved = (self.root / "data/config.json").read_bytes()
        for field in ("django_secret", "database_password", "superuser_password", "admin_password"):
            self.assertRegex(config[field], r"^[a-f0-9]{64}$")
        self.assertEqual(len({config[field] for field in ("database_password", "superuser_password", "admin_password")}), 3)
        self.assertEqual(launcher.load_config(self.root), config)
        moved = self.root / "Перенос с пробелами"
        moved.mkdir()
        shutil.move(str(self.root / "data"), str(moved / "data"))
        self.assertEqual(launcher.load_config(moved), config)
        self.assertEqual((moved / "data/config.json").read_bytes(), saved)
        self.assertNotIn(str(self.root), saved.decode())

    @unittest.skipUnless(os.name == "nt", "Windows per-user DOS drive mapping")
    def test_non_ascii_postgres_paths_use_a_temporary_ascii_drive_and_preserve_files(self):
        marker = self.root / "data/中文.txt"
        marker.write_text("original data", encoding="utf-8")
        paths = launcher.PostgresPaths(self.root)
        self.addCleanup(paths.close)
        paths.acquire()
        self.assertTrue(str(paths.base).isascii())
        self.assertEqual((paths.base / "data/中文.txt").read_text(encoding="utf-8"), "original data")
        record = json.loads((self.root / "data/dos-alias.json").read_text(encoding="utf-8"))
        self.assertEqual(launcher.DosDriveAPI().query(record["drive"]), record["target"])
        paths.close()
        self.assertIsNone(launcher.DosDriveAPI().query(record["drive"]))
        self.assertFalse((self.root / "data/dos-alias.json").exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "original data")

    @unittest.skipUnless(os.name == "nt", "Windows per-user DOS drive mapping")
    def test_stale_owned_dos_alias_is_reclaimed_before_allocating_another(self):
        first = launcher.PostgresPaths(self.root)
        self.addCleanup(first.close)
        first.acquire()
        second = launcher.PostgresPaths(self.root)
        self.addCleanup(second.close)
        second.acquire()
        record = json.loads((self.root / "data/dos-alias.json").read_text(encoding="utf-8"))
        self.assertEqual(launcher.DosDriveAPI().query(record["drive"]), record["target"])
        second.close()
        self.assertIsNone(launcher.DosDriveAPI().query(record["drive"]))

    @unittest.skipUnless(os.name == "nt", "Windows per-user DOS drive mapping")
    def test_stale_alias_metadata_does_not_remove_an_unrelated_drive(self):
        paths = launcher.PostgresPaths(self.root)
        self.addCleanup(paths.close)
        paths.acquire()
        record = json.loads((self.root / "data/dos-alias.json").read_text(encoding="utf-8"))
        record["target"] += "-different"
        (self.root / "data/dos-alias.json").write_text(json.dumps(record))
        launcher.cleanup_stale_dos_alias(self.root)
        self.assertEqual(launcher.DosDriveAPI().query(record["drive"]), paths.target)

    def test_corrupt_metadata_cannot_remove_a_matching_but_foreign_dos_mapping(self):
        foreign_target = "\\??\\C:\\UnrelatedExistingApplication"
        (self.root / "data/dos-alias.json").write_text(json.dumps({
            "version": 1, "drive": "Z:", "target": foreign_target, "token": "corrupt-record",
        }), encoding="utf-8")
        class ForeignDrive:
            def query(self, drive):
                return foreign_target
            def remove(self, drive, target):
                raise AssertionError("A foreign mapping must never be removed, even when metadata matches it")
        launcher.cleanup_stale_dos_alias(self.root, ForeignDrive())

    def test_missing_configuration_never_reinitializes_an_existing_database(self):
        database = self.root / "data/postgres"
        database.mkdir()
        (database / "PG_VERSION").write_text("17\n")
        with self.assertRaisesRegex(launcher.LaunchError, "config.json"):
            launcher.load_config(self.root)
        self.assertFalse((self.root / "data/config.json").exists())
        self.assertEqual((database / "PG_VERSION").read_text(), "17\n")

    def test_corrupt_saved_configuration_is_preserved_and_reported(self):
        config = self.root / "data/config.json"
        config.write_text("{broken")
        with self.assertRaisesRegex(launcher.LaunchError, "config.json"):
            launcher.load_config(self.root)
        self.assertEqual(config.read_text(), "{broken")

    def test_busy_ports_are_replaced_without_changing_saved_credentials(self):
        config = launcher.load_config(self.root)
        held = socket.socket()
        held.bind(("127.0.0.1", config["app_port"]))
        held.listen()
        self.addCleanup(held.close)
        updated = launcher.select_ports(self.root, config)
        self.assertNotEqual(updated["app_port"], config["app_port"])
        self.assertEqual(updated["admin_password"], config["admin_password"])
        self.assertEqual(launcher.load_config(self.root), updated)

    def test_process_environment_ignores_sqlite_dotenv_and_inherited_database_configuration(self):
        config = launcher.load_config(self.root)
        (self.root / ".env").write_text("USE_SQLITE_FOR_TESTS=1\nPOSTGRES_PASSWORD=wrong\n")
        env = launcher.process_environment(self.root, config, {
            "PATH": "unrelated-path", "USE_SQLITE_FOR_TESTS": "1", "PGDATA": "outside",
            "PGSERVICE": "other", "PGOPTIONS": "-c session_authorization=other",
            "PGPASSWORD": "wrong", "PYTHONPATH": "other", "PYTHONHOME": "other",
            "DJANGO_DEBUG": "1", "PORTABLE_FRONTEND_ROOT": "outside",
        })
        for name in ("USE_SQLITE_FOR_TESTS", "PGDATA", "PGSERVICE", "PGOPTIONS", "PGPASSWORD", "PYTHONPATH", "PYTHONHOME"):
            self.assertNotIn(name, env)
        self.assertEqual(env["POSTGRES_PASSWORD"], config["database_password"])
        self.assertEqual(env["POSTGRES_HOST"], "127.0.0.1")
        self.assertEqual(env["DJANGO_PORTABLE"], "1")
        self.assertEqual(env["DJANGO_DEBUG"], "0")
        self.assertEqual(Path(env["DJANGO_MEDIA_ROOT"]).resolve(), (self.root / "data/media").resolve())
        self.assertEqual(Path(env["PORTABLE_FRONTEND_ROOT"]).resolve(), (self.root / "frontend/dist").resolve())

    def test_only_one_instance_can_hold_the_data_directory_lock(self):
        first = launcher.InstanceLock(self.root / "data/run.lock")
        second = launcher.InstanceLock(self.root / "data/run.lock")
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.close()
        self.assertTrue(second.acquire())

    def test_stop_uses_the_active_instance_token(self):
        lock = launcher.InstanceLock(self.root / "data/run.lock")
        self.addCleanup(lock.close)
        self.assertTrue(lock.acquire())
        (self.root / "data/running.json").write_text(json.dumps({"token": "active-session", "url": "http://127.0.0.1:12345"}))
        self.assertTrue(launcher.request_stop(self.root))
        request = json.loads((self.root / "data/stop-request.json").read_text())
        self.assertEqual(request["token"], "active-session")
        self.assertTrue(launcher.stop_requested(self.root, "active-session"))
        self.assertFalse(launcher.stop_requested(self.root, "different-session"))

    def test_reopen_accepts_only_an_explicit_local_http_application_address(self):
        for url in ("http://127.0.0.1:8080", "http://localhost:12345/"):
            with self.subTest(url=url):
                self.assertTrue(launcher.is_loopback_url(url))
        for url in ("http://localhost:8080@external.example", "http://127.0.0.1.external.example:8080",
                    "https://127.0.0.1:8080", "http://localhost", "http://localhost:not-a-port",
                    "http://127.0.0.1:8080/redirect", "http://localhost:8080?next=external", None):
            with self.subTest(url=url):
                self.assertFalse(launcher.is_loopback_url(url))

    def test_stale_pid_metadata_never_terminates_an_unrelated_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        def cleanup():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.addCleanup(cleanup)
        (self.root / "data/running.json").write_text(json.dumps({"token": "stale", "pid": child.pid}))
        self.assertFalse(launcher.request_stop(self.root))
        self.assertIsNone(child.poll())
        self.assertFalse((self.root / "data/stop-request.json").exists())

    def test_partial_database_directory_is_never_deleted_or_reused(self):
        database = self.root / "data/postgres"
        database.mkdir()
        marker = database / "important.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(launcher.LaunchError, "postgres"):
            launcher.database_needs_initialization(self.root)
        self.assertEqual(marker.read_text(), "keep")

    def test_database_owner_verification_reads_only_ascii_pid_from_raw_cp1251_file(self):
        database = self.root / "data/postgres"
        database.mkdir()
        raw = b"24680\n" + "C:/Фото архив/data/postgres".encode("cp1251") + b"\n12345\n55432\n"
        (database / "postmaster.pid").write_bytes(raw)
        class Cursor:
            def execute(self, statement):
                if statement != "SELECT pg_read_binary_file('postmaster.pid', 0, 64)":
                    raise AssertionError("Ownership must be checked without decoding the data directory")
            def fetchone(self):
                return (memoryview(raw[:64]),)
        launcher.verify_database_owner(self.root, 24680, Cursor())

    def test_database_owner_verification_rejects_a_foreign_pid(self):
        database = self.root / "data/postgres"
        database.mkdir()
        (database / "postmaster.pid").write_bytes(b"24680\nlocal path\n")
        class Cursor:
            def execute(self, statement):
                pass
            def fetchone(self):
                return (b"13579\nforeign path\n",)
        with self.assertRaisesRegex(launcher.LaunchError, "another installation"):
            launcher.verify_database_owner(self.root, 24680, Cursor())

    def test_database_owner_verification_checks_local_pid_before_contacting_the_server(self):
        database = self.root / "data/postgres"
        database.mkdir()
        (database / "postmaster.pid").write_bytes(b"13579\nforeign path\n")
        class Cursor:
            def execute(self, statement):
                raise AssertionError("Do not query a server whose local PID does not belong to our process")
        with self.assertRaisesRegex(launcher.LaunchError, "another installation"):
            launcher.verify_database_owner(self.root, 24680, Cursor())

    def test_owned_child_is_stopped_when_process_group_closes(self):
        group = launcher.ChildProcesses()
        self.addCleanup(group.close)
        child = group.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        group.close()
        self.assertIsNotNone(child.poll())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object descendant cleanup")
    def test_process_group_closes_descendant_server_without_signalling_any_external_pid(self):
        port = launcher.available_port(0)
        server_code = f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(30)"
        parent_code = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{server_code!r}]); time.sleep(30)"
        group = launcher.ChildProcesses()
        self.addCleanup(group.close)
        group.spawn([sys.executable, "-c", parent_code])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("The descendant test server did not start")
        group.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            self.fail("A descendant server survived closing its launcher Job Object")


if __name__ == "__main__":
    unittest.main()
