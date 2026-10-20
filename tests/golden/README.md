# Golden questions

`questions.yaml` holds questions with known answers over `examples/corpus`.
Each entry:

```yaml
- id: g001
  question: "Which section replaced section 4 of the 2021 policy?"
  principal: alice
  category: supersedes          # contains | references | co_occurs | derived_from | supersedes | time | permissions
  valid_at: 2026-10-20          # optional
  as_of_csn: null               # optional
  expected_atoms:               # by path_hint, resolved to ids after ingest
    - "policy-2023.md › 4"
  must_not_include: []          # atoms this principal must never see in results
  notes: ""
```

The runner (task T41) resolves `path_hint`s after ingesting the corpus, runs
each question through the Python API and reports the share of questions whose
`expected_atoms` all appear in the top `k`.
