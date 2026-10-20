"""Seeding never reveals a hidden document, in results or through scores and IDF (DESIGN §7.4)."""

from pathlib import Path

from mds.acl import ClassTable, MaskCache, acl_class_for_path, load_config
from mds.model import AtomKind
from mds.seed import Embedder, build_seeder
from mds.store import Store

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mds.toml"

VISIBLE = {
    "policies/retention.md": "Data retention policy. Personal data is retained for six years.",
    "policies/deletion.md": "Records are deleted when the retention period ends.",
    "policies/holiday.md": "Holiday policy for staff.",
}
# Readable by `legal` only. Repeats the query terms so it would move IDF and length norms.
HIDDEN = {
    "legal/acme-hold.md": "Retention retention retention. Legal hold: retain all Acme data "
    "beyond the retention policy period. Project Nightjar.",
}
HIDDEN_ENTITY = "Project Nightjar"

QUERIES = ["retention policy", "personal data retained", "Acme", "Project Nightjar", "holiday"]


def _seed_all(
    tmp_path: Path, embedder: Embedder, principal: str, with_hidden: bool
) -> dict[str, list[tuple]]:
    cfg = load_config(FIXTURE)
    table = ClassTable()
    docs = dict(VISIBLE)
    if with_hidden:
        # Insert the hidden document first so visible atom ids differ between the two runs.
        docs = {**HIDDEN, **VISIBLE}
    with Store(tmp_path / f"{principal}-{with_hidden}") as store:
        titles: dict[int, str] = {}
        with store.commit("test", "ingest") as tx:
            for path, text in docs.items():
                cls = acl_class_for_path(cfg, table, path)
                digest = store.content.put(text)
                atom = tx.add_atom(AtomKind.DOCUMENT, path, path, cls, f"file:///{path}")
                tx.add_version(atom.atom_id, digest, 0, len(text.encode()), "md/norm-1")
                titles[atom.atom_id] = path
            if with_hidden:
                legal = acl_class_for_path(cfg, table, "legal/x")
                ent = tx.add_atom(AtomKind.ENTITY, HIDDEN_ENTITY, HIDDEN_ENTITY, legal)
                titles[ent.atom_id] = HIDDEN_ENTITY
        snapshot = store.snapshot()
        seeder = build_seeder(snapshot, store.content, embedder=embedder)
        masks = MaskCache(
            cfg.principals, table, [(a.atom_id, a.acl_class) for a in snapshot.atoms()]
        )
    mask = masks.mask(principal)
    classes = table.readable_by(cfg.principals[principal].groups)
    return {
        q: [
            (titles[h.atom_id], h.kind, h.score, h.match)
            for h in seeder.seed(q, mask=mask, classes=classes, k=50)
        ]
        for q in QUERIES
    }


def test_hidden_document_changes_neither_results_nor_scores(
    tmp_path: Path, fake_embedder: Embedder
) -> None:
    without = _seed_all(tmp_path, fake_embedder, "research-agent", with_hidden=False)
    with_hidden = _seed_all(tmp_path, fake_embedder, "research-agent", with_hidden=True)
    assert with_hidden == without
    returned = {title for hits in with_hidden.values() for title, *_ in hits}
    assert returned <= set(VISIBLE)


def test_lexical_scores_are_identical_through_idf(tmp_path: Path) -> None:
    # Compare raw BM25 too, since fused scores depend on rank only.
    cfg = load_config(FIXTURE)
    scores = []
    for with_hidden in (False, True):
        table = ClassTable()
        docs = {**HIDDEN, **VISIBLE} if with_hidden else dict(VISIBLE)
        with Store(tmp_path / f"bm25-{with_hidden}") as store:
            with store.commit("test", "ingest") as tx:
                for path, text in docs.items():
                    cls = acl_class_for_path(cfg, table, path)
                    digest = store.content.put(text)
                    atom = tx.add_atom(AtomKind.DOCUMENT, path, path, cls, f"file:///{path}")
                    tx.add_version(atom.atom_id, digest, 0, len(text.encode()), "md/norm-1")
            snapshot = store.snapshot()
            seeder = build_seeder(snapshot, store.content)
            masks = MaskCache(
                cfg.principals, table, [(a.atom_id, a.acl_class) for a in snapshot.atoms()]
            )
            titles = {a.atom_id: a.title for a in snapshot.atoms()}
        classes = table.readable_by(cfg.principals["research-agent"].groups)
        hits = seeder.lexical.search(
            "retention policy", classes=classes, mask=masks.mask("research-agent"), k=50
        )
        scores.append({titles[h.atom_id]: h.score for h in hits})
    assert scores[0] == scores[1]
    assert scores[0]


def test_the_hidden_document_would_move_scores_for_a_reader(
    tmp_path: Path, fake_embedder: Embedder
) -> None:
    """Guard: the leak tests above are only meaningful if the hidden text affects IDF."""
    without = _seed_all(tmp_path, fake_embedder, "alice", with_hidden=False)
    with_hidden = _seed_all(tmp_path, fake_embedder, "alice", with_hidden=True)
    assert with_hidden != without
    assert any(t == "legal/acme-hold.md" for hits in with_hidden.values() for t, *_ in hits)
    assert any(t == HIDDEN_ENTITY for t, *_ in with_hidden["Project Nightjar"])
