"""BM25 over per-class corpus statistics (DESIGN §7.4).

Document frequency, document count and total length are kept per `acl_class`
and summed over only the classes a principal can read, so documents the
principal cannot see never change an IDF or an average length.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

K1 = 1.2
B = 0.75


@dataclass(frozen=True, slots=True)
class ClassStats:
    """Corpus statistics for the atoms of one `acl_class` (or a union of classes)."""

    n_docs: int = 0
    total_length: int = 0
    doc_freq: Mapping[str, int] = field(default_factory=dict, hash=False)

    @property
    def avg_length(self) -> float:
        """Mean document length in tokens, or 0 for an empty corpus."""
        return self.total_length / self.n_docs if self.n_docs else 0.0


def combine(stats: Iterable[ClassStats]) -> ClassStats:
    """Sum statistics over several classes. Classes are disjoint, so sums are exact."""
    n_docs = 0
    total_length = 0
    doc_freq: Counter[str] = Counter()
    for s in stats:
        n_docs += s.n_docs
        total_length += s.total_length
        doc_freq.update(s.doc_freq)
    return ClassStats(n_docs, total_length, dict(doc_freq))


def idf(n_docs: int, doc_freq: int) -> float:
    """BM25 IDF in the non-negative form Lucene and Tantivy use."""
    return math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))


def score(
    terms: Iterable[str],
    term_freq: Mapping[str, int],
    length: int,
    stats: ClassStats,
) -> float:
    """BM25 score of one document for the distinct query `terms`."""
    if stats.n_docs == 0:
        return 0.0
    norm = K1 * (1.0 - B + B * length / stats.avg_length) if stats.avg_length else K1
    total = 0.0
    for term in set(terms):
        tf = term_freq.get(term, 0)
        df = stats.doc_freq.get(term, 0)
        if tf == 0 or df == 0:
            continue
        total += idf(stats.n_docs, df) * tf * (K1 + 1.0) / (tf + norm)
    return total
