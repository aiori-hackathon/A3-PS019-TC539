"""Machine-readable constants of the FieldLedger web API contract (v1).

This is the single place where names shared by the API implementation, the frontend types and
the tests are defined. docs/api-contract.md is the human-readable contract. The tests in
tests/web/test_contract.py fail if the document, these constants and the real backend disagree.

Standard library only (plus the package's own ConfigError): importing this module never needs
the optional web dependencies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..errors import (
    ConfigError,
    DatabaseTimeoutError,
    FieldLedgerError,
    InputAccessError,
    InputTooLargeError,
    InvalidDatabaseError,
    MalformedCsvError,
    PathAccessError,
    UnsupportedFileTypeError,
)

API_VERSION = 1

# The five official class IDs (config/classes.yaml). Never renamed.
CLASS_IDS = (
    "PERSONAL_IDENTITY",
    "CONTACT_INFORMATION",
    "GOVERNMENT_IDENTIFIERS",
    "FINANCIAL_INFORMATION",
    "LOCATION_ADDRESS",
)

SUPPORTED_SUFFIXES = (".csv", ".db", ".db3", ".sqlite", ".sqlite3")
REPORT_FORMATS = ("json", "csv", "md")
METADATA_ORIGINS = ("team_authored_synthetic", "organizer_provided", "user_provided")

# Mirrors metadata.DeclarationStatus (checked by a test against the real enum).
METADATA_STATUSES = (
    "complete",
    "missing_purpose",
    "missing_retention",
    "missing_both",
    "no_entry",
)

REVIEW_STATUSES = ("unreviewed", "confirmed", "not_personal_data", "needs_follow_up")
SCAN_STATES = ("queued", "running", "succeeded", "failed", "cancelled")
SCAN_STAGES = (
    "discovering",
    "profiling",
    "detecting",
    "linking_metadata",
    "writing_reports",
)
FINDING_SORT_KEYS = ("source", "table", "column", "class", "position")
EXPLANATION_KINDS = ("matched_pattern", "rule_patterns_only")
SOURCE_WARNING_CODES = ("possible_delimiter_mismatch",)

# Per-source failure codes (one per backend IngestionError subclass, plus a catch-all).
SOURCE_ERROR_CODES = (
    "unsupported_type",
    "input_access",
    "input_too_large",
    "malformed_csv",
    "invalid_database",
    "database_timeout",
    "path_access",
    "ingest_failed",
)

# Backend exception class -> per-source error code. Any other FieldLedgerError (including the
# IngestionError base) maps to "ingest_failed". A test requires every FieldLedgerError subclass
# defined in errors.py to be listed here or to be deliberately excluded there.
SOURCE_ERROR_CODE_BY_EXCEPTION = {
    UnsupportedFileTypeError: "unsupported_type",
    InputAccessError: "input_access",
    InputTooLargeError: "input_too_large",
    MalformedCsvError: "malformed_csv",
    InvalidDatabaseError: "invalid_database",
    DatabaseTimeoutError: "database_timeout",
    PathAccessError: "path_access",
}


def source_error_code(exc: BaseException) -> str:
    """Stable per-source error code for a backend exception (never derived from its message)."""
    for cls in type(exc).__mro__:
        code = SOURCE_ERROR_CODE_BY_EXCEPTION.get(cls)
        if code is not None:
            return code
    return "ingest_failed"


# Request-level error codes and their HTTP status.
ERROR_HTTP_STATUS = {
    "bad_request": 400,
    "forbidden": 403,
    "unsupported_type": 415,
    "invalid_name": 400,
    "duplicate_name": 409,
    "file_too_large": 413,
    "workspace_too_large": 413,
    "too_many_files": 413,
    "malformed_input": 422,
    "invalid_metadata": 422,
    "no_workspace": 404,
    "not_found": 404,
    "scan_in_progress": 409,
    "scan_not_ready": 409,
    "format_not_generated": 409,
    "benchmark_unavailable": 503,
    "internal_error": 500,
}

ENDPOINTS = (
    ("GET", "/api/health"),
    ("GET", "/api/config"),
    ("GET", "/api/workspace"),
    ("POST", "/api/workspace/files"),
    ("DELETE", "/api/workspace/files"),
    ("POST", "/api/workspace/metadata"),
    ("DELETE", "/api/workspace/metadata"),
    ("POST", "/api/workspace/bundled"),
    ("DELETE", "/api/workspace"),
    ("POST", "/api/scans"),
    ("GET", "/api/scans/{scan_id}"),
    ("POST", "/api/scans/{scan_id}/cancel"),
    ("GET", "/api/scans/{scan_id}/summary"),
    ("GET", "/api/scans/{scan_id}/sources"),
    ("GET", "/api/scans/{scan_id}/findings"),
    ("GET", "/api/scans/{scan_id}/findings/{finding_id}"),
    ("PUT", "/api/scans/{scan_id}/findings/{finding_id}/review"),
    ("GET", "/api/scans/{scan_id}/metadata"),
    ("GET", "/api/scans/{scan_id}/export/{format}"),
    ("GET", "/api/workspace/reviews/export"),
    ("DELETE", "/api/workspace/reviews"),
    ("POST", "/api/evaluations"),
    ("GET", "/api/evaluations/{evaluation_id}"),
)

# The idle timeout may be lowered by configuration but never raised above this.
IDLE_TIMEOUT_CEILING_SECONDS = 3600


@dataclass(frozen=True)
class WorkspaceLimits:
    """Web-layer limits. Defaults are documented in docs/api-contract.md and configurable.

    Ingestion limits (rows, columns, per-file bytes, SQLite budget) stay with the backend's
    IngestionLimits and are not duplicated here.
    """

    max_files: int = 100
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_files_per_request: int = 50
    max_metadata_bytes: int = 1_000_000
    idle_timeout_seconds: int = IDLE_TIMEOUT_CEILING_SECONDS
    scan_timeout_seconds: int = 900

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_workspace_bytes",
            "max_files_per_request",
            "max_metadata_bytes",
            "idle_timeout_seconds",
            "scan_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(f"{name} must be a positive integer")
        if self.idle_timeout_seconds > IDLE_TIMEOUT_CEILING_SECONDS:
            raise ConfigError("idle_timeout_seconds cannot exceed 3600")


def finding_id(source_key: str, table: str, column: str, class_id: str) -> str:
    """Stable 16-hex-character ID of a finding (see docs/api-contract.md, section 2).

    SHA-256 over the four identity fields joined by NUL, UTF-8 encoded. Deterministic across
    scans and processes, so it is a safe key for local review annotations.
    """
    parts = (source_key, table, column, class_id)
    for part in parts:
        if not isinstance(part, str) or not part or "\x00" in part:
            raise ValueError("finding identity parts must be non-empty strings without NUL")
    payload = "\x00".join(parts).encode("utf-8", "surrogatepass")
    return hashlib.sha256(payload).hexdigest()[:16]
