"""CSV formula-injection mitigation for the web export (docs/api-contract.md, section 12).

Post-processes the CSV that the real ``write_reports`` produced. If no cell needs changing the
input is returned unchanged (byte-identical to the CLI output).
"""

from __future__ import annotations

import csv
import io
import unicodedata

_TRIGGERS = frozenset("=+-@\uff1d\uff0b\uff0d\uff20")  # = + - @ and full-width forms


def needs_neutralising(cell: str) -> bool:
    if not cell:
        return False
    if unicodedata.category(cell[0]) == "Cc":
        return True
    for ch in cell:
        if ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        return ch in _TRIGGERS
    return False


def neutralise_cell(cell: str) -> str:
    return "'" + cell if needs_neutralising(cell) else cell


def neutralise_csv_text(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text, newline="")))
    changed = False
    out_rows = []
    for row in rows:
        new_row = []
        for cell in row:
            fixed = neutralise_cell(cell)
            changed = changed or fixed is not cell
            new_row.append(fixed)
        out_rows.append(new_row)
    if not changed:
        return text
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out_rows)
    return buf.getvalue()
