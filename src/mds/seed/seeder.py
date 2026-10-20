"""Seeding: turn a question into the starting frontier `v₀` (DESIGN §8).

Runs the lexical, dense and entity seeders, fuses them with reciprocal rank
fusion, and returns only atoms in the principal's mask.
"""

from collections.abc import Collection, Iterable, Mapping
from pathlib import Path

from pyroaring import AbstractBitMap

from mds.model import Atom, AtomKind
from mds.seed.dense import DenseIndex, Embedder, dense_text
from mds.seed.entity import EntityIndex
from mds.seed.errors import LimitExceededError, SeedError
from mds.seed.fusion import reciprocal_rank_fusion
from mds.seed.hits import Hit, Match, SeedHit
from mds.seed.lexical import LexicalIndex
from mds.store import ContentStore, Snapshot

DEFAULT_K = 10
MAX_K = 50
# Each seeder contributes this many candidates to fusion, whatever `k` is.
FUSION_DEPTH = MAX_K


def parse_kinds(kinds: Iterable[str | AtomKind] | None) -> frozenset[AtomKind] | None:
    """Validate a `kinds` filter. None means every kind."""
    if kinds is None:
        return None
    try:
        return frozenset(AtomKind(k) for k in kinds)
    except ValueError as exc:
        raise SeedError(f"unknown atom kind in {sorted(map(str, kinds))}") from exc


class Seeder:
    """The three seed indexes kept in step, and the fused, masked search over them.

    `dense` is optional: without an embedding model, seeding is lexical and entity only.
    """

    def __init__(
        self,
        lexical: LexicalIndex,
        entity: EntityIndex | None = None,
        dense: DenseIndex | None = None,
    ) -> None:
        self.lexical = lexical
        self.entity = entity if entity is not None else EntityIndex()
        self.dense = dense

    def add_atom(self, atom: Atom, text: str = "", aliases: Iterable[str] = ()) -> None:
        """Index an atom (or re-index it after a new version). Call `commit` to publish."""
        body = f"{atom.title}\n{text}" if text else atom.title
        self.lexical.add(atom.atom_id, atom.acl_class, atom.kind, body)
        if self.dense is not None:
            self.dense.add(atom.atom_id, atom.acl_class, atom.kind, dense_text(atom.title, text))
        if atom.kind is AtomKind.ENTITY:
            self.entity.add(atom.atom_id, atom.title, aliases)

    def remove_atom(self, atom_id: int) -> None:
        """Drop an atom from every index (e.g. on tombstone)."""
        self.lexical.remove(atom_id)
        self.entity.remove(atom_id)
        if self.dense is not None:
            self.dense.remove(atom_id)

    def commit(self) -> None:
        """Publish buffered lexical writes."""
        self.lexical.commit()

    def seed(
        self,
        query: str,
        *,
        mask: AbstractBitMap,
        classes: Collection[int],
        kinds: Iterable[str | AtomKind] | None = None,
        k: int = DEFAULT_K,
    ) -> list[SeedHit]:
        """Fused seeds for `query`, visible to the principal, best first.

        `mask` is the principal's vertex mask `p_u`; `classes` are the
        `acl_class`es the principal can read (`ClassTable.readable_by`), used
        for lexical statistics. Scores are reciprocal-rank-fusion scores.
        """
        if not isinstance(k, int) or k < 1:
            raise SeedError(f"k must be a positive integer, got {k!r}")
        if k > MAX_K:
            raise LimitExceededError(f"k must be at most {MAX_K}, got {k}")
        if not query.strip():
            raise SeedError("query must not be empty")
        kind_set = parse_kinds(kinds)
        rankings: dict[Match, list[Hit]] = {
            Match.LEXICAL: self.lexical.search(
                query, classes=classes, mask=mask, kinds=kind_set, k=FUSION_DEPTH
            ),
            Match.ENTITY: self.entity.search(query, mask=mask, kinds=kind_set, k=FUSION_DEPTH),
        }
        if self.dense is not None:
            rankings[Match.DENSE] = self.dense.search(
                query, mask=mask, kinds=kind_set, k=FUSION_DEPTH
            )
        fused = reciprocal_rank_fusion(rankings, k=k)
        # Every seeder masks already; this keeps the guarantee local to this function.
        return [h for h in fused if h.atom_id in mask]


def build_seeder(
    snapshot: Snapshot,
    content: ContentStore,
    *,
    lexical_path: Path | None = None,
    embedder: Embedder | None = None,
    aliases: Mapping[int, Iterable[str]] | None = None,
) -> Seeder:
    """Index every atom present in `snapshot`, with its current version's text.

    `aliases` maps entity `atom_id`s to alternative names. An existing index at
    `lexical_path` is rebuilt from scratch.
    """
    lexical = LexicalIndex(lexical_path)
    seeder = Seeder(lexical, EntityIndex(), DenseIndex(embedder) if embedder else None)
    lexical.clear()
    aliases = aliases or {}
    for atom in snapshot.atoms():
        version = snapshot.version(atom.atom_id)
        text = (
            ""
            if version is None
            else content.slice(version.digest, version.byte_offset, version.byte_length)
        )
        seeder.add_atom(atom, text, aliases.get(atom.atom_id, ()))
    seeder.commit()
    return seeder
