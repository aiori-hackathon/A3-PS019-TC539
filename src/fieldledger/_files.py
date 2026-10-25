"""Small shared file helper for the ingestion modules."""

from __future__ import annotations

import stat
from pathlib import Path

from .errors import InputAccessError


def regular_file_size(path: Path) -> int:
    """Return the size of a regular file; raise InputAccessError otherwise.

    Messages deliberately omit the path.
    """
    try:
        st = path.stat()
    except OSError:
        raise InputAccessError("input file not found or not accessible") from None
    if not stat.S_ISREG(st.st_mode):
        raise InputAccessError("input is not a regular file")
    return st.st_size
