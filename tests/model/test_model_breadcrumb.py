import dataclasses
from typing import Any

import pytest

from mds.model import ATOM_ID_LIMIT, Breadcrumb, ModelError, ReadSetEntry
from mds.model.breadcrumb import MAX_TEXT_CHARS


def make_breadcrumb(**overrides: Any) -> Breadcrumb:
    fields: dict[str, Any] = {
        "breadcrumb_id": 1,
        "atom_ids": (7,),
        "text": "4.2 is amended by the 2023 policy.",
        "author_id": "research-agent",
        "csn": 20,
        "acl_class": 3,
        "read_set": (ReadSetEntry(7, 1), ReadSetEntry(9, 2)),
    }
    fields.update(overrides)
    return Breadcrumb(**fields)


def test_valid_breadcrumb() -> None:
    crumb = make_breadcrumb(atom_ids=[7, 8])
    assert crumb.atom_ids == (7, 8)
    assert crumb.read_set[1] == ReadSetEntry(atom_id=9, version=2)


def test_read_set_defaults_empty() -> None:
    assert make_breadcrumb(read_set=()).read_set == ()


def test_must_pin_to_an_atom() -> None:
    with pytest.raises(ModelError, match="at least one atom"):
        make_breadcrumb(atom_ids=())


def test_pinned_atom_id_range_checked() -> None:
    with pytest.raises(ModelError, match="atom_id must be in"):
        make_breadcrumb(atom_ids=(7, ATOM_ID_LIMIT))


def test_text_required() -> None:
    with pytest.raises(ModelError, match="text must not be empty"):
        make_breadcrumb(text="")


def test_text_length_limit() -> None:
    assert make_breadcrumb(text="x" * MAX_TEXT_CHARS)
    with pytest.raises(ModelError, match="limited to 4000"):
        make_breadcrumb(text="x" * (MAX_TEXT_CHARS + 1))


def test_author_required() -> None:
    with pytest.raises(ModelError, match="author_id must not be empty"):
        make_breadcrumb(author_id="")


def test_csn_non_negative() -> None:
    with pytest.raises(ModelError, match="csn must be >= 0"):
        make_breadcrumb(csn=-1)


def test_acl_class_non_negative() -> None:
    assert make_breadcrumb().acl_class == 3
    with pytest.raises(ModelError, match="acl_class must be >= 0"):
        make_breadcrumb(acl_class=-1)


def test_read_set_entry_validation() -> None:
    with pytest.raises(ModelError, match="atom_id must be in"):
        ReadSetEntry(-1, 1)
    with pytest.raises(ModelError, match="version must be an int >= 1"):
        ReadSetEntry(7, 0)


def test_breadcrumb_is_frozen_and_hashable() -> None:
    crumb = make_breadcrumb()
    with pytest.raises(dataclasses.FrozenInstanceError):
        crumb.text = "changed"  # type: ignore[misc]
    assert hash(crumb) == hash(make_breadcrumb())
