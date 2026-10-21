from pathlib import Path

import pytest
from pyroaring import BitMap

from mds.model import Atom, AtomKind
from mds.seed import (
    MAX_K,
    DenseIndex,
    Embedder,
    EntityIndex,
    LexicalIndex,
    LimitExceededError,
    Match,
    Seeder,
    SeedError,
    build_seeder,
)
from mds.store import Store


def atom(atom_id: int, kind: AtomKind, title: str, acl_class: int = 0) -> Atom:
    uri = f"file:///{atom_id}" if kind is AtomKind.DOCUMENT else None
    return Atom(atom_id, kind, title, title, acl_class, 1, source_uri=uri)


@pytest.fixture
def seeder(fake_embedder: Embedder) -> Seeder:
    s = Seeder(LexicalIndex(), EntityIndex(), DenseIndex(fake_embedder))
    s.add_atom(atom(1, AtomKind.DOCUMENT, "policy.md"), "Data retention policy for Acme")
    s.add_atom(atom(2, AtomKind.SECTION, "4.2 Retention"), "Records are kept six years.")
    s.add_atom(atom(3, AtomKind.ENTITY, "Acme Holdings"), aliases=["Acme"])
    s.add_atom(atom(4, AtomKind.SECTION, "Secret retention", acl_class=1), "Acme legal hold.")
    s.commit()
    return s


ALL = BitMap([1, 2, 3, 4])


def test_fuses_all_three_seeders(seeder: Seeder) -> None:
    hits = seeder.seed("Acme", mask=ALL, classes={0, 1}, k=10)
    by_id = {h.atom_id: h for h in hits}
    assert by_id[3].match is Match.ENTITY
    assert by_id[3].kind is AtomKind.ENTITY
    assert hits[0].atom_id == 3
    assert {1, 3, 4} <= set(by_id)


def test_masks_every_seeder(seeder: Seeder) -> None:
    for query in ("Acme", "secret retention", "legal hold"):
        hits = seeder.seed(query, mask=BitMap([1, 2]), classes={0}, k=10)
        assert {h.atom_id for h in hits} <= {1, 2}


def test_kinds_filter(seeder: Seeder) -> None:
    hits = seeder.seed("Acme retention", mask=ALL, classes={0, 1}, kinds=["section"], k=10)
    assert {h.kind for h in hits} == {AtomKind.SECTION}


def test_validation(seeder: Seeder) -> None:
    with pytest.raises(LimitExceededError) as exc:
        seeder.seed("x", mask=ALL, classes={0}, k=MAX_K + 1)
    assert exc.value.code == "limit_exceeded"
    for bad_k in (0, -1):
        with pytest.raises(SeedError) as err:
            seeder.seed("x", mask=ALL, classes={0}, k=bad_k)
        assert err.value.code == "bad_request"
    with pytest.raises(SeedError):
        seeder.seed("   ", mask=ALL, classes={0})
    with pytest.raises(SeedError):
        seeder.seed("x", mask=ALL, classes={0}, kinds=["folder"])


def test_k_limits_results(seeder: Seeder) -> None:
    assert len(seeder.seed("Acme retention", mask=ALL, classes={0, 1}, k=2)) == 2


def test_remove_atom_drops_it_everywhere(seeder: Seeder) -> None:
    seeder.remove_atom(3)
    seeder.commit()
    assert 3 not in {h.atom_id for h in seeder.seed("Acme Holdings", mask=ALL, classes={0, 1})}


def test_works_without_a_dense_index() -> None:
    s = Seeder(LexicalIndex())
    s.add_atom(atom(1, AtomKind.SECTION, "Retention"), "kept six years")
    s.commit()
    hits = s.seed("retention", mask=BitMap([1]), classes={0})
    assert [(h.atom_id, h.match) for h in hits] == [(1, Match.LEXICAL)]


def test_build_seeder_indexes_a_snapshot(tmp_path: Path, fake_embedder: Embedder) -> None:
    with Store(tmp_path / "data") as store:
        text = "# Policy\n\n## 4.2 Retention\nPersonal data is kept for six years.\n"
        digest = store.content.put(text)
        start = text.index("## 4.2")
        with store.commit("test", "ingest") as tx:
            doc = tx.add_atom(AtomKind.DOCUMENT, "policy.md", "policy.md", 0, "file:///policy.md")
            sec = tx.add_atom(AtomKind.SECTION, "4.2 Retention", "policy.md › 4.2", 0)
            gone = tx.add_atom(AtomKind.SECTION, "Old retention", "policy.md › old", 0)
            ent = tx.add_atom(AtomKind.ENTITY, "Acme", "Acme", 0)
            tx.add_version(doc.atom_id, digest, 0, len(text.encode()), "md/norm-1")
            tx.add_version(sec.atom_id, digest, start, len(text.encode()) - start, "md/norm-1")
        with store.commit("test", "tombstone") as tx:
            tx.tombstone_atom(gone.atom_id)
        seeder = build_seeder(
            store.snapshot(),
            store.content,
            lexical_path=tmp_path / "seed",
            embedder=fake_embedder,
            aliases={ent.atom_id: ["Acme Corp"]},
        )
    mask = BitMap([doc.atom_id, sec.atom_id, gone.atom_id, ent.atom_id])
    hits = seeder.seed("personal data six years", mask=mask, classes={0})
    assert {h.atom_id for h in hits} == {doc.atom_id, sec.atom_id}
    assert gone.atom_id not in {h.atom_id for h in seeder.seed("old", mask=mask, classes={0})}
    alias = seeder.seed("acme corp", mask=mask, classes={0})
    assert (alias[0].atom_id, alias[0].match) == (ent.atom_id, Match.ENTITY)
