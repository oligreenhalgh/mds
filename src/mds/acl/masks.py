"""Per-principal vertex masks over `atom_id`s (DESIGN §7.1)."""

from collections.abc import Iterable, Mapping

from pyroaring import BitMap, FrozenBitMap

from mds.acl.classes import ClassTable
from mds.acl.config import Principal, lookup_principal
from mds.model.errors import check_atom_id


class MaskCache:
    """Builds and caches each principal's vertex mask `p_u`.

    The mask is the union of the atom sets of every class the principal can
    read. Atoms come in as `(atom_id, acl_class)` pairs from whoever owns them
    (the store), so this module does no I/O.

    Invalidation:

    - `set_atoms` drops only the masks of principals who can read a class an
      atom left or joined.
    - `set_principals` (memberships changed) drops every mask.
    - When rules change, the caller re-classifies the affected atoms and passes
      the new pairs to `set_atoms`.
    """

    def __init__(
        self,
        principals: Mapping[str, Principal],
        classes: ClassTable,
        atoms: Iterable[tuple[int, int]] = (),
    ) -> None:
        self._principals = dict(principals)
        self._classes = classes
        self._class_of: dict[int, int] = {}
        self._by_class: dict[int, BitMap] = {}
        self._masks: dict[str, FrozenBitMap] = {}
        self.set_atoms(atoms)

    def mask(self, principal: str) -> FrozenBitMap:
        """The atoms `principal` may see. Raises `UnknownPrincipalError` for unknown names."""
        cached = self._masks.get(principal)
        if cached is not None:
            return cached
        groups = lookup_principal(self._principals, principal).groups
        mask = BitMap()
        for acl_class in self._classes.readable_by(groups):
            atoms = self._by_class.get(acl_class)
            if atoms is not None:
                mask |= atoms
        frozen = FrozenBitMap(mask)
        self._masks[principal] = frozen
        return frozen

    def set_atoms(self, pairs: Iterable[tuple[int, int]]) -> None:
        """Add atoms, or move existing atoms to a new class, from `(atom_id, acl_class)` pairs."""
        # Validate everything first so a bad pair never leaves masks stale.
        pairs = list(pairs)
        for atom_id, acl_class in pairs:
            check_atom_id(atom_id)
            if acl_class not in self._classes:
                raise LookupError(f"atom {atom_id} has unknown acl_class {acl_class}")
        touched: set[int] = set()
        for atom_id, acl_class in pairs:
            old = self._class_of.get(atom_id)
            if old == acl_class:
                continue
            if old is not None:
                self._by_class[old].discard(atom_id)
                touched.add(old)
            self._by_class.setdefault(acl_class, BitMap()).add(atom_id)
            self._class_of[atom_id] = acl_class
            touched.add(acl_class)
        if touched:
            self._invalidate_readers(touched)

    def set_principals(self, principals: Mapping[str, Principal]) -> None:
        """Replace the principals (e.g. after a config reload) and drop every cached mask."""
        self._principals = dict(principals)
        self._masks.clear()

    def clear(self) -> None:
        """Drop every cached mask; they are rebuilt on next use."""
        self._masks.clear()

    def acl_class_of(self, atom_id: int) -> int | None:
        """The class this cache holds for `atom_id`, or None if it has not been given."""
        return self._class_of.get(atom_id)

    def is_cached(self, principal: str) -> bool:
        """True if `principal`'s mask is cached (for tests and diagnostics)."""
        return principal in self._masks

    def _invalidate_readers(self, acl_classes: set[int]) -> None:
        readers = frozenset().union(*(self._classes.groups(c) for c in acl_classes))
        for name in list(self._masks):
            principal = self._principals.get(name)
            if principal is None or principal.groups & readers:
                del self._masks[name]
