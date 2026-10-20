"""SQLite schema for the system of record (DESIGN.md §11, decision 0003).

Append-only rules (DESIGN §13.3) are enforced by triggers, so they hold even
for code that bypasses `Store`: rows are never deleted, and the only update
allowed is closing a record by setting `csn_to` (or an atom's `tombstone_csn`)
once, from null.
"""

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE commits (
    csn          INTEGER PRIMARY KEY CHECK (csn > 0),
    committed_at TEXT    NOT NULL,
    author_id    TEXT    NOT NULL,
    summary      TEXT    NOT NULL
);

CREATE TABLE changes (
    csn      INTEGER NOT NULL REFERENCES commits(csn),
    seq      INTEGER NOT NULL,
    kind     TEXT    NOT NULL CHECK (kind IN ('atom', 'edge', 'breadcrumb')),
    atom_ids TEXT    NOT NULL,
    summary  TEXT    NOT NULL,
    PRIMARY KEY (csn, seq)
);

CREATE TABLE atoms (
    atom_id       INTEGER PRIMARY KEY CHECK (atom_id >= 0 AND atom_id < 4294967296),
    kind          TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    path_hint     TEXT    NOT NULL,
    acl_class     INTEGER NOT NULL,
    created_csn   INTEGER NOT NULL,
    source_uri    TEXT,
    tombstone_csn INTEGER CHECK (tombstone_csn IS NULL OR tombstone_csn > created_csn)
);

CREATE TABLE atom_versions (
    atom_id     INTEGER NOT NULL REFERENCES atoms(atom_id),
    version     INTEGER NOT NULL CHECK (version >= 1),
    digest      TEXT    NOT NULL,
    byte_offset INTEGER NOT NULL,
    byte_length INTEGER NOT NULL,
    normaliser  TEXT    NOT NULL,
    origin      TEXT    NOT NULL,
    csn_from    INTEGER NOT NULL,
    csn_to      INTEGER CHECK (csn_to IS NULL OR csn_to > csn_from),
    PRIMARY KEY (atom_id, version)
);
CREATE INDEX atom_versions_digest ON atom_versions(digest);

-- One row per edge record. A change closes the current record and inserts a
-- new one with the same edge_id, so edge_id is stable across reviews.
CREATE TABLE edges (
    edge_id     INTEGER NOT NULL,
    csn_from    INTEGER NOT NULL,
    csn_to      INTEGER CHECK (csn_to IS NULL OR csn_to > csn_from),
    dim         TEXT    NOT NULL,
    src         INTEGER NOT NULL REFERENCES atoms(atom_id),
    dst         INTEGER NOT NULL REFERENCES atoms(atom_id),
    weight      REAL    NOT NULL CHECK (weight > 0 AND weight <= 1),
    valid_from  TEXT,
    valid_to    TEXT,
    author_kind TEXT    NOT NULL,
    author_id   TEXT    NOT NULL,
    evidence    TEXT    NOT NULL,
    state       TEXT    NOT NULL,
    note        TEXT    NOT NULL,
    PRIMARY KEY (edge_id, csn_from)
);
CREATE INDEX edges_dim ON edges(dim, csn_from);
CREATE INDEX edges_src ON edges(src);
CREATE INDEX edges_dst ON edges(dst);
CREATE UNIQUE INDEX edges_one_current ON edges(edge_id) WHERE csn_to IS NULL;

CREATE TABLE breadcrumbs (
    breadcrumb_id INTEGER PRIMARY KEY,
    text          TEXT    NOT NULL,
    author_id     TEXT    NOT NULL,
    csn           INTEGER NOT NULL,
    acl_class     INTEGER NOT NULL
);

CREATE TABLE breadcrumb_atoms (
    breadcrumb_id INTEGER NOT NULL REFERENCES breadcrumbs(breadcrumb_id),
    seq           INTEGER NOT NULL,
    atom_id       INTEGER NOT NULL REFERENCES atoms(atom_id),
    PRIMARY KEY (breadcrumb_id, seq)
);
CREATE INDEX breadcrumb_atoms_atom ON breadcrumb_atoms(atom_id);

-- Read-sets, indexed by atom so work built on a revised atom can be found (DESIGN §10.1).
CREATE TABLE breadcrumb_reads (
    breadcrumb_id INTEGER NOT NULL REFERENCES breadcrumbs(breadcrumb_id),
    seq           INTEGER NOT NULL,
    atom_id       INTEGER NOT NULL,
    version       INTEGER NOT NULL,
    PRIMARY KEY (breadcrumb_id, seq)
);
CREATE INDEX breadcrumb_reads_atom ON breadcrumb_reads(atom_id, version);
"""

_NO_DELETE = """
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only: {table} rows cannot be deleted'); END;
"""

_NO_UPDATE = """
CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only: {table} rows cannot be updated'); END;
"""

# Closing is the one permitted update: {col} goes from null to a value and nothing else changes.
_CLOSE_ONLY = """
CREATE TRIGGER {table}_close_only BEFORE UPDATE ON {table}
WHEN OLD.{col} IS NOT NULL OR NEW.{col} IS NULL OR {others_changed}
BEGIN SELECT RAISE(ABORT, 'append-only: {table} rows can only be closed, once'); END;
"""

_CLOSABLE = {
    "atoms": (
        "tombstone_csn",
        ["atom_id", "kind", "title", "path_hint", "acl_class", "created_csn", "source_uri"],
    ),
    "atom_versions": (
        "csn_to",
        ["atom_id", "version", "digest", "byte_offset", "byte_length", "normaliser", "origin"],
    ),
    "edges": (
        "csn_to",
        [
            "edge_id",
            "csn_from",
            "dim",
            "src",
            "dst",
            "weight",
            "valid_from",
            "valid_to",
            "author_kind",
            "author_id",
            "evidence",
            "state",
            "note",
        ],
    ),
}

_IMMUTABLE = ["commits", "changes", "breadcrumbs", "breadcrumb_atoms", "breadcrumb_reads"]


def triggers() -> str:
    """SQL creating the append-only triggers."""
    parts: list[str] = []
    for table, (col, others) in _CLOSABLE.items():
        changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in others)
        parts.append(_NO_DELETE.format(table=table))
        parts.append(_CLOSE_ONLY.format(table=table, col=col, others_changed=changed))
    for table in _IMMUTABLE:
        parts.append(_NO_DELETE.format(table=table))
        parts.append(_NO_UPDATE.format(table=table))
    return "".join(parts)
