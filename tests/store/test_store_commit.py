import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mds.model import Atom, AtomKind, ModelError, UnknownDimensionError
from mds.store import Change, Store, StoreError
from mds.store.sqlite import DB_NAME


def test_new_store_starts_at_csn_zero(store: Store) -> None:
    assert store.latest_csn == 0
    assert store.commits() == []
    assert store.snapshot().atoms() == []


def test_each_commit_takes_the_next_csn(store: Store) -> None:
    for expected in (1, 2, 3):
        with store.commit("ingest", f"commit {expected}") as tx:
            assert tx.csn == expected
            atom = tx.add_atom(AtomKind.ENTITY, f"e{expected}", "", 0)
        assert atom.created_csn == expected
        assert store.latest_csn == expected


def test_empty_commit_uses_no_csn(store: Store) -> None:
    with store.commit("agent", "nothing"):
        pass
    assert store.latest_csn == 0
    assert store.commits() == []


def test_exception_rolls_back_whole_commit(store: Store, two_atoms: tuple[Atom, Atom]) -> None:
    with pytest.raises(RuntimeError), store.commit("ingest", "fails") as tx:
        tx.add_atom(AtomKind.ENTITY, "Acme", "", 0)
        tx.add_edge("r1", 0, 1, 1.0, "extractor", "x/1", "confirmed")
        raise RuntimeError("boom")
    assert store.latest_csn == 1
    snap = store.snapshot()
    assert [a.atom_id for a in snap.atoms()] == [0, 1]
    assert snap.edges() == []
    # The CSN is reused by the next successful commit.
    with store.commit("ingest", "ok") as tx:
        assert tx.csn == 2


def test_validation_error_rolls_back(store: Store, two_atoms: tuple[Atom, Atom]) -> None:
    with pytest.raises(ModelError, match="not time-bound"), store.commit("ingest", "bad") as tx:
        tx.add_atom(AtomKind.ENTITY, "Acme", "", 0)
        tx.add_edge("r1", 0, 1, 1.0, "extractor", "x/1", "confirmed", valid_from=date(2023, 3, 1))
    assert store.latest_csn == 1
    assert len(store.snapshot().atoms()) == 2


def test_unknown_dimension_rejected(store: Store, two_atoms: tuple[Atom, Atom]) -> None:
    with pytest.raises(UnknownDimensionError), store.commit("ingest", "bad") as tx:
        tx.add_edge("cites", 0, 1, 1.0, "extractor", "x/1", "confirmed")


def test_one_writer_at_a_time(store: Store) -> None:
    with (
        store.commit("a", "outer"),
        pytest.raises(StoreError, match="one writer"),
        store.commit("b", "inner"),
    ):
        pass


def test_commit_needs_author(store: Store) -> None:
    with pytest.raises(StoreError, match="author_id"), store.commit("", "x"):
        pass


def test_finished_transaction_cannot_write(store: Store) -> None:
    with store.commit("a", "x") as tx:
        tx.add_atom(AtomKind.ENTITY, "Acme", "", 0)
    with pytest.raises(StoreError, match="finished"):
        tx.add_atom(AtomKind.ENTITY, "Late", "", 0)


def test_commit_log_and_change_feed(store: Store, two_atoms: tuple[Atom, Atom]) -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    with store.commit("research-agent", "linked sections") as tx:
        tx.add_edge("references", 0, 1, 0.8, "agent", "research-agent", "pending")
    commits = store.commits()
    assert [(c.csn, c.author_id, c.summary) for c in commits] == [
        (1, "test", "seed"),
        (2, "research-agent", "linked sections"),
    ]
    assert commits[1].committed_at >= before
    assert store.commits(since_csn=1) == commits[1:]
    assert store.changes(since_csn=1) == [Change(2, "edge", (0, 1), "added references edge 0")]
    assert [c.kind for c in store.changes()] == ["atom", "atom", "edge"]


def test_ids_are_allocated_in_order(store: Store) -> None:
    with store.commit("a", "x") as tx:
        ids = [tx.add_atom(AtomKind.ENTITY, str(i), "", 0).atom_id for i in range(3)]
    assert ids == [0, 1, 2]


def test_unknown_atoms_rejected(store: Store, two_atoms: tuple[Atom, Atom]) -> None:
    cases = [
        lambda tx: tx.add_edge("r1", 0, 99, 1.0, "extractor", "x/1", "confirmed"),
        lambda tx: tx.add_edge("r1", 0, 1, 1.0, "extractor", "x/1", "confirmed", evidence=[99]),
        lambda tx: tx.add_breadcrumb([99], "note", "agent", 0),
        lambda tx: tx.add_version(99, "a" * 64, 0, 0, "n"),
        lambda tx: tx.tombstone_atom(99),
    ]
    for case in cases:
        with pytest.raises(StoreError, match="unknown atom 99"), store.commit("a", "x") as tx:
            case(tx)
    assert store.latest_csn == 1


def test_data_persists_across_reopen(tmp_path: Path) -> None:
    with Store(tmp_path) as s, s.commit("a", "x") as tx:
        tx.add_atom(AtomKind.ENTITY, "Acme", "", 4)
    with Store(tmp_path) as s:
        assert s.latest_csn == 1
        assert s.snapshot().atom(0) == Atom(0, AtomKind.ENTITY, "Acme", "", 4, 1)


def test_schema_version_mismatch_refused(tmp_path: Path) -> None:
    Store(tmp_path).close()
    conn = sqlite3.connect(tmp_path / DB_NAME)
    conn.execute("UPDATE meta SET value = 99 WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="schema version 99"):
        Store(tmp_path)
