# 0002: Dimensions as sparse matrices evaluated with GraphBLAS

Status: accepted · 2026-10-20

## Context
MDS needs typed, multi-hop traversal with per-user masks applied at every hop and a
choice of path semantics (reachability, counting, confidence, bottleneck).

## Decision
Store each dimension as its own sparse adjacency matrix plus transpose, and evaluate
traversals as masked vector-matrix products over selectable semirings.

## Alternatives considered
- Property graph database (Neo4j and similar): flexible labels, but masks and semirings
  would live in application code, and per-hop masking is slower and easier to get wrong.
- Pointer-based in-memory graph: simple, but no algebra, poor cache behaviour.

## Consequences
- Masks are a native operation, which gives the permission guarantee by construction.
- Atom ids must be dense integers (matrix indices).
