"""Lexical seeding: a Tantivy index over atom titles and text, scored with per-class BM25.

Tantivy's own BM25 uses index-wide statistics, which would let hidden documents
change scores through IDF (DESIGN §7.4). Tantivy is therefore used for
tokenising, storing and retrieving candidates, and to count statistics per
`acl_class`; the score itself is computed by `mds.seed.bm25` over only the
classes the principal can read.
"""

import json
from collections import Counter
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

import tantivy
from pyroaring import AbstractBitMap

from mds.model import AtomKind
from mds.seed import bm25
from mds.seed.bm25 import ClassStats
from mds.seed.hits import Hit, top_k
from mds.seed.text import LEXICAL_TOKENIZER, lexical_analyzer

_WRITER_HEAP = 15_000_000


def _schema() -> Any:
    sb = tantivy.SchemaBuilder()
    sb.add_integer_field("atom_id", stored=True, indexed=True, fast=True)
    sb.add_integer_field("acl_class", stored=True, indexed=True, fast=True)
    sb.add_integer_field("length", stored=True, fast=True)
    sb.add_text_field("kind", stored=True, tokenizer_name="raw")
    sb.add_text_field("body", tokenizer_name=LEXICAL_TOKENIZER, index_option="freq")
    # Term frequencies as JSON, so BM25 can be recomputed with per-class statistics.
    sb.add_text_field("tf", stored=True, tokenizer_name="raw", index_option="basic")
    return sb.build()


class LexicalIndex:
    """BM25 over atom text, titles and entity names (DESIGN §8).

    Writes are buffered until `commit`. With `path=None` the index lives in memory.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._schema = _schema()
        if path is None:
            self._index = tantivy.Index(self._schema)
        else:
            path.mkdir(parents=True, exist_ok=True)
            if tantivy.Index.exists(str(path)):
                self._index = tantivy.Index.open(str(path))
            else:
                self._index = tantivy.Index(self._schema, path=str(path))
        self._analyzer = lexical_analyzer()
        self._index.register_tokenizer(LEXICAL_TOKENIZER, self._analyzer)
        self._writer: Any = None
        self._class_cache: dict[int, tuple[int, int]] = {}
        self._df_cache: dict[tuple[int, str], int] = {}

    def tokens(self, text: str) -> list[str]:
        """Index terms for `text`, as both documents and queries are tokenised."""
        return [str(t) for t in self._analyzer.analyze(text)]

    # Writes ---------------------------------------------------------------

    def _w(self) -> Any:
        if self._writer is None:
            self._writer = self._index.writer(heap_size=_WRITER_HEAP, num_threads=1)
        return self._writer

    def add(self, atom_id: int, acl_class: int, kind: AtomKind, text: str) -> None:
        """Index an atom's text, replacing anything indexed for it before."""
        tokens = self.tokens(text)
        writer = self._w()
        writer.delete_documents_by_term("atom_id", atom_id)
        writer.add_document(
            tantivy.Document(
                atom_id=atom_id,
                acl_class=acl_class,
                length=len(tokens),
                kind=str(kind),
                body=text,
                tf=json.dumps(Counter(tokens), sort_keys=True),
            )
        )

    def remove(self, atom_id: int) -> None:
        """Drop an atom from the index (e.g. on tombstone)."""
        self._w().delete_documents_by_term("atom_id", atom_id)

    def clear(self) -> None:
        """Drop every document (buffered until `commit`)."""
        self._w().delete_all_documents()

    def commit(self) -> None:
        """Make buffered writes searchable and release the writer lock."""
        if self._writer is None:
            return
        self._writer.commit()
        self._writer.wait_merging_threads()
        self._writer = None
        self._index.reload()
        self._class_cache.clear()
        self._df_cache.clear()

    # Statistics -----------------------------------------------------------

    def _count(self, searcher: Any, query: Any) -> int:
        return int(searcher.search(query, limit=1).count)

    def _in_class(self, acl_class: int) -> Any:
        return tantivy.Query.term_query(self._schema, "acl_class", acl_class)

    def class_stats(self, acl_class: int, terms: Iterable[str]) -> ClassStats:
        """Statistics for one class, with document frequencies for `terms` only."""
        searcher = self._index.searcher()
        if acl_class not in self._class_cache:
            n_docs = self._count(searcher, self._in_class(acl_class))
            total = 0
            if n_docs:
                agg = searcher.aggregate(
                    self._in_class(acl_class), {"len": {"sum": {"field": "length"}}}
                )
                total = round(agg["len"]["value"] or 0)
            self._class_cache[acl_class] = (n_docs, total)
        n_docs, total = self._class_cache[acl_class]
        doc_freq: dict[str, int] = {}
        for term in set(terms):
            key = (acl_class, term)
            if key not in self._df_cache:
                q = tantivy.Query.boolean_query(
                    [
                        (tantivy.Occur.Must, self._in_class(acl_class)),
                        (tantivy.Occur.Must, tantivy.Query.term_query(self._schema, "body", term)),
                    ]
                )
                self._df_cache[key] = self._count(searcher, q) if n_docs else 0
            if self._df_cache[key]:
                doc_freq[term] = self._df_cache[key]
        return ClassStats(n_docs, total, doc_freq)

    # Search ---------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        classes: Collection[int],
        mask: AbstractBitMap,
        kinds: Collection[AtomKind] | None = None,
        k: int,
    ) -> list[Hit]:
        """Top `k` atoms in `mask` for `query`, scored with statistics from `classes` only.

        `classes` must be exactly the classes the principal can read, so that
        statistics come from visible atoms only.
        """
        terms = sorted(set(self.tokens(query)))
        if not terms or not classes or k < 1:
            return []
        stats = bm25.combine(self.class_stats(c, terms) for c in sorted(classes))
        clauses = [
            (
                tantivy.Occur.Must,
                tantivy.Query.boolean_query(
                    [
                        (tantivy.Occur.Should, tantivy.Query.term_query(self._schema, "body", t))
                        for t in terms
                    ]
                ),
            ),
            (
                tantivy.Occur.Must,
                tantivy.Query.term_set_query(self._schema, "acl_class", sorted(classes)),
            ),
        ]
        if kinds is not None:
            if not kinds:
                return []
            clauses.append(
                (
                    tantivy.Occur.Must,
                    tantivy.Query.term_set_query(self._schema, "kind", sorted(map(str, kinds))),
                )
            )
        q = tantivy.Query.boolean_query(clauses)
        searcher = self._index.searcher()
        n = self._count(searcher, q)
        if n == 0:
            return []
        hits: list[Hit] = []
        # Every candidate is rescored, because Tantivy's order uses index-wide statistics.
        for _, address in searcher.search(q, limit=n, count=False).hits:
            doc = searcher.doc(address)
            atom_id = int(doc["atom_id"][0])
            if atom_id not in mask:
                continue
            tf: dict[str, int] = json.loads(doc["tf"][0])
            s = bm25.score(terms, tf, int(doc["length"][0]), stats)
            if s > 0:
                hits.append(Hit(atom_id, AtomKind(doc["kind"][0]), s))
        return top_k(hits, k)
