import dataclasses
from typing import Any

import pytest

from mds.model import ATOM_ID_LIMIT, Atom, AtomKind, AtomVersion, ModelError

DIGEST = "ab" * 32


def make_atom(**overrides: Any) -> Atom:
    fields: dict[str, Any] = {
        "atom_id": 7,
        "kind": AtomKind.SECTION,
        "title": "4.2 Retention period",
        "path_hint": "policy-2021.pdf › 4 › 4.2",
        "acl_class": 0,
        "created_csn": 10,
    }
    fields.update(overrides)
    return Atom(**fields)


def make_version(**overrides: Any) -> AtomVersion:
    fields: dict[str, Any] = {
        "atom_id": 7,
        "version": 1,
        "digest": DIGEST,
        "byte_offset": 0,
        "byte_length": 120,
        "normaliser": "docling-2.x/norm-1",
        "csn_from": 10,
    }
    fields.update(overrides)
    return AtomVersion(**fields)


# Atom ---------------------------------------------------------------------


@pytest.mark.parametrize("atom_id", [0, 1, ATOM_ID_LIMIT - 1])
def test_atom_id_in_range_accepted(atom_id: int) -> None:
    assert make_atom(atom_id=atom_id).atom_id == atom_id


@pytest.mark.parametrize("atom_id", [-1, ATOM_ID_LIMIT, 2**40])
def test_atom_id_out_of_range_rejected(atom_id: int) -> None:
    with pytest.raises(ModelError, match=r"atom_id must be in \[0, 2\*\*32\)"):
        make_atom(atom_id=atom_id)


@pytest.mark.parametrize("atom_id", [1.0, "1", True, None])
def test_atom_id_must_be_int(atom_id: Any) -> None:
    with pytest.raises(ModelError, match="atom_id must be an int"):
        make_atom(atom_id=atom_id)


@pytest.mark.parametrize("kind", list(AtomKind))
def test_every_kind_accepted(kind: AtomKind) -> None:
    uri = "file:///x.md" if kind is AtomKind.DOCUMENT else None
    assert make_atom(kind=kind, source_uri=uri).kind is kind


def test_kind_coerced_from_string() -> None:
    assert make_atom(kind="entity").kind is AtomKind.ENTITY


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ModelError, match="kind must be one of document, section"):
        make_atom(kind="folder")


def test_document_requires_source_uri() -> None:
    with pytest.raises(ModelError, match="need a source_uri"):
        make_atom(kind=AtomKind.DOCUMENT)
    doc = make_atom(kind=AtomKind.DOCUMENT, source_uri="file:///pep-0008.rst")
    assert doc.source_uri == "file:///pep-0008.rst"


def test_source_uri_only_on_documents() -> None:
    with pytest.raises(ModelError, match="only document atoms have a source_uri"):
        make_atom(kind=AtomKind.SECTION, source_uri="file:///x.md")


@pytest.mark.parametrize("field", ["acl_class", "created_csn"])
def test_negative_counters_rejected(field: str) -> None:
    with pytest.raises(ModelError, match=f"{field} must be >= 0"):
        make_atom(**{field: -1})


def test_tombstone_after_created_accepted() -> None:
    assert make_atom(created_csn=10, tombstone_csn=11).tombstone_csn == 11


@pytest.mark.parametrize("tombstone", [9, 10])
def test_tombstone_not_after_created_rejected(tombstone: int) -> None:
    with pytest.raises(ModelError, match="tombstone_csn .* must be after created_csn"):
        make_atom(created_csn=10, tombstone_csn=tombstone)


def test_atom_is_frozen_and_hashable() -> None:
    atom = make_atom()
    with pytest.raises(dataclasses.FrozenInstanceError):
        atom.title = "other"  # type: ignore[misc]
    assert hash(atom) == hash(make_atom())


# AtomVersion --------------------------------------------------------------


def test_valid_version_accepted() -> None:
    v = make_version(csn_to=12, origin={"page": 3, "bbox": [0, 0, 10, 10]})
    assert v.csn_to == 12
    assert v.origin["page"] == 3


def test_version_atom_id_range_checked() -> None:
    with pytest.raises(ModelError, match="atom_id must be in"):
        make_version(atom_id=ATOM_ID_LIMIT)


@pytest.mark.parametrize("version", [0, -1])
def test_version_number_starts_at_one(version: int) -> None:
    with pytest.raises(ModelError, match="version must be an int >= 1"):
        make_version(version=version)


@pytest.mark.parametrize("digest", ["", "ab" * 31, "AB" * 32, "zz" * 32])
def test_digest_must_be_blake3_hex(digest: str) -> None:
    with pytest.raises(ModelError, match="digest must be 64 lowercase hex"):
        make_version(digest=digest)


@pytest.mark.parametrize("field", ["byte_offset", "byte_length"])
def test_byte_range_non_negative(field: str) -> None:
    with pytest.raises(ModelError, match=f"{field} must be >= 0"):
        make_version(**{field: -1})


def test_normaliser_required() -> None:
    with pytest.raises(ModelError, match="normaliser must not be empty"):
        make_version(normaliser="")


@pytest.mark.parametrize("csn_to", [10, 9])
def test_version_csn_from_before_csn_to(csn_to: int) -> None:
    with pytest.raises(ModelError, match="csn_from .* must be before csn_to"):
        make_version(csn_from=10, csn_to=csn_to)


def test_version_negative_csn_rejected() -> None:
    with pytest.raises(ModelError, match="csn_from must be >= 0"):
        make_version(csn_from=-1)


def test_version_hash_ignores_origin() -> None:
    a = make_version(origin={"page": 1})
    b = make_version(origin={"page": 1})
    assert a == b
    assert hash(a) == hash(b)
