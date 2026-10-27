"""Strict YAML mapping loader for FieldLedger rule and label files.

`classes.load_yaml_mapping` is intentionally left unchanged. This loader is used by the
Milestone 4 configuration files (rules and labels) and is stricter:

- Uses a `yaml.SafeLoader` subclass: no arbitrary Python object construction.
- Duplicate mapping keys are rejected (PyYAML would silently keep the last one).
- YAML aliases are rejected. Without aliases a small file cannot expand into a huge object
  graph ("billion laughs"), so the byte cap is also a meaningful size bound.
- YAML merge keys (``<<``) are rejected, because they hide key collisions.
- Mapping keys must be plain strings (``1:``, ``true:``, ``null:`` and composite keys are
  rejected, which also avoids YAML's implicit-typing surprises for keys).
- The file is read with a hard byte cap even if it grows after the size check.
- Nesting that exhausts the Python recursion limit is reported as a ConfigError.

Error messages carry the file NAME (never a directory path), a fixed description and a
line number. They never echo keys, values or snippets from the file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .classes import MAX_CONFIG_BYTES
from .errors import ConfigError

_MERGE_TAG = "tag:yaml.org,2002:merge"
_STR_TAG = "tag:yaml.org,2002:str"


class _Rejected(yaml.MarkedYAMLError):
    """A construct that FieldLedger refuses (alias, duplicate key, merge key, bad key)."""


class _StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise _Rejected(problem="YAML aliases are not allowed", problem_mark=event.start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, _value_node in node.value:
                if key_node.tag == _MERGE_TAG:
                    raise _Rejected(
                        problem="YAML merge keys are not allowed",
                        problem_mark=key_node.start_mark,
                    )
                if not isinstance(key_node, yaml.ScalarNode) or key_node.tag != _STR_TAG:
                    raise _Rejected(
                        problem="mapping keys must be plain strings",
                        problem_mark=key_node.start_mark,
                    )
                if key_node.value in seen:
                    raise _Rejected(
                        problem="duplicate mapping key", problem_mark=key_node.start_mark
                    )
                seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def _describe(exc: yaml.YAMLError, name: str) -> str:
    mark = getattr(exc, "problem_mark", None)
    where = f" near line {mark.line + 1}" if mark is not None else ""
    if isinstance(exc, _Rejected):
        return f"{name}: {exc.problem}{where}"
    return f"invalid or unsafe YAML in {name}{where}"


def load_strict_yaml_mapping(path: str | Path) -> dict:
    """Load one YAML file whose top level is a mapping, applying the rules above."""
    p = Path(path)
    name = p.name
    try:
        is_file = p.is_file()
    except OSError:
        is_file = False
    if not is_file:
        raise ConfigError(f"config file not found: {name}")

    try:
        with open(p, "rb") as fh:
            raw = fh.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        raise ConfigError(f"cannot read {name}") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError(f"config file too large: {name}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigError(f"cannot read {name} as UTF-8 text") from None

    # Build the loader directly and read exactly one document from it. This is the same
    # sequence the PyYAML convenience functions run internally, with the same SafeLoader
    # base class, so only safe constructors are available. The constructor is inside the
    # try block because it already scans the text (non-printable characters raise there).
    loader = None
    try:
        loader = _StrictLoader(text)
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise ConfigError(_describe(exc, name)) from None
    except RecursionError:
        raise ConfigError(f"YAML nesting is too deep in {name}") from None
    finally:
        if loader is not None:
            loader.dispose()

    if not isinstance(data, dict):
        raise ConfigError(f"top level of {name} must be a mapping")
    return data
