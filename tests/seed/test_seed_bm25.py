import math

import pytest

from mds.seed import ClassStats
from mds.seed.bm25 import combine, idf, score


def test_combine_sums_counts_lengths_and_doc_freqs() -> None:
    a = ClassStats(2, 10, {"x": 1, "y": 2})
    b = ClassStats(3, 20, {"x": 3})
    c = combine([a, b])
    assert (c.n_docs, c.total_length) == (5, 30)
    assert c.doc_freq == {"x": 4, "y": 2}
    assert c.avg_length == 6.0


def test_combine_of_nothing_is_empty() -> None:
    c = combine([])
    assert (c.n_docs, c.total_length, dict(c.doc_freq), c.avg_length) == (0, 0, {}, 0.0)


def test_idf_falls_as_doc_freq_rises_and_stays_positive() -> None:
    assert idf(10, 1) > idf(10, 5) > idf(10, 10) > 0
    assert idf(10, 1) == pytest.approx(math.log(1 + 9.5 / 1.5))


def test_score_uses_only_the_given_statistics() -> None:
    tf = {"retent": 2, "polici": 1}
    narrow = ClassStats(2, 10, {"retent": 1, "polici": 2})
    wide = ClassStats(3, 13, {"retent": 2, "polici": 2})
    assert score(["retent"], tf, 5, narrow) != score(["retent"], tf, 5, wide)
    assert score(["retent"], tf, 5, narrow) == score(["retent"], tf, 5, narrow)


def test_score_ignores_repeated_and_absent_terms() -> None:
    stats = ClassStats(2, 10, {"a": 1})
    assert score(["a", "a"], {"a": 1}, 5, stats) == score(["a"], {"a": 1}, 5, stats)
    assert score(["b"], {"b": 3}, 5, stats) == 0.0
    assert score(["a"], {"a": 1}, 5, ClassStats()) == 0.0


def test_score_rewards_term_frequency_and_short_documents() -> None:
    stats = ClassStats(4, 40, {"a": 2})
    assert score(["a"], {"a": 3}, 10, stats) > score(["a"], {"a": 1}, 10, stats)
    assert score(["a"], {"a": 1}, 5, stats) > score(["a"], {"a": 1}, 20, stats)
