
"""Safely discover supported FieldLedger input files."""

from __future__ import annotations

from pathlib import Path

from .errors import InputAccessError, PathAccessError
from .safety import resolve_within

SUPPORTED_SUFFIXES = frozenset(
    {".csv", ".db", ".sqlite", ".sqlite3", ".db3"}
)


def discover_sources(
    allowed_base: str | Path,
    *,
    recursive: bool = True,
) -> tuple[Path, ...]:
    """Return supported regular files under allowed_base, sorted by relative path.

    Directory symlinks are not traversed. File symlinks are accepted only when
    their resolved targets remain inside the allowed base.
    """
    try:
        base = Path(allowed_base).resolve(strict=True)
        if not base.is_dir():
            raise InputAccessError("input base is not a directory")
    except OSError:
        raise InputAccessError("input base is not accessible") from None

    found: list[Path] = []
    pending = [base]

    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            raise InputAccessError("a directory could not be accessed") from None

        child_dirs: list[Path] = []

        for entry in entries:
            try:
                # Resolve symlinks and reject anything escaping the base.
                resolved = resolve_within(base, entry)
                if entry.is_symlink() and resolved.is_dir():
                    continue

                if resolved.is_dir():
                    if recursive and not entry.is_symlink():
                        child_dirs.append(resolved)
                    continue

                if not resolved.is_file():
                    continue

                if entry.suffix.lower() in SUPPORTED_SUFFIXES:
                    found.append(entry)

            except PathAccessError:
                # Unsafe entries are not returned or traversed.
                continue
            except OSError:
                raise InputAccessError("a source could not be inspected") from None

        pending.extend(reversed(child_dirs))

    return tuple(
        sorted(
            found,
            key=lambda p: p.relative_to(base).as_posix().casefold(),
        )
    )
