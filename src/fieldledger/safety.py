"""Path guard: keep file access inside an intended input directory."""

from __future__ import annotations

from pathlib import Path

from .errors import PathAccessError


def resolve_within(base: str | Path, candidate: str | Path) -> Path:
    """Resolve `candidate` against `base`; refuse anything that escapes `base`."""
    base_resolved = Path(base).resolve()
    target = (base_resolved / candidate).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise PathAccessError("path escapes the permitted directory") from None
    return target
