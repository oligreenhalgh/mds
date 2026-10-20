# MDS — Multi-Dimensional Server

**A filing system for AI agents.** MDS files information as a graph with
several typed dimensions instead of a folder tree. Agents navigate it and add
to it while they work, and every query respects time and the permissions of
whoever the agent is acting for.

> Status: pre-alpha. Design documents are in place; the implementation is
> starting. Nothing here is usable yet.

## Why

Folders force one way of organising information, chosen up front. Agents
working over a folder tree re-read the same files, miss how documents relate,
and cannot tell which version applies. MDS keeps the files where they are and
files *relationships* instead:

| Dimension | Answers |
| --- | --- |
| `contains` | What is this part of? What are its parts? |
| `references` | What does this refer to? What refers to it? |
| `co_occurs` | What else concerns the same entity? |
| `derived_from` | Where did this come from? |
| `supersedes` | What replaced this, and from when? |

A "folder" becomes a **view**: a saved traversal along one or more dimensions.
Each dimension is a sparse matrix, and traversal is masked sparse linear
algebra (GraphBLAS), so agents get the few items that matter, with the path
that connects them.

## Design

- [`DESIGN.md`](DESIGN.md): the data model and invariants
- [`INTERFACES.md`](INTERFACES.md): MCP tools, CLI, configuration
- [`SCOPE.md`](SCOPE.md): what v0.1 does and does not include
- [`decisions/`](decisions/): why key choices were made
- [`TASKS.md`](TASKS.md): the build plan

## Quickstart (planned for v0.1)

```bash
uv tool install mds-server
mds-server init demo && cd demo
mds-server ingest ../examples/corpus
mds-server serve --principal alice     # add to your MCP client config
```

## Platforms

macOS (Apple silicon) and Linux. Python 3.12+.

## Licence

Apache 2.0. See [`LICENSE`](LICENSE).
