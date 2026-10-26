
"""Unified entry point for profiling supported FieldLedger sources."""

from __future__ import annotations

from pathlib import Path

from .errors import UnsupportedFileTypeError
from .ingest_csv import profile_csv
from .ingest_sqlite import SQLITE_SUFFIXES, profile_sqlite
from .limits import IngestionLimits
from .profiles import SourceProfile


def profile_source(
    path: str | Path,
    allowed_base: str | Path,
    limits: IngestionLimits | None = None,
) -> SourceProfile:
    """Profile one supported source inside the approved base directory."""
    suffix = Path(path).suffix.lower()

    if suffix == ".csv":
        return profile_csv(path, allowed_base, limits)

    if suffix in SQLITE_SUFFIXES:
        return profile_sqlite(path, allowed_base, limits)

    raise UnsupportedFileTypeError("unsupported source file extension")
