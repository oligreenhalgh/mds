# 0005: Naming

Status: accepted · 2026-10-20

## Context
"mds" is taken on PyPI (a Markdown Server package) and is the name of the macOS
Spotlight metadata daemon.

## Decision
Project name: MDS (Multi-Dimensional Server). PyPI distribution: `mds-server`.
Python import: `mds`. CLI: `mds-server`.

## Consequences
- No clash with Spotlight in process lists or with the existing PyPI package's
  distribution name.
- The import name `mds` would clash only if the unrelated PyPI `mds` package were
  installed in the same environment.
