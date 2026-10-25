import pytest

from fieldledger.errors import PathAccessError
from fieldledger.safety import resolve_within


def test_path_inside_base_is_allowed(tmp_path):
    (tmp_path / "sub").mkdir()
    assert resolve_within(tmp_path, "sub/data.csv") == (tmp_path / "sub" / "data.csv").resolve()


def test_parent_traversal_is_rejected(tmp_path):
    with pytest.raises(PathAccessError):
        resolve_within(tmp_path, "../outside.csv")


def test_nested_traversal_is_rejected(tmp_path):
    with pytest.raises(PathAccessError):
        resolve_within(tmp_path, "sub/../../outside.csv")


def test_absolute_path_outside_base_is_rejected(tmp_path, tmp_path_factory):
    other = tmp_path_factory.mktemp("other") / "x.csv"
    with pytest.raises(PathAccessError):
        resolve_within(tmp_path, other)
