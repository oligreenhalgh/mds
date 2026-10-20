from collections.abc import Iterator
from pathlib import Path

import pytest

from mds.model import Atom, AtomKind
from mds.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "data") as s:
        yield s


@pytest.fixture
def two_atoms(store: Store) -> tuple[Atom, Atom]:
    """A document and one section, committed at CSN 1."""
    with store.commit("test", "seed") as tx:
        doc = tx.add_atom(
            AtomKind.DOCUMENT, "policy.md", "policy.md", 0, source_uri="file:///policy.md"
        )
        sec = tx.add_atom(AtomKind.SECTION, "4.2 Retention", "policy.md › 4.2", 0)
    return doc, sec
