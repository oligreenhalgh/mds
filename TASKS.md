# v0.1 tasks

Each task is sized for one agent session or a short series of them. A task is
done when its acceptance checks pass and the maintainer has reviewed the
branch. Section references are to `DESIGN.md` (D) and `INTERFACES.md` (I).

Tasks in the same wave touch different modules and can run in parallel.

## Wave 0: foundations (maintainer)

- [x] **T00 Decisions.** Licence, name, stack, layout. Done (see `decisions/`).
- [x] **T01 Sample corpus.** Choose 30–100 public, licence-compatible documents
  with real containment, cross-references, revisions and amendments. Record
  sources and licences in `examples/corpus/SOURCES.md`.
- [x] **T02 Golden questions.** 30–50 questions over the corpus in
  `tests/golden/questions.yaml` (format in `tests/golden/README.md`), covering
  every dimension, `valid_at`, `as_of_csn`, conflicts and at least two
  principals.

## Wave 1: model and store

- [x] **T10 Model** (`src/mds/model/`). Dataclasses for Atom, AtomVersion,
  Edge, Breadcrumb; dimension registry loaded from
  `src/mds/model/dimensions.yaml` (D §2–4).
  Accept: unit tests for validation rules (id ranges, valid intervals, states).
- [x] **T11 Store** (`src/mds/store/`). SQLite schema, append-only writes, CSN
  counter, commit log, snapshot reads at a CSN, BLAKE3 content store (D §11).
  Accept: tests for append-only behaviour, `as_of_csn` reads, content
  round-trip.
- [x] **T12 Config and ACL** (`src/mds/acl/`). Parse `mds.toml`; intern
  `acl_class`; compute principal masks with `pyroaring`; cache and invalidate
  (D §7, I §4).
  Accept: unit tests for rule precedence and mask contents.

## Wave 2: ingestion, engine, seeding

- [x] **T20 Parsing and normalisation** (`src/mds/ingest/parse.py`). Docling
  for PDF and DOCX; Markdown, reStructuredText and text natively. Produce
  derivative text, section tree and origin map (D §2.1).
- [ ] **T21 Containment and re-anchoring** (`src/mds/ingest/`). `contains`
  edges from the section tree; heading-path and MinHash matching on revision;
  `derived_from` for splits and merges (D §2.2).
- [ ] **T22 References extractor.** Pattern rules and resolution order (D §9).
  Accept: precision and recall reported on fixtures.
- [ ] **T23 Supersedes extractor.** Amendment phrase rules, target resolution,
  effective dates (D §9).
  Accept: precision and recall reported on fixtures.
- [x] **T24 Entities and co_occurs.** Entity spans (dictionary first, GLiNER
  optional), `E`, IDF, pruning (D §3.3).
- [ ] **T25 Engine** (`src/mds/engine/`). Build matrices from the store; vertex
  and evidence masks; valid-time edge mask; four operators; typed paths;
  `top_k` and degree cap; ranking (D §5).
  Accept: tests against hand-computed results on small graphs; every
  operator; masks proven to exclude hidden atoms from scores.
- [ ] **T26 in_force** (`src/mds/engine/inforce.py`). Chains, unchanged,
  conflicts (D §5.5).
- [ ] **T27 Seeding** (`src/mds/seed/`). Tantivy BM25 with per-class
  statistics; small dense index; entity match; reciprocal rank fusion; masking
  (D §8).

## Wave 3: interfaces and blackboard

- [ ] **T30 Python API** (`src/mds/api.py`). `Server`, `Session`, all
  operations in I §6.
- [ ] **T31 Blackboard** (`src/mds/blackboard/`). Assertions with trust and
  default states; breadcrumbs with read-sets; change feed with cone filter;
  review (D §10).
- [ ] **T32 MCP server** (`src/mds/agents/`). Every tool in I §2 with exact
  names, inputs, outputs and error codes; principal fixed per session.
- [ ] **T33 CLI** (`src/mds/cli.py`). Every command in I §3.

## Wave 4: proof

- [ ] **T40 Leak suite** (`tests/leak/`). One test per channel in D §7.4, plus
  every read path.
- [ ] **T41 Golden runner.** Runs T02 questions through the API and reports
  hit rate per dimension and category.
- [ ] **T42 Benchmark** (`benchmarks/`). Same agent, same questions: MDS tools
  vs file search vs plain vector retrieval. Results in `benchmarks/RESULTS.md`.
- [ ] **T43 Quickstart and packaging.** Clean-checkout quickstart under 10
  minutes; `uv tool install` works; example MCP client configs.
