"""Atoms and atom versions (DESIGN.md sections 2 and 2.1)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mds.model.enums import AtomKind
from mds.model.errors import (
    ModelError,
    as_enum,
    check_atom_id,
    check_csn_interval,
    check_non_negative,
)


@dataclass(frozen=True, slots=True)
class Atom:
    """A unit being filed: a document, section, item, entity or placeholder."""

    atom_id: int
    kind: AtomKind
    title: str
    path_hint: str
    acl_class: int
    created_csn: int
    source_uri: str | None = None
    tombstone_csn: int | None = None

    def __post_init__(self) -> None:
        check_atom_id(self.atom_id)
        object.__setattr__(self, "kind", as_enum(AtomKind, self.kind, "kind"))
        check_non_negative(self.acl_class, "acl_class")
        check_non_negative(self.created_csn, "created_csn")
        if self.kind is AtomKind.DOCUMENT and not self.source_uri:
            raise ModelError("document atoms need a source_uri")
        if self.kind is not AtomKind.DOCUMENT and self.source_uri is not None:
            raise ModelError(f"only document atoms have a source_uri, not {self.kind}")
        if self.tombstone_csn is not None:
            check_non_negative(self.tombstone_csn, "tombstone_csn")
            if self.tombstone_csn <= self.created_csn:
                raise ModelError(
                    f"tombstone_csn ({self.tombstone_csn}) must be after "
                    f"created_csn ({self.created_csn})"
                )


@dataclass(frozen=True, slots=True)
class AtomVersion:
    """One revision of an atom's content: a byte range inside a stored derivative."""

    atom_id: int
    version: int
    digest: str
    byte_offset: int
    byte_length: int
    normaliser: str
    csn_from: int
    csn_to: int | None = None
    # Excluded from hashing because a mapping is not hashable.
    origin: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        check_atom_id(self.atom_id)
        if not isinstance(self.version, int) or self.version < 1:
            raise ModelError(f"version must be an int >= 1, got {self.version!r}")
        if not _is_blake3_hex(self.digest):
            raise ModelError(f"digest must be 64 lowercase hex characters, got {self.digest!r}")
        check_non_negative(self.byte_offset, "byte_offset")
        check_non_negative(self.byte_length, "byte_length")
        if not self.normaliser:
            raise ModelError("normaliser must not be empty")
        check_csn_interval(self.csn_from, self.csn_to)


def _is_blake3_hex(digest: str) -> bool:
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
