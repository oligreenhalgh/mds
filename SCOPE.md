# v0.1 scope

v0.1 proves the core idea end to end on a laptop: documents are filed into a
multi-dimensional graph automatically, agents navigate it over MCP, agents add
to it, and every read respects time and permissions.

## Goals

1. `mds-server ingest <folder>` ingests PDF, DOCX, Markdown, reStructuredText
   and plain text from a local folder.
2. Ingestion builds `contains`, `references`, `derived_from`, `co_occurs` and
   `supersedes` edges with deterministic extractors (DESIGN §9).
3. Engine runs masked, typed traversals with all four operators over
   `python-graphblas` (DESIGN §5).
4. `in_force` resolves supersession chains at a valid date and reports
   conflicts (DESIGN §5.5).
5. Transaction time (CSN) and valid time both work for reads (DESIGN §6).
6. Principals, groups and path-based ACL rules from `mds.toml`; vertex and
   evidence masks applied at every hop (DESIGN §7).
7. All MCP tools in `INTERFACES.md` §2 work over stdio with Claude Desktop and
   Claude Code.
8. Agents can assert edges and post breadcrumbs; pending assertions can be
   reviewed from the CLI.
9. A public sample corpus, golden tests and leak tests ship in the repo and pass
   in CI on macOS and Linux.
10. One-command quickstart: install, ingest the sample corpus, connect an agent.

## Non-goals for v0.1

Do not build these. Open an issue instead if one seems necessary.

- Rust engine, custom SIMD kernels, mmap checkpoints, WAL, delta matrices
- Sharding, replication or multi-node operation
- Connectors to SharePoint, Google Drive, Box, S3 or Git; external ACL sync
- Leases, conflict detection on commit, push subscriptions
- Earliest-arrival temporal operator
- Custom user-defined dimensions (the registry format allows them; no tooling)
- gRPC API, HTTP API, read-only path view
- Any human UI beyond the CLI
- Code ingestion (tree-sitter, SCIP)
- LLM calls inside MDS itself. Agents call MDS; MDS does not call models,
  except local embedding and entity models for seeding and `co_occurs`.
- Windows support

## Acceptance

v0.1 is done when:

- [ ] All goals above are implemented and documented
- [ ] `uv run pytest` passes on macOS (arm64) and Ubuntu in CI
- [ ] Golden suite: at least 90% of golden questions return every expected
      source atom in the top `k`
- [ ] Leak suite: zero failures
- [ ] Quickstart in `README.md` works from a clean checkout in under 10 minutes
- [ ] A recorded agent session shows seed → traverse → in_force → hydrate →
      breadcrumb on the sample corpus
- [ ] Benchmark comparing an agent using MDS tools against file search and
      plain vector retrieval on the golden questions, results in
      `benchmarks/RESULTS.md`
