"""Class-registry loader. Holds NO class definitions: classes come from YAML only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ConfigError

MAX_CONFIG_BYTES = 1_000_000
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,63}$")
_STATUSES = ("provisional", "confirmed")
_TOP_KEYS = {"taxonomy_status", "required_class_count", "classes"}
_CLASS_KEYS = {"id", "label", "description"}


@dataclass(frozen=True)
class ClassDef:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class ClassRegistry:
    taxonomy_status: str
    classes: tuple[ClassDef, ...]

    @property
    def is_provisional(self) -> bool:
        return self.taxonomy_status != "confirmed"

    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.classes)


def load_yaml_mapping(path: str | Path) -> dict:
    """Read a YAML file with the safe loader; the top level must be a mapping."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p.name}")
    if p.stat().st_size > MAX_CONFIG_BYTES:
        raise ConfigError(f"config file too large: {p.name}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid or unsafe YAML in {p.name}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {p.name} as UTF-8 text") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"top level of {p.name} must be a mapping")
    return data


def load_class_registry(path: str | Path) -> ClassRegistry:
    data = load_yaml_mapping(path)

    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level keys: {sorted(map(str, unknown))}")

    status = data.get("taxonomy_status")
    if status not in _STATUSES:
        raise ConfigError(f"taxonomy_status must be one of {list(_STATUSES)}")

    raw = data.get("classes")
    if not isinstance(raw, list) or not raw:
        raise ConfigError("classes must be a non-empty list")

    classes: list[ClassDef] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"classes[{i}] must be a mapping")
        extra = set(item) - _CLASS_KEYS
        if extra:
            raise ConfigError(f"classes[{i}] has unknown keys: {sorted(map(str, extra))}")
        cid, label = item.get("id"), item.get("label")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            raise ConfigError(f"classes[{i}].id is missing or invalid")
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"classes[{i}].label must be a non-empty string")
        desc = item.get("description", "")
        if not isinstance(desc, str):
            raise ConfigError(f"classes[{i}].description must be a string")
        if cid.lower() in seen:
            raise ConfigError(f"duplicate class id: {cid}")
        seen.add(cid.lower())
        classes.append(ClassDef(id=cid, label=label.strip(), description=desc.strip()))

    if "required_class_count" in data:
        n = data["required_class_count"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ConfigError("required_class_count must be a positive integer")
        if n != len(classes):
            raise ConfigError(f"expected {n} classes but found {len(classes)}")

    return ClassRegistry(taxonomy_status=status, classes=tuple(classes))
