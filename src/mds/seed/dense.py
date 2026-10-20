"""Dense seeding: cosine similarity over embeddings of atom titles and first lines (DESIGN §8).

The embedding model is behind the `Embedder` protocol. MDS ships no model:
whoever opens the server supplies one loaded from a local cache, and tests use
a fake. There is no network access and no LLM call here.
"""

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from pyroaring import AbstractBitMap

from mds.model import AtomKind
from mds.seed.hits import Hit, top_k
from mds.seed.text import first_line


class Embedder(Protocol):
    """Turns texts into fixed-length vectors. Must be deterministic and run offline."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """One vector per text, all of the same length."""
        ...


def dense_text(title: str, text: str) -> str:
    """What the dense index embeds for an atom: its title and the first line of its text."""
    line = first_line(text)
    return f"{title}\n{line}" if line and line != title.strip() else title


def _unit(vector: Sequence[float]) -> tuple[float, ...] | None:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return None
    return tuple(x / norm for x in vector)


@dataclass(frozen=True, slots=True)
class _Entry:
    acl_class: int
    kind: AtomKind
    vector: tuple[float, ...]


class DenseIndex:
    """A small in-memory index searched exhaustively. Rebuilt from the store at start-up."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._entries: dict[int, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, atom_id: int, acl_class: int, kind: AtomKind, text: str) -> None:
        """Embed `text` for an atom, replacing any earlier entry. Empty vectors are skipped."""
        self._entries.pop(atom_id, None)
        (vector,) = self._embedder.embed([text])
        unit = _unit(vector)
        if unit is not None:
            self._entries[atom_id] = _Entry(acl_class, kind, unit)

    def remove(self, atom_id: int) -> None:
        """Drop an atom from the index."""
        self._entries.pop(atom_id, None)

    def search(
        self,
        query: str,
        *,
        mask: AbstractBitMap,
        kinds: Collection[AtomKind] | None = None,
        k: int,
        min_score: float = 0.0,
    ) -> list[Hit]:
        """Top `k` atoms in `mask` by cosine similarity above `min_score`."""
        if k < 1 or not query.strip():
            return []
        (vector,) = self._embedder.embed([query])
        q = _unit(vector)
        if q is None:
            return []
        hits: list[Hit] = []
        for atom_id, entry in self._entries.items():
            # Mask before scoring: invisible atoms are never compared.
            if atom_id not in mask or (kinds is not None and entry.kind not in kinds):
                continue
            if len(entry.vector) != len(q):
                raise ValueError(
                    f"embedder returned {len(q)} dimensions, index holds {len(entry.vector)}"
                )
            s = sum(a * b for a, b in zip(q, entry.vector, strict=True))
            if s > min_score:
                hits.append(Hit(atom_id, entry.kind, s))
        return top_k(hits, k)
