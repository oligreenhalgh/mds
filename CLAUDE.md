# Working on MDS

Instructions for every agent session in this repository. Read this, then the
documents it points to, before writing code.

## What this is

MDS (Multi-Dimensional Server) is an agent-driven filing system: information is
filed as a multi-dimensional typed graph, navigated and extended by agents over
MCP, with time and permissions applied to every read. Python-first prototype.
Licence: Apache 2.0.

## Source of truth

| File | Role | May an agent change it? |
| --- | --- | --- |
| `DESIGN.md` | Data model and invariants | No. Propose changes in your summary; the maintainer edits. |
| `INTERFACES.md` | Tool, CLI, config and API contracts | No, same as above. |
| `SCOPE.md` | What v0.1 includes and excludes | No. |
| `decisions/` | Why key choices were made | Add a new record only when asked. |
| `TASKS.md` | Task breakdown | Tick your own task's boxes only. |

If code and these documents disagree, the documents win. If a document seems
wrong or incomplete, stop and say so instead of working around it.

## Commands

```bash
uv sync                      # install dependencies
uv run pytest                # all tests
uv run pytest tests/engine   # one area
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src              # type check
uv run mds-server --help     # CLI
```

Run tests, lint and type checks before saying a task is done, and report the
results as they are, including failures.

## Layout

```
src/mds/
  model/       atoms, versions, edges, dimensions registry (pure data, no I/O)
  store/       SQLite store, content store, CSN and commit log
  ingest/      parsing, normalisation, re-anchoring, extractors per dimension
  engine/      matrices, masks, operators, traversal, in_force, ranking
  seed/        lexical, dense and entity seeding; rank fusion
  acl/         principals, classes, mask computation
  blackboard/  assertions, breadcrumbs, read-sets, change feed, review
  agents/      MCP server and tool definitions
  api.py       Python API (Server, Session)
  cli.py       mds-server command
tests/
  unit tests mirror src/mds/ ; golden/ ; leak/
examples/corpus/   public sample documents
```

Dependencies flow downwards only: `model` ← `store` ← (`ingest`, `engine`,
`seed`, `acl`, `blackboard`) ← `api` ← (`agents`, `cli`). Never import upwards.

## Rules

- Stay inside your task's module. Touching another module needs a reason in
  your summary.
- Ask before adding a dependency. Every dependency must be Apache 2.0
  compatible (MIT, BSD, Apache, ISC, PSF). No GPL or AGPL.
- No network access at runtime, and none in tests. Models are loaded from a
  local cache; tests use fakes.
- No LLM calls inside MDS.
- Permissions are never optional: every read path goes through the principal's
  masks. A test in `tests/leak/` must cover any new read path.
- Edge and version records are append-only (DESIGN §13).
- Type hints on all public functions. Docstrings on public classes and
  functions; comments only where the why isn't obvious.
- Tests for every behaviour you add. Use the small fixtures in
  `tests/fixtures/`; don't depend on the full sample corpus in unit tests.
- Never commit to `main` directly. Work on a branch named after the task
  (`t05-engine-traverse`) and summarise what you did, what you didn't, and
  test results.

## Style

- Python 3.12+, `ruff` defaults with line length 100.
- Plain names that match `DESIGN.md` terms: `atom_id`, `dim`, `csn`,
  `valid_from`, `principal`, `mask`.
- Prefer small pure functions in `engine/` and `model/`; keep I/O in `store/`.
