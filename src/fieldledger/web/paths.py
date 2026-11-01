"""Validation of upload names and relative paths (docs/api-contract.md, section 5).

A stricter superset of ``fieldledger.findings.validate_source_key``: the names must be safe on
Windows as well as on POSIX systems, because the demo target is Windows 10/11.
"""

from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath

from ..discovery import SUPPORTED_SUFFIXES
from ..findings import MAX_SOURCE_KEY_CHARS, validate_source_key
from .apierrors import ApiError

MAX_SEGMENT_CHARS = 255
_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _bad(message: str = "unsafe or unusable file name") -> ApiError:
    return ApiError("invalid_name", message)


def validate_relative_path(value: object) -> str:
    """Return `value` unchanged if it is a safe relative upload path; else raise ApiError."""
    if not isinstance(value, str) or not value:
        raise _bad("file name must be a non-empty string")
    if len(value) > MAX_SOURCE_KEY_CHARS:
        raise _bad("file path is too long")
    if "\\" in value:
        raise _bad("use forward slashes in file paths")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":" and value[0].isalpha()):
        raise _bad("file path must be relative")
    for ch in value:
        if unicodedata.category(ch) in ("Cc", "Cs") or ch in _FORBIDDEN_CHARS:
            raise _bad("file path contains a character that is not allowed")
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise _bad("file path contains an empty or relative segment")
        if len(segment) > MAX_SEGMENT_CHARS:
            raise _bad("a path segment is too long")
        if segment.endswith((".", " ")):
            raise _bad("a path segment may not end with a dot or a space")
        if segment.split(".", 1)[0].strip().casefold() in _RESERVED:
            raise _bad("a path segment uses a reserved device name")
    try:
        validate_source_key(value)
    except ValueError:
        raise _bad() from None
    suffix = PurePosixPath(value).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ApiError("unsupported_type", "unsupported file type")
    return value
