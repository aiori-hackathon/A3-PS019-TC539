"""Guard: no dynamic code execution and no unsafe YAML or pickle use in src/."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = re.compile(
    r"\beval\s*\(|\bexec\s*\(|yaml\.load\s*\(|yaml\.unsafe_load|\bpickle\b|os\.system\s*\("
)


def test_src_contains_no_forbidden_constructs():
    offenders = []
    for py in SRC.rglob("*.py"):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if FORBIDDEN.search(code):
                offenders.append(f"{py.relative_to(SRC)}:{n}")
    assert offenders == []
