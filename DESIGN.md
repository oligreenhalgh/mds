# MDS design

MDS (Multi-Dimensional Server) is an agent-driven filing system. Information is
filed as a graph with several typed dimensions instead of a folder tree. Agents
read it, navigate it and add to it while they work. Every query respects time
and the permissions of whoever the agent is acting for.

This document is the source of truth for the data model. Code implements it;
it does not change it. Changes to this file need explicit approval from the
maintainer (see `CLAUDE.md`).

Status: draft for v0.1. Sections marked **(later)** describe intended
behaviour that v0.1 does not implement.

---

## 1. Concepts at a glance

| Concept | One line |
| --- | --- |
| Atom | A unit being filed: a document, a section, a clause, an item. Has a stable id. |
| Atom version | One revision of an atom's content, stored by content address. |
| Dimension | A named kind of relationship with its own meaning and rules. |
| Edge | A typed, directed, dated, attributed link between two atoms in one dimension. |
| Principal | Who an agent is acting for. Determines what is visible. |
| Mask | The set of atoms (and derived edges) a principal may see. |
| View | A saved traversal. The multi-dimensional equivalent of a folder. |
| CSN | Commit Sequence Number. Transaction time: when MDS learned something. |
| Valid time | When a fact is true in the world, e.g. when an amendment takes effect. |
| Blackboard | Where agents leave assertions, breadcrumbs and subscriptions. |

---

## 2. Atoms

An **atom** is anything worth addressing on its own.

| Field | Type | Notes |
| --- | --- | --- |
| `atom_id` | int, 0 ≤ id < 2³² | Stable for the atom's whole life. Also its matrix row/column index. |
| `kind` | enum | `document`, `section`, `item`, `entity`, `placeholder` |
| `source_uri` | str | Where the original lives (`file:///…` in v0.1). Documents only. |
| `title` | str | Heading text or file name. |
| `path_hint` | str | Human-readable location, e.g. `policy-2021.pdf › 4 › 4.2`. |
| `acl_class` | int | Interned permission class (section 7). |
| `created_csn` | int | When the atom first appeared. |
| `tombstone_csn` | int or null | When it disappeared from the source, if it did. |

Rules:

- `atom_id`s are never reused.
- `entity` atoms represent named things (a party, a project, a place). They have
  no content of their own; they exist to anchor `co_occurs` edges.
- `placeholder` atoms stand for something referenced but not held (e.g. "the
  Standard dated 1 May 2019" when that file is not ingested). A later ingest can
  resolve a placeholder into a real atom; edges then move to the real atom.

### 2.1 Atom versions and content

Each revision of an atom's content is an **atom version**.

| Field | Type | Notes |
| --- | --- | --- |
| `atom_id` | int | |
| `version` | int | 1, 2, 3 … per atom |
| `digest` | str | BLAKE3 hex of the normalised text derivative of the containing document |
| `byte_offset`, `byte_length` | int | UTF-8 byte range inside that derivative |
| `normaliser` | str | Normaliser name and version, e.g. `docling-2.x/norm-1` |
| `origin` | json | Location in the original: page + bounding box, paragraph id, or cell |
| `csn_from`, `csn_to` | int, int or null | Transaction-time interval in which this version was current |

- Raw files are never copied into MDS. Each document is normalised once into a
  plain-text derivative and stored in the **content store** under its BLAKE3
  digest. Atoms point at byte ranges inside derivatives.
- Changing the normaliser changes every digest. The normaliser id is part of
  the version record so derivatives from different normalisers can coexist.

### 2.2 Re-anchoring on revision

When a document changes:

1. Parse the new version into sections.
2. Match new sections to existing atoms: first by heading path, then by
   MinHash similarity of shingles (threshold set in config).
3. Matched sections keep their `atom_id` and get a new version.
4. Splits and merges create new atoms linked by `derived_from`.
5. Unmatched old atoms get `tombstone_csn`.

Edges attach to `atom_id`, so revisions never orphan inbound edges.

---

## 3. Dimensions

A **dimension** is a kind of relationship with fixed meaning and rules. Each
dimension is stored as its own sparse matrix (section 5), so traversals can
combine dimensions precisely.

### 3.1 Built-in dimensions

| Id | Name | Edge `a → b` means | Time-bound | Default operator | Typical writers |
| --- | --- | --- | --- | --- | --- |
| `r0` | `contains` | `a` structurally contains `b` | no* | boolean | extractor |
| `r1` | `references` | `a` refers to `b` (cross-reference, defined term use, link) | no | boolean | extractor, agent |
| `r2` | `co_occurs` | `a` and `b` concern the same entity (stored both ways) | no | max-plus | extractor |
| `r3` | `derived_from` | `a` was derived from `b` (new version, copy, template) | no | boolean | extractor |
| `r4` | `supersedes` | `a` replaces or amends `b` from `valid_from` | yes | boolean | extractor, agent, person |

\* `contains` is time-bound only when a section is inserted or deleted by an
amendment; v0.1 treats it as not time-bound.

Every dimension is stored forwards and transposed, so each can be walked in
either direction (`fwd`, `rev`).

### 3.2 Dimension registry

Dimensions are declared in a registry, not hard-coded in traversal logic:

```yaml
- id: r4
  name: supersedes
  symmetric: false
  time_bound: true
  default_operator: boolean
  writers: [extractor, agent, person]
  agent_default_state: pending     # agent assertions need confirmation
  description: >
    a replaces or amends b. Follow forwards from an original to find what is
    currently in force.
```

The description is shown to agents verbatim (see `INTERFACES.md`). v0.1 ships
the five dimensions above. The format allows custom dimensions **(later)**.

### 3.3 Co-occurrence construction

`co_occurs` is derived, not asserted directly. With `E` the atom × entity
incidence matrix and `W` a diagonal IDF weighting:

```
A_r2 = prune_k( E · W · Eᵀ )
```

Entities whose document frequency exceeds a ceiling are dropped before the
product. Each row keeps its top `k` entries. Self-loops are removed.

---

## 4. Edges

| Field | Type | Notes |
| --- | --- | --- |
| `edge_id` | int | |
| `dim` | str | Dimension id |
| `src`, `dst` | int | `atom_id`s |
| `weight` | float | Confidence in (0, 1]. Extractor edges usually 1.0. |
| `valid_from`, `valid_to` | ISO date or null | Valid-time interval `[from, to)`. Null = unbounded. Only meaningful for time-bound dimensions. |
| `author_kind` | enum | `extractor`, `agent`, `person` |
| `author_id` | str | Extractor name+version, agent id, or person id |
| `evidence` | list[int] | Atoms this edge was derived from. Defaults to `[src, dst]`. |
| `state` | enum | `confirmed`, `pending`, `rejected` |
| `csn_from`, `csn_to` | int, int or null | Transaction-time interval in which this edge record was current |
| `note` | str | Optional free text from the author |

Rules:

- Edges are never updated in place. A change ends the old record (`csn_to`)
  and starts a new one.
- `pending` and `rejected` edges are not traversed by default. Traversal can
  opt in to `pending` edges; they come back marked as such.
- Evidence is how permissions apply to derived edges (section 7.2).

---

## 5. Graph engine

### 5.1 Matrices

For each dimension `r`, at a given CSN, the engine holds:

- `A_r`: square sparse matrix, `n × n` where `n` is the atom id space. Entry
  `(i, j)` = weight of the confirmed edge `i → j`.
- `A_rᵀ`: its transpose, kept materialised.
- A side table mapping each stored entry to its edge record (for valid-time
  intervals, author, evidence).

v0.1 uses `python-graphblas` (SuiteSparse:GraphBLAS) and rebuilds affected
matrices on commit. Split delta matrices with tombstone masks **(later)**:

```
A_eff(s) = (M ⊕ Δ⁺(≤ s)) ⟨¬ Δ⁻(≤ s)⟩
```

### 5.2 Traversal

A traversal starts from a weighted frontier vector `v₀` and applies a **path**:
an ordered list of steps.

```
step := { dim, direction: fwd | rev, hops: 1 | n | "*" }
```

Each hop computes:

```
v ← top_k( v ⊕.⊗ A ⟨E_u ∧ V(t)⟩ ) ⟨p_u⟩
```

- `⊕.⊗` is the step's operator (5.3).
- `⟨E_u ∧ V(t)⟩` is an edge mask: evidence visible to the principal, and (for
  time-bound dimensions) valid at instant `t`.
- `⟨p_u⟩` is the vertex mask of atoms the principal may see.
- `top_k` keeps the `k` highest-scoring entries; a per-hop degree cap limits
  expansion from very high-degree atoms.
- `hops: "*"` repeats until the frontier stops changing or a hop limit (default
  16) is hit.

The result is the final frontier, ranked, with each atom's best path.

### 5.3 Operators

| Name | (⊕, ⊗) | Use |
| --- | --- | --- |
| `boolean` | (or, and) | Exact reach and closure |
| `count` | (+, ×) | Rank by number of supporting paths |
| `max_plus` | (max, +) over log-weights | Most confident path |
| `bottleneck` | (max, min) | Strongest weakest link |

Time-respecting earliest-arrival traversal **(later)**.

### 5.4 Ranking

Final scores blend the step operator's value, the seed score and edge
confidence. The formula is in `src/mds/engine/rank.py` and covered by golden
tests; it is not part of the public contract.

### 5.5 The in-force query

"What does atom `c` say at valid time `t`, as known at CSN `s`?"

```
inforce(c, t, s) = sink( e_cᵀ (or.and)* A_r4ᵀ(s) ⟨E_u ∧ V(t)⟩ )
```

Follow `supersedes` in reverse from `c` (from the original to what replaced
it), keeping only edges valid at `t`, to the end of the chain. The answer is
the set of **sinks**: atoms reached from `c` that are not themselves
superseded at `t`.

| Sinks reached | Status | `current` |
| --- | --- | --- |
| None (nothing valid at `t` supersedes `c`) | `unchanged` | `[c]` |
| Exactly one | `resolved` | that sink |
| More than one | `conflict` | every sink |

A conflict means the chain ends in more than one place. Branches that
converge on a single sink are not a conflict: if `b` and `d` both supersede
`a`, and `d` also supersedes `b`, the only sink is `d` and the result is
`resolved`. When there is a conflict, return every sink. Never pick one
silently.

A sink may be a `placeholder` atom (something referenced as the replacement
but not held). It is returned like any other sink.

Direction convention: an edge `a → b` in `r4` means `a` supersedes `b`, so the
walk from an original uses `rev`.

---

## 6. Time

MDS tracks two independent times.

- **Transaction time (CSN).** A 64-bit counter incremented by every commit.
  Every atom version and edge record carries `csn_from`/`csn_to`. Reading "as
  of CSN `s`" sees exactly what MDS believed at that commit.
- **Valid time.** Dates on time-bound edges (`valid_from`, `valid_to`). Reading
  "as of valid time `t`" keeps only edges whose interval contains `t`.

Defaults: `s` = latest CSN, `t` = today (UTC date). Queries can set either.

Permissions are never time-travelled: historical reads always use the
principal's **current** mask (section 7.4).

---

## 7. Permissions

### 7.1 Principals and classes

- A **principal** is a user or service an agent acts for. It belongs to groups.
- Every atom has an `acl_class`: an interned id for one distinct combination
  of groups allowed to read it.
- A principal's **vertex mask** `p_u` is the union of the atom sets for every
  class the principal can read. It is a roaring bitmap, cached per principal
  and invalidated when classes or memberships change.

v0.1 reads principals, groups and path-based class rules from a config file
(`mds.toml`, see `INTERFACES.md`). Connectors that mirror external ACLs
**(later)**.

### 7.2 Evidence mask

A derived edge is visible only if at least one of its `evidence` atoms is in
`p_u`, in addition to both endpoints being visible. A link that exists only
because of a hidden document is therefore invisible.

### 7.3 Guarantee

Both masks apply at every hop, before ranking. Atoms and edges the principal
cannot see never contribute to scores, counts or paths. Non-traversal holds by
construction.

### 7.4 Known side channels

| Channel | v0.1 handling |
| --- | --- |
| Lexical statistics (document frequency, IDF) | Kept per `acl_class` and combined over the principal's classes at query time |
| IDF used to prune `co_occurs` | Global in v0.1; documented residual leak |
| `hydrate` | Re-checks the mask on every call |
| Blackboard | Breadcrumbs and assertions carry the author's `acl_class`; readers see only those they could read |
| Timing | Accepted; documented |
| Historical reads | Always use the current mask |

The leak test suite (`tests/leak/`) encodes these as tests.

---

## 8. Seeding

Seeding turns a question into the starting frontier `v₀`.

- **Lexical:** Tantivy BM25 over atom text, titles and entity names.
- **Dense:** a small embedding model over atom titles and first lines only.
- **Entity:** exact and alias match against entity atoms.

Results are fused with reciprocal rank fusion and masked by `p_u`. Seeding
never returns invisible atoms.

---

## 9. Ingestion

Ingestion fills the graph deterministically. Agents add to it later through the
Blackboard.

| Dimension | v0.1 extractor |
| --- | --- |
| `contains` | Document structure from the parser: headings, numbered sections, lists, tables |
| `references` | Pattern rules for explicit references ("clause 4.2", "section 3", "Schedule 2", "Appendix A"), resolved within the same document first; hyperlinks; defined-term use |
| `co_occurs` | Entity spans (GLiNER or dictionary) → `E` → section 3.3 |
| `derived_from` | Source version history; MinHash near-duplicates |
| `supersedes` | Amendment phrase rules ("deleted and replaced", "is amended to read", "supersedes", "with effect from") plus explicit effective dates |

Each extractor records its name and version as `author_id`, so re-running a
newer extractor can retire its own older edges without touching others.

### 9.1 Supersession declared in document metadata

Some documents declare supersession in structured metadata rather than in
amendment wording. The PEP sample corpus does this with header fields:

- `Superseded-By: X` in document `b` gives the edge `X supersedes b`.
- `Replaces: Y` in document `a` gives the edge `a supersedes Y`.
- When both headers describe the same pair, they produce one edge.
- A target that is not in the corpus becomes a `placeholder` atom.

**Valid time when no effective date is stated.** Metadata like this rarely
carries an effective date. The edge's `valid_from` is then the superseding
document's own date: for PEPs, the `Created` header of the superseding PEP.
`valid_to` is unbounded. An explicit effective date in the document, where
one exists, always takes precedence over this fallback.

The golden questions in `tests/golden/` depend on these rules.

---

## 10. Blackboard

Agents coordinate through the graph rather than by messaging each other.

| Object | Purpose |
| --- | --- |
| Assertion | An agent proposes an edge. Stored as an edge with `author_kind=agent` and the dimension's `agent_default_state`. |
| Breadcrumb | A note pinned to one or more atoms: a finding, a summary, a warning. Has author, CSN and the read-set it was based on. |
| Change feed | Ordered log of commits. Agents ask for changes since a CSN, optionally filtered to a cone of atoms. |
| Subscription **(later)** | Push notifications when a change intersects a watched cone. |
| Lease **(later)** | Advisory claim on a region before writing. |

### 10.1 Read-sets and taint

Every `hydrate` call records which atom versions a session read. Breadcrumbs
and assertions store that read-set. If a source atom is later revised or
tombstoned, everything built on it can be found and flagged.

### 10.2 Trust and review

- Each agent id has a trust level in config.
- Assertions from agents below a dimension's threshold are `pending`.
- A person (or a higher-trust agent, if config allows) confirms or rejects.
- Conflicting assertions are kept side by side with provenance; MDS never
  resolves a conflict silently.

---

## 11. Storage

v0.1:

- **SQLite** holds atoms, versions, edges, breadcrumbs, the commit log and the
  CSN counter. One writer; readers use snapshots.
- **Content store:** a directory of BLAKE3-addressed text derivatives.
- **Seed index:** Tantivy index directory.
- **Matrices:** rebuilt from SQLite at start-up and updated on commit.

Write-ahead log, mmap checkpoints, delta matrices and a Rust engine
**(later)**.

---

## 12. Views

A **view** is a named, saved traversal: seeds or a seed query, a path, an
operator, `k`, and optional time settings. Running a view as a principal
returns what that principal can see. Views are how a "folder" is expressed:

- "Everything about Acme" = seed on the Acme entity, `co_occurs fwd 1`.
- "What depends on this section" = seed on the section, `references rev *`.
- "Current version of every policy" = seed on policy documents,
  `supersedes rev *` at today.

A read-only path tree generated from views **(later)**.

---

## 13. Glossary of invariants

1. `atom_id`s are stable and never reused.
2. Raw files are never copied; only normalised derivatives are stored.
3. Edge and version records are append-only; changes close old records.
4. Masks apply at every hop, before ranking.
5. Historical reads use current permissions.
6. Conflicting supersession is reported, never silently resolved.
7. Every edge says who made it, from what evidence, and how confident it is.
