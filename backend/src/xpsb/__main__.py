"""Entry point of the backend sidecar.

Protocol with the desktop shell:
- The token comes from --token or the XPSB_TOKEN environment variable (never
  from the command line in production, so it does not show up in process lists).
- After binding, exactly one line `XPSB_READY {"port": ..., "version": ...}` is
  written to stdout.
- With --watch-stdin the process exits as soon as stdin is closed, i.e. when
  the desktop app is gone.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from xpsb import __version__
from xpsb.api.app import AppContext, create_app
from xpsb.jobs.manager import JobManager
from xpsb.paths import AppPaths, resolve_paths
from xpsb.secrets import KeyringSecretStore, MemorySecretStore
from xpsb.settings import SettingsStore

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="xpsb-backend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    p.add_argument("--token", default=None)
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument(
        "--resources-dir",
        type=Path,
        default=None,
        help="app resources (bundled DSFTool lives in <dir>/bin)",
    )
    p.add_argument("--watch-stdin", action="store_true")
    p.add_argument("--dev-cors", action="store_true", help="allow the Vite dev server origin")
    p.add_argument("--log-level", default="info")
    return p.parse_args(argv)


def _setup_logging(paths: AppPaths, level: str) -> None:
    handler = logging.handlers.RotatingFileHandler(
        paths.logs / "backend.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addHandler(handler)
    if sys.stderr:
        root.addHandler(logging.StreamHandler(sys.stderr))


def _watch_stdin(server: uvicorn.Server) -> None:
    def run() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        server.should_exit = True

    threading.Thread(target=run, name="stdin-watchdog", daemon=True).start()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.host not in LOOPBACK:
        raise SystemExit("refusing to listen on a non-loopback address")

    token = args.token or os.environ.get("XPSB_TOKEN")
    generated = token is None
    token = token or secrets.token_urlsafe(32)

    paths = resolve_paths(args.data_dir)
    _setup_logging(paths, args.log_level)
    settings = SettingsStore(paths.settings_file)
    secret_store = KeyringSecretStore()
    extra = {}
    if os.environ.get("XPSB_FAKE_AI"):
        # Development/browser tests only: a simulated Claude from a test file, so the
        # UI can be exercised without an API key or costs.
        import importlib.util

        spec = importlib.util.spec_from_file_location("fake_ai", os.environ["XPSB_FAKE_AI"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extra["ai_transport_factory"] = module.FakeAi().factory
        # A test key must never end up in (or overwrite) the real credential store.
        secret_store = MemorySecretStore()
        logging.getLogger("xpsb").warning("simulated AI active (XPSB_FAKE_AI)")
    jobs = JobManager(paths, settings, secret_store, args.resources_dir, **extra)
    ctx = AppContext(
        token=token,
        paths=paths,
        settings=settings,
        secrets=secret_store,
        jobs=jobs,
        resources_dir=args.resources_dir,
    )
    app = create_app(ctx, dev_cors=args.dev_cors)

    sock = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET)
    if sys.platform != "win32":  # on Windows SO_REUSEADDR would allow port hijacking
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(128)  # accept-queue is live before we announce readiness
    port = sock.getsockname()[1]

    config = uvicorn.Config(
        app,
        log_level=args.log_level,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=3,  # never hang on open connections when the app quits
    )
    server = uvicorn.Server(config)
    if args.watch_stdin:
        _watch_stdin(server)

    ready = {"port": port, "version": __version__}
    if generated:
        ready["token"] = token  # only when nobody gave us one (dev convenience)
    print("XPSB_READY " + json.dumps(ready), flush=True)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
