"""The SQLite system of record: commits, CSN counter and snapshot reads (DESIGN.md §6, §11).

Writes go through `Store.commit()`, which hands out a `Transaction` holding the
next CSN. Reads go through `Store.snapshot()`, which pins a CSN and sees exactly
the records current at that commit.

These reads are unmasked. The store sits below `acl/` and `engine/`, which
apply principal masks before anything reaches an agent (CLAUDE.md layout).
"""

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mds.model import (
    Atom,
    AtomKind,
    AtomVersion,
    AuthorKind,
    Breadcrumb,
    Edge,
    EdgeState,
    ReadSetEntry,
    default_registry,
)
from mds.model.errors import as_enum
from mds.store.content import ContentStore
from mds.store.schema import SCHEMA, SCHEMA_VERSION, triggers

DB_NAME = "mds.sqlite"
CONTENT_DIR = "content"


class StoreError(Exception):
    """A write would break a store rule (unknown atom, closed record, bad CSN)."""


@dataclass(frozen=True, slots=True)
class Commit:
    """One entry in the commit log."""

    csn: int
    committed_at: datetime
    author_id: str
    summary: str


@dataclass(frozen=True, slots=True)
class Change:
    """One change inside a commit, as the change feed reports it (INTERFACES `mds_changes`)."""

    csn: int
    kind: str
    atom_ids: tuple[int, ...]
    summary: str


class Store:
    """The system of record in one data directory: `mds.sqlite` plus `content/`.

    One writer at a time; readers use snapshots.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.content = ContentStore(self.data_dir / CONTENT_DIR)
        # Transactions are managed explicitly (BEGIN IMMEDIATE / COMMIT).
        self._conn = sqlite3.connect(self.data_dir / DB_NAME, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._in_commit = False
        self._init_schema()

    def _init_schema(self) -> None:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if row is None:
            self._conn.executescript(
                "BEGIN IMMEDIATE;"
                + SCHEMA
                + triggers()
                + f"INSERT INTO meta (key, value) VALUES ('schema_version', {SCHEMA_VERSION});"
                + "INSERT INTO meta (key, value) VALUES ('csn', 0);"
                + "COMMIT;"
            )
            return
        version = self._meta("schema_version")
        if version != SCHEMA_VERSION:
            raise StoreError(f"store schema version {version}, expected {SCHEMA_VERSION}")

    def _meta(self, key: str) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return int(row["value"])

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def latest_csn(self) -> int:
        """The CSN of the most recent commit (0 for an empty store)."""
        return self._meta("csn")

    @contextmanager
    def commit(self, author_id: str, summary: str) -> Iterator["Transaction"]:
        """Open a write transaction at the next CSN.

        Everything written inside the block commits atomically with one CSN; an
        exception rolls it all back and the CSN is not used. A block that writes
        nothing commits nothing.
        """
        if not author_id:
            raise StoreError("a commit needs an author_id")
        if self._in_commit:
            raise StoreError("a commit is already open; MDS has one writer")
        self._in_commit = True
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            tx = Transaction(self, self.latest_csn + 1)
            try:
                yield tx
                if tx.changes:
                    self._finish(tx, author_id, summary)
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute("ROLLBACK")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            finally:
                tx._closed = True
        finally:
            self._in_commit = False

    def _finish(self, tx: "Transaction", author_id: str, summary: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO commits (csn, committed_at, author_id, summary) VALUES (?, ?, ?, ?)",
            (tx.csn, now, author_id, summary),
        )
        self._conn.executemany(
            "INSERT INTO changes (csn, seq, kind, atom_ids, summary) VALUES (?, ?, ?, ?, ?)",
            [
                (tx.csn, seq, c.kind, json.dumps(list(c.atom_ids)), c.summary)
                for seq, c in enumerate(tx.changes)
            ],
        )
        self._conn.execute("UPDATE meta SET value = ? WHERE key = 'csn'", (tx.csn,))

    def snapshot(self, as_of_csn: int | None = None) -> "Snapshot":
        """A read view at `as_of_csn` (default: the latest commit)."""
        latest = self.latest_csn
        csn = latest if as_of_csn is None else as_of_csn
        if not 0 <= csn <= latest:
            raise StoreError(f"as_of_csn must be in [0, {latest}], got {csn}")
        return Snapshot(self._conn, csn)

    def commits(self, since_csn: int = 0) -> list[Commit]:
        """Commits after `since_csn`, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM commits WHERE csn > ? ORDER BY csn", (since_csn,)
        ).fetchall()
        return [
            Commit(
                csn=r["csn"],
                committed_at=datetime.fromisoformat(r["committed_at"]),
                author_id=r["author_id"],
                summary=r["summary"],
            )
            for r in rows
        ]

    def changes(self, since_csn: int = 0) -> list[Change]:
        """Changes in commits after `since_csn`, in commit order."""
        rows = self._conn.execute(
            "SELECT * FROM changes WHERE csn > ? ORDER BY csn, seq", (since_csn,)
        ).fetchall()
        return [
            Change(
                csn=r["csn"],
                kind=r["kind"],
                atom_ids=tuple(json.loads(r["atom_ids"])),
                summary=r["summary"],
            )
            for r in rows
        ]


class Transaction:
    """Writes for one commit. Every record written here gets this transaction's CSN."""

    def __init__(self, store: Store, csn: int) -> None:
        self._store = store
        self._conn = store._conn
        self.csn = csn
        self.changes: list[Change] = []
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            raise StoreError("transaction is finished")

    def _log(self, kind: str, atom_ids: Sequence[int], summary: str) -> None:
        self.changes.append(Change(self.csn, kind, tuple(atom_ids), summary))

    def _next_id(self, table: str, column: str) -> int:
        row = self._conn.execute(f"SELECT MAX({column}) AS m FROM {table}").fetchone()
        return 0 if row["m"] is None else int(row["m"]) + 1

    def _require_atoms(self, atom_ids: Sequence[int]) -> None:
        for atom_id in set(atom_ids):
            row = self._conn.execute("SELECT 1 FROM atoms WHERE atom_id = ?", (atom_id,)).fetchone()
            if row is None:
                raise StoreError(f"unknown atom {atom_id}")

    # Atoms ----------------------------------------------------------------

    def add_atom(
        self,
        kind: AtomKind | str,
        title: str,
        path_hint: str,
        acl_class: int,
        source_uri: str | None = None,
    ) -> Atom:
        """Create an atom with the next unused `atom_id`."""
        self._check_open()
        atom = Atom(
            atom_id=self._next_id("atoms", "atom_id"),
            kind=as_enum(AtomKind, kind, "kind"),
            title=title,
            path_hint=path_hint,
            acl_class=acl_class,
            created_csn=self.csn,
            source_uri=source_uri,
        )
        self._conn.execute(
            "INSERT INTO atoms (atom_id, kind, title, path_hint, acl_class, created_csn,"
            " source_uri) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                atom.atom_id,
                atom.kind.value,
                atom.title,
                atom.path_hint,
                atom.acl_class,
                atom.created_csn,
                atom.source_uri,
            ),
        )
        self._log("atom", [atom.atom_id], f"added {atom.kind} {atom.title!r}")
        return atom

    def tombstone_atom(self, atom_id: int) -> None:
        """Record that an atom disappeared from its source. Its id is never reused."""
        self._check_open()
        row = self._conn.execute(
            "SELECT created_csn, tombstone_csn FROM atoms WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown atom {atom_id}")
        if row["tombstone_csn"] is not None:
            raise StoreError(f"atom {atom_id} is already tombstoned")
        if row["created_csn"] == self.csn:
            raise StoreError(f"atom {atom_id} was created in this commit")
        self._conn.execute(
            "UPDATE atoms SET tombstone_csn = ? WHERE atom_id = ?", (self.csn, atom_id)
        )
        self._log("atom", [atom_id], "tombstoned")

    # Atom versions --------------------------------------------------------

    def add_version(
        self,
        atom_id: int,
        digest: str,
        byte_offset: int,
        byte_length: int,
        normaliser: str,
        origin: Mapping[str, Any] | None = None,
    ) -> AtomVersion:
        """Add the next version of an atom, closing its current version at this CSN.

        The derivative must already be in the content store and contain the byte range.
        """
        self._check_open()
        self._require_atoms([atom_id])
        content = self._store.content
        if not content.has(digest):
            raise StoreError(f"derivative {digest} is not in the content store")
        if byte_offset + byte_length > content.size(digest):
            raise StoreError(f"byte range {byte_offset}+{byte_length} is outside {digest}")
        current = self._conn.execute(
            "SELECT version, csn_from FROM atom_versions WHERE atom_id = ? AND csn_to IS NULL",
            (atom_id,),
        ).fetchone()
        number = 1
        if current is not None:
            if current["csn_from"] == self.csn:
                raise StoreError(f"atom {atom_id} already has a new version in this commit")
            number = current["version"] + 1
            self._conn.execute(
                "UPDATE atom_versions SET csn_to = ? WHERE atom_id = ? AND version = ?",
                (self.csn, atom_id, current["version"]),
            )
        version = AtomVersion(
            atom_id=atom_id,
            version=number,
            digest=digest,
            byte_offset=byte_offset,
            byte_length=byte_length,
            normaliser=normaliser,
            csn_from=self.csn,
            origin=dict(origin or {}),
        )
        self._conn.execute(
            "INSERT INTO atom_versions (atom_id, version, digest, byte_offset, byte_length,"
            " normaliser, origin, csn_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                atom_id,
                number,
                digest,
                byte_offset,
                byte_length,
                normaliser,
                json.dumps(version.origin, sort_keys=True),
                self.csn,
            ),
        )
        self._log("atom", [atom_id], f"version {number}")
        return version

    # Edges ----------------------------------------------------------------

    def add_edge(
        self,
        dim: str,
        src: int,
        dst: int,
        weight: float,
        author_kind: AuthorKind | str,
        author_id: str,
        state: EdgeState | str,
        valid_from: date | None = None,
        valid_to: date | None = None,
        evidence: Sequence[int] | None = None,
        note: str = "",
    ) -> Edge:
        """Create a new edge with the next unused `edge_id`."""
        self._check_open()
        edge = Edge(
            edge_id=self._next_id("edges", "edge_id"),
            dim=dim,
            src=src,
            dst=dst,
            weight=weight,
            author_kind=as_enum(AuthorKind, author_kind, "author_kind"),
            author_id=author_id,
            state=as_enum(EdgeState, state, "state"),
            csn_from=self.csn,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence=None if evidence is None else tuple(evidence),
            note=note,
        )
        self._insert_edge(edge)
        self._log("edge", [edge.src, edge.dst], f"added {_dim_name(edge.dim)} edge {edge.edge_id}")
        return edge

    def replace_edge(self, edge_id: int, /, **changes: Any) -> Edge:
        """Close the current record of an edge and start a new one with `changes` applied.

        The edge keeps its `edge_id`. Use this to confirm or reject an assertion,
        or to correct its dates or weight. `dim`, `src` and `dst` cannot change.
        """
        self._check_open()
        fixed = {"edge_id", "dim", "src", "dst", "csn_from", "csn_to"} & changes.keys()
        if fixed:
            raise StoreError(f"cannot change {', '.join(sorted(fixed))} of an edge")
        current = self._close_edge(edge_id)
        edge = replace(current, csn_from=self.csn, csn_to=None, **changes)
        self._insert_edge(edge)
        self._log("edge", [edge.src, edge.dst], f"changed edge {edge_id}")
        return edge

    def close_edge(self, edge_id: int) -> None:
        """End an edge at this CSN without a replacement (for example, a retired extractor edge)."""
        self._check_open()
        edge = self._close_edge(edge_id)
        self._log("edge", [edge.src, edge.dst], f"closed edge {edge_id}")

    def _close_edge(self, edge_id: int) -> Edge:
        row = self._conn.execute(
            "SELECT * FROM edges WHERE edge_id = ? AND csn_to IS NULL", (edge_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"edge {edge_id} has no current record")
        if row["csn_from"] == self.csn:
            raise StoreError(f"edge {edge_id} was already written in this commit")
        self._conn.execute(
            "UPDATE edges SET csn_to = ? WHERE edge_id = ? AND csn_from = ?",
            (self.csn, edge_id, row["csn_from"]),
        )
        return _edge(row, None)

    def _insert_edge(self, edge: Edge) -> None:
        assert edge.evidence is not None
        self._require_atoms([edge.src, edge.dst, *edge.evidence])
        self._conn.execute(
            "INSERT INTO edges (edge_id, csn_from, dim, src, dst, weight, valid_from, valid_to,"
            " author_kind, author_id, evidence, state, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                edge.edge_id,
                edge.csn_from,
                edge.dim,
                edge.src,
                edge.dst,
                edge.weight,
                _iso(edge.valid_from),
                _iso(edge.valid_to),
                edge.author_kind.value,
                edge.author_id,
                json.dumps(list(edge.evidence)),
                edge.state.value,
                edge.note,
            ),
        )

    # Breadcrumbs ----------------------------------------------------------

    def add_breadcrumb(
        self,
        atom_ids: Sequence[int],
        text: str,
        author_id: str,
        acl_class: int,
        read_set: Sequence[ReadSetEntry] = (),
    ) -> Breadcrumb:
        """Pin a note to atoms, with the read-set it was based on."""
        self._check_open()
        crumb = Breadcrumb(
            breadcrumb_id=self._next_id("breadcrumbs", "breadcrumb_id"),
            atom_ids=tuple(atom_ids),
            text=text,
            author_id=author_id,
            csn=self.csn,
            acl_class=acl_class,
            read_set=tuple(read_set),
        )
        self._require_atoms(crumb.atom_ids)
        self._conn.execute(
            "INSERT INTO breadcrumbs (breadcrumb_id, text, author_id, csn, acl_class)"
            " VALUES (?, ?, ?, ?, ?)",
            (crumb.breadcrumb_id, crumb.text, crumb.author_id, crumb.csn, crumb.acl_class),
        )
        self._conn.executemany(
            "INSERT INTO breadcrumb_atoms (breadcrumb_id, seq, atom_id) VALUES (?, ?, ?)",
            [(crumb.breadcrumb_id, i, a) for i, a in enumerate(crumb.atom_ids)],
        )
        self._conn.executemany(
            "INSERT INTO breadcrumb_reads (breadcrumb_id, seq, atom_id, version)"
            " VALUES (?, ?, ?, ?)",
            [(crumb.breadcrumb_id, i, r.atom_id, r.version) for i, r in enumerate(crumb.read_set)],
        )
        self._log("breadcrumb", crumb.atom_ids, f"breadcrumb {crumb.breadcrumb_id}")
        return crumb


class Snapshot:
    """Records as they stood at one CSN.

    A record is visible when `csn_from <= csn` and it was not closed by then.
    Closing CSNs and tombstones later than the snapshot are reported as null,
    because at that commit they had not happened.
    """

    def __init__(self, conn: sqlite3.Connection, csn: int) -> None:
        self._conn = conn
        self.csn = csn

    # Atoms ----------------------------------------------------------------

    def atom(self, atom_id: int) -> Atom | None:
        """The atom with this id, if it existed and was not tombstoned at this CSN."""
        row = self._conn.execute(
            "SELECT * FROM atoms WHERE atom_id = ? AND created_csn <= ?"
            " AND (tombstone_csn IS NULL OR tombstone_csn > ?)",
            (atom_id, self.csn, self.csn),
        ).fetchone()
        return None if row is None else _atom(row, self.csn)

    def atoms(self, include_tombstoned: bool = False) -> list[Atom]:
        """All atoms present at this CSN, by `atom_id`."""
        sql = "SELECT * FROM atoms WHERE created_csn <= ?"
        params: tuple[int, ...] = (self.csn,)
        if not include_tombstoned:
            sql += " AND (tombstone_csn IS NULL OR tombstone_csn > ?)"
            params = (self.csn, self.csn)
        rows = self._conn.execute(sql + " ORDER BY atom_id", params).fetchall()
        return [_atom(r, self.csn) for r in rows]

    # Versions -------------------------------------------------------------

    def version(self, atom_id: int) -> AtomVersion | None:
        """The atom's version current at this CSN."""
        row = self._conn.execute(
            "SELECT * FROM atom_versions WHERE atom_id = ? AND " + _CURRENT,
            (atom_id, self.csn, self.csn),
        ).fetchone()
        return None if row is None else _version(row, self.csn)

    def versions(self, atom_id: int) -> list[AtomVersion]:
        """Every version of the atom recorded by this CSN, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM atom_versions WHERE atom_id = ? AND csn_from <= ? ORDER BY version",
            (atom_id, self.csn),
        ).fetchall()
        return [_version(r, self.csn) for r in rows]

    # Edges ----------------------------------------------------------------

    def edge(self, edge_id: int) -> Edge | None:
        """The record of this edge current at this CSN."""
        row = self._conn.execute(
            "SELECT * FROM edges WHERE edge_id = ? AND " + _CURRENT,
            (edge_id, self.csn, self.csn),
        ).fetchone()
        return None if row is None else _edge(row, self.csn)

    def edges(self, dim: str | None = None) -> list[Edge]:
        """Edge records current at this CSN, optionally in one dimension (id or name)."""
        sql = "SELECT * FROM edges WHERE " + _CURRENT
        params: list[Any] = [self.csn, self.csn]
        if dim is not None:
            sql += " AND dim = ?"
            params.append(default_registry().get(dim).id)
        rows = self._conn.execute(sql + " ORDER BY edge_id", params).fetchall()
        return [_edge(r, self.csn) for r in rows]

    def edge_history(self, edge_id: int) -> list[Edge]:
        """Every record of this edge written by this CSN, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE edge_id = ? AND csn_from <= ? ORDER BY csn_from",
            (edge_id, self.csn),
        ).fetchall()
        return [_edge(r, self.csn) for r in rows]

    # Breadcrumbs ----------------------------------------------------------

    def breadcrumbs(self, atom_id: int | None = None) -> list[Breadcrumb]:
        """Breadcrumbs posted by this CSN, optionally only those pinned to `atom_id`."""
        sql = "SELECT * FROM breadcrumbs WHERE csn <= ?"
        params: list[int] = [self.csn]
        if atom_id is not None:
            sql += (
                " AND breadcrumb_id IN"
                " (SELECT breadcrumb_id FROM breadcrumb_atoms WHERE atom_id = ?)"
            )
            params.append(atom_id)
        rows = self._conn.execute(sql + " ORDER BY breadcrumb_id", params).fetchall()
        return [self._breadcrumb(r) for r in rows]

    def breadcrumbs_reading(self, atom_id: int) -> list[Breadcrumb]:
        """Breadcrumbs whose read-set includes any version of `atom_id` (DESIGN §10.1)."""
        rows = self._conn.execute(
            "SELECT * FROM breadcrumbs WHERE csn <= ? AND breadcrumb_id IN"
            " (SELECT breadcrumb_id FROM breadcrumb_reads WHERE atom_id = ?)"
            " ORDER BY breadcrumb_id",
            (self.csn, atom_id),
        ).fetchall()
        return [self._breadcrumb(r) for r in rows]

    def _breadcrumb(self, row: sqlite3.Row) -> Breadcrumb:
        bid = row["breadcrumb_id"]
        pins = self._conn.execute(
            "SELECT atom_id FROM breadcrumb_atoms WHERE breadcrumb_id = ? ORDER BY seq", (bid,)
        ).fetchall()
        reads = self._conn.execute(
            "SELECT atom_id, version FROM breadcrumb_reads WHERE breadcrumb_id = ? ORDER BY seq",
            (bid,),
        ).fetchall()
        return Breadcrumb(
            breadcrumb_id=bid,
            atom_ids=tuple(p["atom_id"] for p in pins),
            text=row["text"],
            author_id=row["author_id"],
            csn=row["csn"],
            acl_class=row["acl_class"],
            read_set=tuple(ReadSetEntry(r["atom_id"], r["version"]) for r in reads),
        )


_CURRENT = "csn_from <= ? AND (csn_to IS NULL OR csn_to > ?)"


def _as_of(value: int | None, csn: int | None) -> int | None:
    """Hide a closing CSN that lies after the snapshot."""
    if value is None or csn is None or value <= csn:
        return value
    return None


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _dim_name(dim: str) -> str:
    return default_registry().get(dim).name


def _atom(row: sqlite3.Row, csn: int) -> Atom:
    return Atom(
        atom_id=row["atom_id"],
        kind=AtomKind(row["kind"]),
        title=row["title"],
        path_hint=row["path_hint"],
        acl_class=row["acl_class"],
        created_csn=row["created_csn"],
        source_uri=row["source_uri"],
        tombstone_csn=_as_of(row["tombstone_csn"], csn),
    )


def _version(row: sqlite3.Row, csn: int) -> AtomVersion:
    return AtomVersion(
        atom_id=row["atom_id"],
        version=row["version"],
        digest=row["digest"],
        byte_offset=row["byte_offset"],
        byte_length=row["byte_length"],
        normaliser=row["normaliser"],
        csn_from=row["csn_from"],
        csn_to=_as_of(row["csn_to"], csn),
        origin=json.loads(row["origin"]),
    )


def _edge(row: sqlite3.Row, csn: int | None) -> Edge:
    return Edge(
        edge_id=row["edge_id"],
        dim=row["dim"],
        src=row["src"],
        dst=row["dst"],
        weight=row["weight"],
        author_kind=AuthorKind(row["author_kind"]),
        author_id=row["author_id"],
        state=EdgeState(row["state"]),
        csn_from=row["csn_from"],
        csn_to=_as_of(row["csn_to"], csn),
        valid_from=_date(row["valid_from"]),
        valid_to=_date(row["valid_to"]),
        evidence=tuple(json.loads(row["evidence"])),
        note=row["note"],
    )
