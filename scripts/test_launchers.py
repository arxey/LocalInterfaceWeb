"""Run the portable launchers against a fake Docker CLI, without a daemon."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="photo service ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in ("start.sh", "stop.sh", "scripts/launch.sh", "scripts/launch.ps1"):
            self.assertTrue((ROOT / name).is_file(), f"Portable launcher is missing: {name}")
            shutil.copyfile(ROOT / name, self.root / name)
        self.log = self.root / "docker-calls.txt"
        self.env = os.environ | {
            "FAKE_DOCKER_LOG": str(self.log), "NO_BROWSER": "1",
            "FAKE_DOCKER_VERSION": "2.24.0", "FAKE_DOCKER_FAIL": "",
        }
        if os.name == "nt":
            (self.root / "docker.cmd").write_text(
                '@echo off\n'
                'echo %*>>"%FAKE_DOCKER_LOG%"\n'
                'if "%1"=="info" if "%FAKE_DOCKER_FAIL%"=="daemon" exit /b 42\n'
                'if "%1"=="info" (echo linux& exit /b 0)\n'
                'if "%2"=="version" (echo %FAKE_DOCKER_VERSION%& exit /b 0)\n'
                'if "%FAKE_DOCKER_FAIL%"=="up" (\n'
                '  echo %* | findstr /c:" up " >nul\n'
                '  if not errorlevel 1 exit /b 42\n'
                ')\nexit /b 0\n'
            )
            self.command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(self.root / "scripts/launch.ps1"), "-NoBrowser"]
        else:
            docker = self.root / "docker"
            docker.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\n'
                'if [ "$1" = info ]; then [ "$FAKE_DOCKER_FAIL" != daemon ] || exit 42; echo linux; exit 0; fi\n'
                'if [ "$2" = version ]; then echo "$FAKE_DOCKER_VERSION"; exit 0; fi\n'
                'case "$*" in *" up "*) [ "$FAKE_DOCKER_FAIL" != up ] || exit 42;; esac\n'
                'exit 0\n'
            )
            docker.chmod(0o700)
            self.command = ["sh", str(self.root / "scripts/launch.sh")]
        self.env["PATH"] = str(self.root) + os.pathsep + self.env["PATH"]

    def launch(self, action="start"):
        args = ["-Action", action] if os.name == "nt" else [action]
        return subprocess.run(self.command + args, cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_start_creates_secrets_once_without_changing_user_dotenv(self):
        (self.root / ".env").write_text("KEEP=original\n")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        content = (self.root / ".env.docker").read_bytes()
        values = dict(line.split("=", 1) for line in content.decode().splitlines()
                      if line and not line.startswith("#"))
        for name in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "ADMIN_PASSWORD", "PGADMIN_DEFAULT_PASSWORD"):
            self.assertRegex(values[name], r"^[a-f0-9]{64}$")
        self.assertEqual(len({values[name] for name in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "ADMIN_PASSWORD")}), 3)
        self.assertEqual((self.root / ".env").read_text(), "KEEP=original\n")
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual((self.root / ".env.docker").read_bytes(), content)
        self.assertIn("up -d --build --wait", self.log.read_text())

    def test_failed_start_returns_failure_and_keeps_generated_secrets(self):
        self.env["FAKE_DOCKER_FAIL"] = "up"
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / ".env.docker").exists())
        self.assertIn("logs --tail", self.log.read_text())

    def test_stop_keeps_the_saved_configuration_and_volumes(self):
        self.assertEqual(self.launch().returncode, 0)
        before = (self.root / ".env.docker").read_bytes()
        result = self.launch("stop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / ".env.docker").read_bytes(), before)
        self.assertIn("down", self.log.read_text())
        self.assertNotIn("--volumes", self.log.read_text())
        self.assertNotIn(" -v", self.log.read_text())

    def test_old_compose_fails_before_generating_configuration(self):
        self.env["FAKE_DOCKER_VERSION"] = "2.19.0"
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2.24", result.stdout + result.stderr)
        self.assertFalse((self.root / ".env.docker").exists())

    def test_stopped_docker_reports_how_to_recover(self):
        self.env["FAKE_DOCKER_FAIL"] = "daemon"
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Start Docker Desktop", result.stdout + result.stderr)
        self.assertFalse((self.root / ".env.docker").exists())


if __name__ == "__main__":
    unittest.main()
