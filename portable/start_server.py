"""Entry point used by the bundled, isolated Python interpreter."""

import logging
import os
from pathlib import Path
import sys


def main():
    sys.dont_write_bytecode = True
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["DJANGO_PORTABLE"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if sys.argv[1:2] == ["--manage"]:
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", *sys.argv[2:]])
        return
    from config.wsgi import application
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    serve(application, host="127.0.0.1", port=int(os.environ["APP_PORT"]), threads=4,
          max_request_body_size=11 * 1024 * 1024, max_request_header_size=32768,
          expose_tracebacks=False, channel_timeout=60, connection_limit=100,
          clear_untrusted_proxy_headers=True, ident="Photo Service")


if __name__ == "__main__":
    main()
