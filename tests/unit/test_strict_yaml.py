import pytest

from fieldledger.classes import MAX_CONFIG_BYTES
from fieldledger.errors import ConfigError
from fieldledger.strict_yaml import load_strict_yaml_mapping


def write(tmp_path, content, name="c.yaml"):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return p


def test_plain_mapping_loads_with_native_types(tmp_path):
    data = load_strict_yaml_mapping(write(tmp_path, "a: 1\nb: [x, 2, true]\nc: {d: e}\n"))
    assert data == {"a": 1, "b": ["x", 2, True], "c": {"d": "e"}}


def test_same_key_at_different_levels_is_fine(tmp_path):
    assert load_strict_yaml_mapping(write(tmp_path, "a: {x: 1}\nb: {x: 2}\n")) == {
        "a": {"x": 1},
        "b": {"x": 2},
    }


def test_utf8_bom_is_accepted(tmp_path):
    assert load_strict_yaml_mapping(write(tmp_path, b"\xef\xbb\xbfa: 1\n")) == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2\n",
        "a:\n  b: 1\n  b: 2\n",
        "l:\n  - {k: 1, k: 2}\n",
        'a: 1\n"a": 2\n',  # differently quoted, same key
    ],
)
def test_duplicate_keys_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError, match="duplicate mapping key near line"):
        load_strict_yaml_mapping(write(tmp_path, text))


def test_error_messages_do_not_echo_keys_or_paths(tmp_path):
    p = write(tmp_path, "SENTINELKEY: 1\nSENTINELKEY: 2\n")
    with pytest.raises(ConfigError) as ei:
        load_strict_yaml_mapping(p)
    assert "SENTINELKEY" not in str(ei.value)
    assert str(tmp_path) not in str(ei.value)
    assert "c.yaml" in str(ei.value)


@pytest.mark.parametrize(
    "text",
    [
        "a: &x [1]\nb: *x\n",
        "base: &b {x: 1}\nd:\n  <<: *b\n",
    ],
)
def test_aliases_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError, match="aliases are not allowed"):
        load_strict_yaml_mapping(write(tmp_path, text))


def test_billion_laughs_style_document_is_rejected_quickly(tmp_path):
    text = (
        "a: &a [x, x, x, x, x, x, x, x, x]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "d: [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
    )
    with pytest.raises(ConfigError, match="aliases are not allowed"):
        load_strict_yaml_mapping(write(tmp_path, text))


def test_merge_keys_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="merge keys are not allowed"):
        load_strict_yaml_mapping(write(tmp_path, "d:\n  <<: {x: 2}\n"))


@pytest.mark.parametrize("text", ["1: a\n", "true: a\n", "null: a\n", "? [a, b]\n: c\n"])
def test_non_string_keys_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError, match="plain strings"):
        load_strict_yaml_mapping(write(tmp_path, text))


def test_unsafe_python_tag_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="invalid or unsafe YAML"):
        load_strict_yaml_mapping(write(tmp_path, "x: !!python/object/apply:os.getcwd []\n"))


def test_malformed_yaml_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="invalid or unsafe YAML"):
        load_strict_yaml_mapping(write(tmp_path, "classes: [unclosed"))


def test_multiple_documents_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="invalid or unsafe YAML"):
        load_strict_yaml_mapping(write(tmp_path, "a: 1\n---\nb: 2\n"))


def test_deeply_nested_document_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="nesting is too deep"):
        load_strict_yaml_mapping(write(tmp_path, "[" * 3000 + "]" * 3000))


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string\n", ""])
def test_top_level_must_be_a_mapping(tmp_path, text):
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_strict_yaml_mapping(write(tmp_path, text))


def test_missing_file_and_directory(tmp_path):
    with pytest.raises(ConfigError, match="not found") as ei:
        load_strict_yaml_mapping(tmp_path / "nope.yaml")
    assert str(tmp_path) not in str(ei.value)
    with pytest.raises(ConfigError, match="not found"):
        load_strict_yaml_mapping(tmp_path)


def test_oversized_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="too large"):
        load_strict_yaml_mapping(write(tmp_path, b"# " + b"x" * (MAX_CONFIG_BYTES + 10)))


def test_file_exactly_at_the_cap_is_accepted(tmp_path):
    body = b"a: 1\n"
    padding = b"#" * (MAX_CONFIG_BYTES - len(body) - 1) + b"\n"
    assert load_strict_yaml_mapping(write(tmp_path, body + padding)) == {"a": 1}


def test_non_utf8_bytes_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="UTF-8"):
        load_strict_yaml_mapping(write(tmp_path, b"a: \xff\xfe\n"))
