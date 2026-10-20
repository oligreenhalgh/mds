# MDS interfaces

This file defines every interface agents and people use. The MCP tool surface
is the product: names, inputs, outputs and errors here are a contract.
Changes need maintainer approval (see `CLAUDE.md`).

Concepts and invariants are defined in `DESIGN.md`.

---

## 1. Conventions

- Atom ids are integers. Responses always pair an id with a short `title` and
  `path_hint` so an agent never has to look an id up to understand it.
- Dates are ISO `YYYY-MM-DD`. CSNs are integers.
- Every response includes `csn`: the commit the answer was computed at.
- Responses are compact JSON. Text content only appears in `hydrate`.
- The principal is fixed per server session (section 4). Tools never take a
  principal argument, so an agent cannot widen its own access.
- Errors return `{"error": {"code": "...", "message": "..."}}` with codes from
  section 5.

---

## 2. MCP tools

Tool names are prefixed `mds_`. Descriptions in code must match the "Purpose"
lines below, because agents choose tools from them.

### `mds_dimensions`

Purpose: list the dimensions an agent can traverse, with what each means.

Input: none.

Output:

```json
{"csn": 812, "dimensions": [
  {"id": "r4", "name": "supersedes", "time_bound": true,
   "description": "a replaces or amends b. Follow rev from an original to find what is currently in force."}
]}
```

### `mds_seed`

Purpose: find starting atoms for a question or name.

| Input | Type | Default |
| --- | --- | --- |
| `query` | str | required |
| `kinds` | list[str] | all |
| `k` | int | 10 (max 50) |

Output: `{"csn", "results": [{"atom_id", "kind", "title", "path_hint", "score", "match": "lexical|dense|entity"}]}`

### `mds_traverse`

Purpose: follow typed relationships from starting atoms.

| Input | Type | Default |
| --- | --- | --- |
| `seeds` | list[int] | required |
| `path` | list[step] | required, 1–8 steps |
| `operator` | `boolean` \| `count` \| `max_plus` \| `bottleneck` | `count` |
| `k` | int | 20 (max 200) |
| `valid_at` | date | today |
| `as_of_csn` | int | latest |
| `include_pending` | bool | false |

`step = {"dim": "references", "direction": "fwd" | "rev", "hops": 1 | 2 | … | "*"}`

`dim` accepts a dimension id (`r1`) or name (`references`).

Output:

```json
{"csn": 812, "results": [
  {"atom_id": 4411, "kind": "section", "title": "4.2 Retention period",
   "path_hint": "policy-2023.pdf › 4 › 4.2", "score": 0.91,
   "path": [{"from": 102, "dim": "supersedes", "direction": "rev",
             "edge_id": 9001, "weight": 1.0, "state": "confirmed",
             "valid_from": "2023-03-01", "valid_to": null}]}
], "truncated": false}
```

### `mds_in_force`

Purpose: what currently applies for an atom, after all replacements, at a date.

| Input | Type | Default |
| --- | --- | --- |
| `atom_id` | int | required |
| `valid_at` | date | today |
| `as_of_csn` | int | latest |

Output:

```json
{"csn": 812, "valid_at": "2026-10-20",
 "status": "resolved" | "unchanged" | "conflict",
 "current": [{"atom_id": 5120, "title": "...", "path_hint": "..."}],
 "chain": [{"atom_id": 102, "...": "..."}, {"atom_id": 4411}, {"atom_id": 5120}],
 "note": "Covers replacements visible to you."}
```

`note` is identical in every response, whether or not anything is hidden.

### `mds_hydrate`

Purpose: read the text of specific atoms, with citations.

| Input | Type | Default |
| --- | --- | --- |
| `atom_ids` | list[int] | required, max 20 |
| `max_chars` | int | 4000 per atom |

Output: `{"csn", "atoms": [{"atom_id", "version", "title", "path_hint", "text", "truncated", "citation": {"source_uri", "origin"}}]}`

Records the read-set for this session (DESIGN §10.1).

### `mds_view`

Purpose: run, list or save a named view (a saved traversal; the equivalent of a folder).

| Input | Type | Notes |
| --- | --- | --- |
| `action` | `list` \| `run` \| `save` | required |
| `name` | str | for `run` and `save` |
| `definition` | object | for `save`: `seeds` or `seed_query`, `path`, `operator`, `k`, `valid_at?` |

### `mds_assert_edge`

Purpose: record a relationship you found between two atoms.

| Input | Type | Default |
| --- | --- | --- |
| `dim` | str | required |
| `src`, `dst` | int | required |
| `confidence` | float in (0, 1] | required |
| `evidence` | list[int] | `[src, dst]` |
| `valid_from`, `valid_to` | date | null |
| `note` | str | "" |

Output: `{"csn", "edge_id", "state": "pending" | "confirmed"}`

Fails with `not_visible` if the agent's principal cannot see `src`, `dst` or
any evidence atom.

### `mds_breadcrumb`

Purpose: leave or read notes pinned to atoms for other agents.

| Input | Type | Notes |
| --- | --- | --- |
| `action` | `post` \| `list` | required |
| `atom_ids` | list[int] | required |
| `text` | str | for `post`, max 4000 chars |

`post` stores the session's read-set with the breadcrumb.

### `mds_changes`

Purpose: see what changed since a commit, optionally near given atoms.

| Input | Type | Default |
| --- | --- | --- |
| `since_csn` | int | required |
| `near` | list[int] | none |
| `depth` | int | 2 (cone depth when `near` is set) |

Output: `{"csn", "changes": [{"csn", "kind": "atom|edge|breadcrumb", "atom_ids", "summary"}]}`

### `mds_review` (people and trusted agents only)

Purpose: confirm or reject pending assertions.

| Input | Type |
| --- | --- |
| `action` | `list` \| `confirm` \| `reject` |
| `edge_id` | int, for confirm/reject |
| `reason` | str, optional |

Refused with `forbidden` unless the session's trust level allows review.

---

## 3. Command line

The CLI is `mds-server` (not `mds`, which is the macOS Spotlight daemon).

```
mds-server init [DIR]                 create mds.toml and a data directory
mds-server ingest PATH                ingest a file or folder
mds-server serve --principal NAME     run the MCP server (stdio) as a principal
mds-server query seed "TEXT"          seed from the shell (debugging)
mds-server query traverse --seeds 1,2 --path "references:fwd:1"
mds-server review list|confirm|reject
mds-server stats                      atom, edge and dimension counts
```

---

## 4. Configuration: `mds.toml`

```toml
[store]
data_dir = ".mds"

[[principals]]
name = "alice"
groups = ["staff", "legal"]
trust = "person"          # person | agent_high | agent

[[principals]]
name = "research-agent"
groups = ["staff"]
trust = "agent"

[[acl_rules]]              # first match wins; path globs relative to ingest root
glob = "legal/**"
groups = ["legal"]

[[acl_rules]]
glob = "**"
groups = ["staff"]

[dimensions.r4]
agent_default_state = "pending"
```

The server's principal is chosen at start (`serve --principal`). An MCP client
connects to one server per principal.

---

## 5. Error codes

| Code | Meaning |
| --- | --- |
| `bad_request` | Input failed validation |
| `unknown_dimension` | `dim` is not in the registry |
| `not_found` | Atom or edge does not exist |
| `not_visible` | Atom exists but the principal cannot see it. Returned only for writes; reads treat invisible atoms as not found, so existence is not leaked. |
| `forbidden` | Action needs a higher trust level |
| `limit_exceeded` | `k`, path length or batch size above maximum |

---

## 6. Python API

The MCP server is a thin layer over a Python API with the same operations:

```python
from mds import Server

srv = Server.open("mds.toml")
s = srv.session(principal="alice")
seeds = s.seed("data retention period", k=5)
res = s.traverse(
    [a.atom_id for a in seeds],
    path=[("contains", "rev", 1), ("supersedes", "rev", "*")],
    operator="count",
)
current = s.in_force(seeds[0].atom_id, valid_at="2026-10-20")
texts = s.hydrate([r.atom_id for r in res][:5])
```

Signatures in `src/mds/api.py` are the reference; this section must stay in
sync with them.
