# 0003: SQLite as the v0.1 system of record

Status: accepted · 2026-10-20

## Decision
Atoms, versions, edges, breadcrumbs, the commit log and the CSN counter live in one
SQLite database. Matrices are derived from it and rebuilt or patched on commit.

## Consequences
- One file, transactional, no server to run.
- Single writer; fine for v0.1.
- WAL, checkpoints and delta matrices are deferred (see SCOPE non-goals).
