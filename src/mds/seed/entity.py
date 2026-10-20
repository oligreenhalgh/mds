"""Entity seeding: exact and alias match of entity names in the query (DESIGN §8).

Names and queries are compared as token sequences (lowercased, accents
folded, punctuation dropped). A query that is exactly an entity's name scores
highest; a name that appears as consecutive words inside a longer query
scores by how much of the query it covers.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from pyroaring import AbstractBitMap

from mds.model import AtomKind
from mds.seed.hits import Hit, top_k
from mds.seed.text import name_analyzer

EXACT_NAME = 1.0
EXACT_ALIAS = 0.9
# Contained matches score below any exact match: weight × share of query tokens covered.
CONTAINED_NAME = 0.8
CONTAINED_ALIAS = 0.7


@dataclass(frozen=True, slots=True)
class _Key:
    atom_id: int
    is_alias: bool


class EntityIndex:
    """Entity names and aliases, keyed by token sequence."""

    def __init__(self) -> None:
        self._analyzer = name_analyzer()
        self._by_tokens: dict[tuple[str, ...], set[_Key]] = {}
        self._keys_of: dict[int, list[tuple[str, ...]]] = {}
        self._max_len = 0

    def tokens(self, text: str) -> tuple[str, ...]:
        """Normalised word sequence for a name or query."""
        return tuple(str(t) for t in self._analyzer.analyze(text))

    def add(self, atom_id: int, name: str, aliases: Iterable[str] = ()) -> None:
        """Register an entity atom's name and aliases, replacing any earlier ones."""
        self.remove(atom_id)
        entries = [(self.tokens(name), False)] + [(self.tokens(a), True) for a in aliases]
        keys: list[tuple[str, ...]] = []
        for tokens, is_alias in entries:
            if not tokens:
                continue
            self._by_tokens.setdefault(tokens, set()).add(_Key(atom_id, is_alias))
            keys.append(tokens)
            self._max_len = max(self._max_len, len(tokens))
        self._keys_of[atom_id] = keys

    def remove(self, atom_id: int) -> None:
        """Drop an entity from the index."""
        for tokens in self._keys_of.pop(atom_id, []):
            bucket = self._by_tokens[tokens]
            bucket.difference_update({_Key(atom_id, False), _Key(atom_id, True)})
            if not bucket:
                del self._by_tokens[tokens]

    def search(
        self,
        query: str,
        *,
        mask: AbstractBitMap,
        kinds: Collection[AtomKind] | None = None,
        k: int,
    ) -> list[Hit]:
        """Top `k` visible entities named in `query`."""
        if k < 1 or (kinds is not None and AtomKind.ENTITY not in kinds):
            return []
        q = self.tokens(query)
        if not q:
            return []
        best: dict[int, float] = {}
        for n in range(1, min(len(q), self._max_len) + 1):
            for start in range(len(q) - n + 1):
                for key in self._by_tokens.get(q[start : start + n], ()):
                    if key.atom_id not in mask:
                        continue
                    if n == len(q):
                        s = EXACT_ALIAS if key.is_alias else EXACT_NAME
                    else:
                        weight = CONTAINED_ALIAS if key.is_alias else CONTAINED_NAME
                        s = weight * n / len(q)
                    if s > best.get(key.atom_id, 0.0):
                        best[key.atom_id] = s
        return top_k([Hit(a, AtomKind.ENTITY, s) for a, s in best.items()], k)
