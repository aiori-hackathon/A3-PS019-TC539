"""Temporary, per-process workspace for uploaded files (docs/api-contract.md, sections 5 and 10).

Nothing is written to the repository or to the browser. The workspace is a randomly named
directory under the system temporary directory, owner-only where the OS supports it. It is
removed on idle expiry, on explicit clearing and on shutdown. Deletion is a normal file removal,
not secure erasure.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..discovery import SUPPORTED_SUFFIXES
from ..limits import DEFAULT_LIMITS, IngestionLimits
from ..safety import resolve_within
from ..errors import PathAccessError
from .apierrors import ApiError
from .contract import WorkspaceLimits

logger = logging.getLogger("fieldledger.web")

WORKSPACE_PREFIX = "fieldledger-ws-"
SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SUFFIXES = (".db", ".db3", ".sqlite", ".sqlite3")


def utc_iso(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_type_for(key: str) -> str:
    return "sqlite" if key.lower().endswith(_SQLITE_SUFFIXES) else "csv"


def rmtree_robust(path: Path, attempts: int = 6) -> bool:
    """Remove a directory tree, retrying for transient Windows file locks. True on success."""

    def _onerror(func, p, _exc):  # read-only files on Windows
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass

    for i in range(attempts):
        shutil.rmtree(path, onerror=_onerror)
        if not path.exists():
            return True
        time.sleep(0.1 * (i + 1))
    return not path.exists()


def tree_bytes(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


@dataclass
class FileInfo:
    size_bytes: int
    type: str


@dataclass
class MetadataInfo:
    entries: int
    origin: str
    size_bytes: int


class Workspace:
    """One workspace. All mutating methods must be called while holding `lock`."""

    def __init__(self, base: Path | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=base)).resolve()
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.data_dir = self.root / "data"
        self.meta_dir = self.root / "metadata"
        self.reports_dir = self.root / "reports"
        self.incoming_dir = self.root / "incoming"
        for d in (self.data_dir, self.meta_dir, self.reports_dir, self.incoming_dir):
            d.mkdir()
        self.lock = threading.RLock()
        self.revision = 0
        self.files: dict[str, FileInfo] = {}
        self.metadata: MetadataInfo | None = None
        self.reviews: dict[str, dict] = {}
        self.created_at = utc_iso()

    # ------------------------------------------------------------------ names
    @property
    def metadata_path(self) -> Path:
        return self.meta_dir / "metadata.yaml"

    @property
    def reviews_path(self) -> Path:
        return self.root / "reviews.json"

    def new_incoming_path(self, suffix: str = ".part") -> Path:
        return self.incoming_dir / f"{uuid.uuid4().hex}{suffix}"

    def bytes_used(self) -> int:
        return tree_bytes(self.root)

    # ------------------------------------------------------------------ files
    def check_can_add(self, key: str, limits: WorkspaceLimits) -> None:
        folded = key.casefold()
        existing = {k.casefold() for k in self.files}
        if folded in existing:
            raise ApiError("duplicate_name", "a file with this name already exists")
        parts = folded.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in existing:
                raise ApiError("invalid_name", "a parent path is already used by a file")
        prefix = folded + "/"
        if any(k.startswith(prefix) for k in existing):
            raise ApiError("invalid_name", "this name is already used as a folder")
        if len(self.files) >= limits.max_files:
            raise ApiError("too_many_files", "the workspace file limit has been reached")

    def per_file_limit(self, key: str, ingestion: IngestionLimits) -> int:
        return ingestion.max_db_bytes if source_type_for(key) == "sqlite" else ingestion.max_file_bytes

    def commit_file(self, key: str, tmp: Path, size: int, limits: WorkspaceLimits) -> None:
        """Validate the finished upload and move it into the data directory."""
        self.check_can_add(key, limits)
        if size == 0:
            raise ApiError("malformed_input", "the file is empty")
        stype = source_type_for(key)
        with open(tmp, "rb") as fh:
            head = fh.read(4096)
        if stype == "sqlite" and not head.startswith(SQLITE_MAGIC):
            raise ApiError("malformed_input", "the file is not a SQLite database")
        if stype == "csv" and b"\x00" in head:
            raise ApiError("malformed_input", "the file contains binary data and is not a CSV")
        try:
            target = resolve_within(self.data_dir, key)
        except PathAccessError:
            raise ApiError("invalid_name", "path escapes the workspace") from None
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ApiError("duplicate_name", "a file with this name already exists")
        os.replace(tmp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self.files[key] = FileInfo(size_bytes=size, type=stype)
        self.revision += 1

    def remove_file(self, key: str) -> bool:
        if key not in self.files:
            return False
        try:
            target = resolve_within(self.data_dir, key)
        except PathAccessError:
            return False
        for suffix in ("", "-wal", "-shm", "-journal"):
            side = Path(str(target) + suffix)
            try:
                side.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        parent = target.parent
        while parent != self.data_dir:  # prune empty folders
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        del self.files[key]
        self.revision += 1
        return True

    # ------------------------------------------------------------------ metadata
    def set_metadata(self, tmp: Path, entries: int, origin: str, size: int) -> None:
        os.replace(tmp, self.metadata_path)
        self.metadata = MetadataInfo(entries=entries, origin=origin, size_bytes=size)
        self.revision += 1

    def clear_metadata(self) -> bool:
        if self.metadata is None:
            return False
        try:
            self.metadata_path.unlink()
        except OSError:
            pass
        self.metadata = None
        self.revision += 1
        return True

    # ------------------------------------------------------------------ reviews
    def save_reviews(self) -> None:
        tmp = self.root / "reviews.json.tmp"
        tmp.write_text(json.dumps(self.reviews, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.reviews_path)

    def public_state(self, idle_seconds: int, expires_at: str) -> dict:
        return {
            "exists": True,
            "revision": self.revision,
            "files": [
                {"source_key": k, "type": v.type, "size_bytes": v.size_bytes}
                for k, v in sorted(self.files.items(), key=lambda kv: kv[0].casefold())
            ],
            "metadata": None
            if self.metadata is None
            else {"entries": self.metadata.entries, "origin": self.metadata.origin},
            "review_count": len(self.reviews),
            "expires_at": expires_at,
            "idle_timeout_seconds": idle_seconds,
        }


class WorkspaceManager:
    """Owns the single workspace of this server process and its idle expiry."""

    def __init__(
        self,
        limits: WorkspaceLimits | None = None,
        ingestion: IngestionLimits | None = None,
        *,
        temp_base: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or WorkspaceLimits()
        self.ingestion = ingestion or DEFAULT_LIMITS
        self.temp_base = temp_base
        self._clock = clock
        self._lock = threading.RLock()
        self._ws: Workspace | None = None
        self._last = clock()
        self._on_discard: list[Callable[[], None]] = []

    def on_discard(self, fn: Callable[[], None]) -> None:
        """Register a callback run when the workspace is removed (drops scan state)."""
        self._on_discard.append(fn)

    # ------------------------------------------------------------------ lifecycle
    def cleanup_stale(self) -> int:
        """Remove leftover directories with our prefix that are older than the idle timeout."""
        base = Path(self.temp_base or tempfile.gettempdir())
        removed = 0
        cutoff = time.time() - self.limits.idle_timeout_seconds
        try:
            entries = list(base.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if not entry.name.startswith(WORKSPACE_PREFIX) or entry.is_symlink():
                continue
            if self._ws is not None and entry.resolve() == self._ws.root:
                continue
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff and rmtree_robust(entry):
                    removed += 1
            except OSError:
                continue
        if removed:
            logger.info("removed %d stale workspace directorie(s)", removed)
        return removed

    def touch(self) -> None:
        with self._lock:
            self._last = self._clock()
            if self._ws is not None:
                try:
                    os.utime(self._ws.root, None)
                except OSError:
                    pass

    def expires_at(self) -> str:
        remaining = self.limits.idle_timeout_seconds - (self._clock() - self._last)
        return utc_iso(datetime.now(timezone.utc) + timedelta(seconds=max(0, remaining)))

    def sweep(self) -> bool:
        """Remove the workspace if it has been idle too long. True if it was removed."""
        with self._lock:
            if self._ws is None:
                return False
            if self._clock() - self._last < self.limits.idle_timeout_seconds:
                return False
        logger.info("workspace expired after idle timeout")
        return self.clear()

    def peek(self) -> Workspace | None:
        self.sweep()
        with self._lock:
            return self._ws

    def get(self) -> Workspace:
        ws = self.peek()
        if ws is None:
            raise ApiError("no_workspace", "there is no active workspace")
        self.touch()
        return ws

    def ensure(self) -> Workspace:
        self.sweep()
        with self._lock:
            if self._ws is None:
                self._ws = Workspace(self.temp_base)
                logger.info("workspace created")
            self.touch()
            return self._ws

    def clear(self) -> bool:
        with self._lock:
            ws, self._ws = self._ws, None
        for fn in self._on_discard:
            try:
                fn()
            except Exception:  # never let cleanup callbacks block removal
                logger.warning("discard callback failed")
        if ws is None:
            return False
        ok = rmtree_robust(ws.root)
        logger.info("workspace removed ok=%s", ok)
        return True

    def state(self) -> dict:
        ws = self.peek()
        if ws is None:
            return {"exists": False}
        self.touch()
        with ws.lock:
            return ws.public_state(self.limits.idle_timeout_seconds, self.expires_at())
