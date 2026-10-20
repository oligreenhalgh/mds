"""Breadcrumbs: notes agents pin to atoms (DESIGN.md section 10, INTERFACES.md `mds_breadcrumb`)."""

from dataclasses import dataclass

from mds.model.errors import ModelError, check_atom_id, check_non_negative

MAX_TEXT_CHARS = 4000


@dataclass(frozen=True, slots=True)
class ReadSetEntry:
    """One atom version a session read before writing (DESIGN §10.1)."""

    atom_id: int
    version: int

    def __post_init__(self) -> None:
        check_atom_id(self.atom_id)
        if not isinstance(self.version, int) or self.version < 1:
            raise ModelError(f"version must be an int >= 1, got {self.version!r}")


@dataclass(frozen=True, slots=True)
class Breadcrumb:
    """A note pinned to one or more atoms, with its author, CSN and read-set.

    `acl_class` is the author's class: readers see only breadcrumbs they could read
    (DESIGN §7.4).
    """

    breadcrumb_id: int
    atom_ids: tuple[int, ...]
    text: str
    author_id: str
    csn: int
    acl_class: int
    read_set: tuple[ReadSetEntry, ...] = ()

    def __post_init__(self) -> None:
        check_non_negative(self.breadcrumb_id, "breadcrumb_id")
        object.__setattr__(self, "atom_ids", tuple(self.atom_ids))
        object.__setattr__(self, "read_set", tuple(self.read_set))
        if not self.atom_ids:
            raise ModelError("a breadcrumb must be pinned to at least one atom")
        for atom_id in self.atom_ids:
            check_atom_id(atom_id)
        if not self.text:
            raise ModelError("breadcrumb text must not be empty")
        if len(self.text) > MAX_TEXT_CHARS:
            raise ModelError(f"breadcrumb text is limited to {MAX_TEXT_CHARS} characters")
        if not self.author_id:
            raise ModelError("author_id must not be empty")
        check_non_negative(self.csn, "csn")
        check_non_negative(self.acl_class, "acl_class")
