# Golden questions

`questions.yaml` holds questions with known answers over the sample corpus in
`examples/corpus/`. Each question has a natural-language form (for agent
benchmarks, task T42) and a structured `check` (for the golden runner, task
T41). `test_questions_valid.py` checks the file against the corpus manifest.

## Running the corpus

The runner (T41) builds the store in two stages so transaction-time questions
have something to look back at:

1. Copy `examples/corpus/docs/` to a temporary ingest root, then overwrite the
   files in `examples/corpus/history/stage1/` onto it (same relative paths).
   Ingest with `examples/corpus/mds.toml`. Record the CSN as `stage1`.
2. Copy the current files from `examples/corpus/docs/` back over those paths
   and ingest again. This is `latest`.

Stage 1 contains older versions of PEP 563 and PEP 649, from before either
PEP recorded that 649 replaces 563.

## Principals

| Principal | Can read |
| --- | --- |
| `alice` | everything |
| `bob` | everything except `docs/restricted/` (PEPs 615 and 749) |

## Format

```yaml
- id: g020
  category: supersedes   # contains | references | co_occurs | derived_from | supersedes | time | revision | permissions | seed
  principal: alice       # default alice
  question: "What currently replaces PEP 563?"
  check:
    tool: in_force
    atom: pep-0563.rst
    valid_at: null       # ISO date; null = today
    as_of: latest        # latest | stage1
    expect:
      status: conflict   # resolved | unchanged | conflict
      current: [pep-0649.rst, pep-0749.rst]
```

Other `check` shapes:

```yaml
check:
  tool: traverse
  seeds: [pep-0484.rst]            # or seed_query: "manylinux" (top 5 seeds)
  path: [[references, rev, 1], [contains, rev, "*"]]
  operator: count                  # default count
  k: 50                            # default 50
  expect:
    include: [pep-0526.rst]
    exclude: [pep-0749.rst]

check:
  tool: seed
  query: "asyncio event loop"
  k: 10
  expect: {include: [pep-3156.rst], exclude: []}

check:
  tool: hydrate
  atoms: [pep-0749.rst]
  expect: {error: not_found}
```

## Matching rules

- An atom is named by its `path_hint`: the file name for a document
  (`pep-0563.rst`), or the file name and the section heading for a section
  (`pep-0563.rst › Abstract`). Headings are matched case-insensitively at any
  depth.
- In `include` and `exclude`, a document hint also matches any atom the
  document contains. `exclude` therefore means "nothing from this document".
- `current` in `in_force` must match exactly (as a set), at document level.
- `current` may contain `placeholder:PEP 600`: a placeholder atom for a PEP
  that is referenced but not in the corpus (PEP 600 has no licence statement,
  so it is excluded).

## Conventions the extractors must follow for this corpus

The supersession and `in_force` rules below are specified in `DESIGN.md`
§5.5 and §9.1; they are summarised here because the expected answers depend
on them.

- **Supersession comes from PEP headers.** `Superseded-By: X` in PEP `b`
  gives the edge `X supersedes b`. `Replaces: Y` in PEP `a` gives
  `a supersedes Y`. Duplicate edges from both headers are one edge.
- **Valid time of a supersession** starts at the `Created` date of the
  superseding PEP and has no end.
- **`in_force` returns the sinks** of the supersession chain at the valid
  date: atoms reached that are not themselves superseded. One sink is
  `resolved`; several sinks are a `conflict`; none reached is `unchanged`.
  Branches that converge on one sink (PEP 345 is replaced by both 426 and
  566, and 426 by 566) resolve to that sink.
- **References** come from `PEP NNN` text and `:pep:` roles, and from the
  `Requires` header. They point at the referenced document.
- **`derived_from`** links a near-duplicate document to the older one it was
  copied from (by `Created` date). PEP 3333 is a near-duplicate of PEP 333.
