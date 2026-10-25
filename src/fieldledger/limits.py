"""Explicit, documented resource limits for the ingestion layer."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConfigError


@dataclass(frozen=True)
class IngestionLimits:
    """Upper bounds applied by `profile_csv` and `profile_sqlite`.

    Defaults are deliberately conservative for a hackathon-scale, offline tool.

    max_file_bytes:          largest CSV accepted (default 50 MiB). Enforced by a stat() check
                             and again while reading.
    max_rows:                most CSV data rows accepted, header excluded (default 100,000).
                             Not applied to SQLite, where row counts come from COUNT(*) and cost
                             is bounded by `max_db_bytes` and `sqlite_timeout_seconds`.
    max_columns:             most columns per CSV or per SQLite table (default 1,000).
    max_line_chars:          longest physical CSV line, terminator included (default 262,144).
    max_db_bytes:            largest SQLite file accepted (default 256 MiB).
    max_tables:              most user tables profiled in one database (default 1,000).
    sqlite_timeout_seconds:  wall-clock budget for the whole SQLite profiling call; also caps
                             the lock-wait time at 5 s (default 30.0).
    """

    max_file_bytes: int = 50 * 1024 * 1024
    max_rows: int = 100_000
    max_columns: int = 1_000
    max_line_chars: int = 262_144
    max_db_bytes: int = 256 * 1024 * 1024
    max_tables: int = 1_000
    sqlite_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_rows",
            "max_columns",
            "max_line_chars",
            "max_db_bytes",
            "max_tables",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(f"{name} must be a positive integer")
        t = self.sqlite_timeout_seconds
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not t > 0:
            raise ConfigError("sqlite_timeout_seconds must be a positive number")


DEFAULT_LIMITS = IngestionLimits()
