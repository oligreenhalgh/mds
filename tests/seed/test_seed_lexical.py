from pathlib import Path

import pytest
from pyroaring import BitMap

from mds.model import AtomKind
from mds.seed import LexicalIndex

S = AtomKind.SECTION


@pytest.fixture
def index() -> LexicalIndex:
    lx = LexicalIndex()
    lx.add(1, 0, S, "4.2 Retention\nPersonal data is retained for six years.")
    lx.add(2, 0, S, "4.3 Deletion\nRecords are deleted after the retention period ends.")
    lx.add(3, 0, AtomKind.DOCUMENT, "Holiday policy")
    lx.add(4, 1, S, "Retention retention retention: legal hold on Acme records.")
    lx.commit()
    return lx


ALL = BitMap([1, 2, 3, 4])


def test_ranks_by_bm25_and_stems_terms(index: LexicalIndex) -> None:
    hits = index.search("retention data", classes={0}, mask=BitMap([1, 2, 3]), k=10)
    assert [h.atom_id for h in hits] == [1, 2]
    assert hits[0].score > hits[1].score > 0
    assert hits[0].kind is S
    # "retaining" and "retained" share a stem.
    assert [h.atom_id for h in index.search("retaining", classes={0}, mask=ALL, k=10)] == [1]


def test_results_are_limited_to_mask(index: LexicalIndex) -> None:
    hits = index.search("retention", classes={0, 1}, mask=BitMap([2, 4]), k=10)
    assert {h.atom_id for h in hits} == {2, 4}


def test_results_are_limited_to_classes(index: LexicalIndex) -> None:
    hits = index.search("retention", classes={0}, mask=ALL, k=10)
    assert 4 not in {h.atom_id for h in hits}


def test_class_stats_are_per_class(index: LexicalIndex) -> None:
    term = index.tokens("retention")[0]
    s0 = index.class_stats(0, [term])
    s1 = index.class_stats(1, [term])
    assert (s0.n_docs, s0.doc_freq[term]) == (3, 2)
    assert (s1.n_docs, s1.doc_freq[term]) == (1, 1)
    assert s0.total_length + s1.total_length == sum(
        len(index.tokens(t))
        for t in [
            "4.2 Retention\nPersonal data is retained for six years.",
            "4.3 Deletion\nRecords are deleted after the retention period ends.",
            "Holiday policy",
            "Retention retention retention: legal hold on Acme records.",
        ]
    )


def test_kinds_filter(index: LexicalIndex) -> None:
    assert index.search("policy", classes={0}, mask=ALL, kinds={S}, k=10) == []
    hits = index.search("policy", classes={0}, mask=ALL, kinds={AtomKind.DOCUMENT}, k=10)
    assert [h.atom_id for h in hits] == [3]
    assert index.search("policy", classes={0}, mask=ALL, kinds=set(), k=10) == []


def test_k_truncates(index: LexicalIndex) -> None:
    assert len(index.search("retention", classes={0, 1}, mask=ALL, k=1)) == 1


def test_query_without_terms_or_classes_finds_nothing(index: LexicalIndex) -> None:
    assert index.search("!!!", classes={0}, mask=ALL, k=10) == []
    assert index.search("retention", classes=set(), mask=ALL, k=10) == []
    assert index.search("nonexistentword", classes={0}, mask=ALL, k=10) == []


def test_re_adding_replaces_and_remove_deletes(index: LexicalIndex) -> None:
    index.add(3, 0, AtomKind.DOCUMENT, "Retention schedule")
    index.remove(2)
    index.commit()
    hits = index.search("retention", classes={0}, mask=ALL, k=10)
    assert {h.atom_id for h in hits} == {1, 3}
    assert index.search("holiday", classes={0}, mask=ALL, k=10) == []
    assert index.class_stats(0, []).n_docs == 2


def test_writes_are_invisible_until_commit(index: LexicalIndex) -> None:
    index.add(5, 0, S, "zebra crossing")
    assert index.search("zebra", classes={0}, mask=BitMap([5]), k=10) == []
    index.commit()
    assert [h.atom_id for h in index.search("zebra", classes={0}, mask=BitMap([5]), k=10)] == [5]


def test_on_disk_index_reopens(tmp_path: Path) -> None:
    lx = LexicalIndex(tmp_path / "seed")
    lx.add(7, 0, S, "Supplier onboarding checklist")
    lx.commit()
    reopened = LexicalIndex(tmp_path / "seed")
    hits = reopened.search("onboarding", classes={0}, mask=BitMap([7]), k=10)
    assert [h.atom_id for h in hits] == [7]
    assert hits[0].score == lx.search("onboarding", classes={0}, mask=BitMap([7]), k=10)[0].score


def test_clear_drops_everything(index: LexicalIndex) -> None:
    index.clear()
    index.commit()
    assert index.search("retention", classes={0, 1}, mask=ALL, k=10) == []
