"""Interned `acl_class` ids: one small int per distinct set of reader groups (DESIGN §7.1)."""

from collections.abc import Iterable, Iterator, Mapping
from pathlib import PurePosixPath

from mds.acl.config import Config
from mds.model.errors import check_non_negative


class ClassTable:
    """Two-way map between reader-group sets and `acl_class` ids.

    Ids are handed out in order of first use and never change or get reused, so
    an atom's stored `acl_class` stays valid for the life of the store. Pass the
    persisted table back in as `classes` to keep ids stable across restarts.
    """

    def __init__(self, classes: Mapping[int, Iterable[str]] | None = None) -> None:
        self._groups: dict[int, frozenset[str]] = {}
        self._ids: dict[frozenset[str], int] = {}
        for acl_class, groups in (classes or {}).items():
            check_non_negative(acl_class, "acl_class")
            key = frozenset(groups)
            if key in self._ids:
                raise ValueError(
                    f"acl_class {acl_class} and {self._ids[key]} have the same groups {sorted(key)}"
                )
            self._groups[acl_class] = key
            self._ids[key] = acl_class
        self._next = max(self._groups, default=-1) + 1

    def intern(self, groups: Iterable[str]) -> int:
        """Return the id for this set of groups, allocating the next id if it is new."""
        key = frozenset(groups)
        acl_class = self._ids.get(key)
        if acl_class is None:
            acl_class = self._next
            self._next += 1
            self._ids[key] = acl_class
            self._groups[acl_class] = key
        return acl_class

    def groups(self, acl_class: int) -> frozenset[str]:
        """The groups that may read atoms of this class."""
        try:
            return self._groups[acl_class]
        except KeyError:
            raise LookupError(f"unknown acl_class {acl_class}") from None

    def readable_by(self, principal_groups: Iterable[str]) -> frozenset[int]:
        """Classes a member of `principal_groups` may read: those sharing at least one group."""
        mine = frozenset(principal_groups)
        return frozenset(c for c, groups in self._groups.items() if groups & mine)

    def items(self) -> Iterator[tuple[int, frozenset[str]]]:
        """All `(acl_class, groups)` pairs in id order, for persisting."""
        return iter(sorted(self._groups.items()))

    def __contains__(self, acl_class: object) -> bool:
        return acl_class in self._groups

    def __len__(self) -> int:
        return len(self._groups)


def acl_class_for_path(config: Config, classes: ClassTable, path: str | PurePosixPath) -> int:
    """Intern the class for a path relative to the ingest root, using the first matching rule."""
    return classes.intern(config.groups_for_path(path))
