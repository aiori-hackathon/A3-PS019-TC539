"""Command-line entry point: ``python -m fieldledger.web``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..errors import ConfigError
from . import contract
from .security import LOOPBACK_NAMES

DEFAULT_PORT = 8765


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"error: {name} must be a positive integer") from None


def build_parser() -> argparse.ArgumentParser:
    d = contract.WorkspaceLimits()
    p = argparse.ArgumentParser(prog="python -m fieldledger.web",
                                description="Run the FieldLedger local web UI and API.")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1, localhost only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--allow-network", action="store_true",
                   help="required to bind a non-loopback address (there is no authentication)")
    p.add_argument("--root", default=".", help="repository root containing config/ (default: current directory)")
    p.add_argument("--max-files", type=int, default=_env_int("FIELDLEDGER_MAX_FILES", d.max_files))
    p.add_argument("--max-workspace-mb", type=int,
                   default=_env_int("FIELDLEDGER_MAX_WORKSPACE_MB", d.max_workspace_bytes // (1024 * 1024)))
    p.add_argument("--idle-timeout-seconds", type=int,
                   default=_env_int("FIELDLEDGER_IDLE_TIMEOUT_SECONDS", d.idle_timeout_seconds))
    p.add_argument("--scan-timeout-seconds", type=int,
                   default=_env_int("FIELDLEDGER_SCAN_TIMEOUT_SECONDS", d.scan_timeout_seconds))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = args.host.strip().strip("[]")
    if host.lower() not in LOOPBACK_NAMES and not args.allow_network:
        print("error: refusing to bind a non-loopback address without --allow-network. "
              "FieldLedger has no authentication.", file=sys.stderr)
        return 2
    if host.lower() not in LOOPBACK_NAMES:
        print("WARNING: listening on a non-loopback address. Anyone who can reach this port can "
              "upload data and read findings. There is no authentication.", file=sys.stderr)
    try:
        import uvicorn
        from .app import AppConfig, create_app
    except ImportError:
        print("error: the web dependencies are not installed. Run: pip install -e \".[web]\"",
              file=sys.stderr)
        return 1
    try:
        limits = contract.WorkspaceLimits(
            max_files=args.max_files,
            max_workspace_bytes=args.max_workspace_mb * 1024 * 1024,
            idle_timeout_seconds=args.idle_timeout_seconds,
            scan_timeout_seconds=args.scan_timeout_seconds,
        )
        app = create_app(AppConfig(root=Path(args.root), host=host, port=args.port, limits=limits))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    shown = f"[{host}]" if ":" in host else host
    print(f"FieldLedger is starting at http://{shown}:{args.port}  (Ctrl+C to stop)")
    # access_log is off on purpose: request lines can contain file names and search text.
    uvicorn.run(app, host=host, port=args.port, access_log=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
