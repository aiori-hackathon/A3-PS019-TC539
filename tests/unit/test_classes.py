from pathlib import Path

import pytest

from fieldledger.classes import MAX_CONFIG_BYTES, load_class_registry
from fieldledger.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID = """\
taxonomy_status: provisional
required_class_count: 2
classes:
  - {id: ALPHA_1, label: Alpha}
  - {id: BETA_2, label: Beta, description: second}
"""


def write(tmp_path, text, name="classes.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_valid_config_loads(tmp_path):
    reg = load_class_registry(write(tmp_path, VALID))
    assert reg.ids() == ("ALPHA_1", "BETA_2")
    assert reg.is_provisional is True
    assert reg.classes[1].description == "second"


def test_confirmed_status_is_not_provisional(tmp_path):
    reg = load_class_registry(write(tmp_path, VALID.replace("provisional", "confirmed")))
    assert reg.is_provisional is False


def test_bundled_provisional_config_loads_five_labelled_classes():
    reg = load_class_registry(REPO_ROOT / "config" / "classes.provisional.yaml")
    assert reg.is_provisional is True
    assert len(reg.classes) == 5
    assert all(c.id.startswith("TEST_CLASS_") for c in reg.classes)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_class_registry(tmp_path / "nope.yaml")


def test_malformed_yaml(tmp_path):
    with pytest.raises(ConfigError, match="invalid or unsafe YAML"):
        load_class_registry(write(tmp_path, "classes: [unclosed"))


def test_unsafe_python_tag_is_rejected(tmp_path):
    text = 'taxonomy_status: !!python/object/apply:os.getcwd []\nclasses: []\n'
    with pytest.raises(ConfigError, match="unsafe YAML"):
        load_class_registry(write(tmp_path, text))


def test_top_level_must_be_mapping(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_class_registry(write(tmp_path, "- a\n- b\n"))


def test_empty_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_class_registry(write(tmp_path, ""))


def test_oversized_file_rejected(tmp_path):
    big = "# " + "x" * (MAX_CONFIG_BYTES + 10)
    with pytest.raises(ConfigError, match="too large"):
        load_class_registry(write(tmp_path, big))


@pytest.mark.parametrize(
    "text, match",
    [
        (VALID.replace("provisional", "draft"), "taxonomy_status"),
        (VALID.replace("taxonomy_status: provisional\n", ""), "taxonomy_status"),
        ("taxonomy_status: provisional\nclasses: []\n", "non-empty"),
        (VALID + "extra_key: 1\n", "unknown top-level"),
        (VALID.replace("{id: BETA_2, label: Beta, description: second}",
                       "{id: beta_2, label: Beta}").replace("ALPHA_1", "BETA_2"),
         "duplicate"),
        (VALID.replace("ALPHA_1", "1bad id"), "id"),
        (VALID.replace("label: Alpha", "label: ''"), "label"),
        (VALID.replace("label: Alpha", "label: Alpha, colour: red"), "unknown keys"),
        (VALID.replace("required_class_count: 2", "required_class_count: 5"), "expected 5"),
        (VALID.replace("required_class_count: 2", "required_class_count: true"), "positive integer"),
    ],
)
def test_invalid_configs_are_rejected(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load_class_registry(write(tmp_path, text))
