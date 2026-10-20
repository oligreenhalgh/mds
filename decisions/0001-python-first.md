# 0001: Python first, Rust later

Status: accepted · 2026-10-20

## Context
MDS is built by one person with coding agents. The data model, dimensions and agent
tool surface are unproven; the engine's raw speed is not the first risk.

## Decision
Build v0.1 in Python 3.12+, using `python-graphblas` (SuiteSparse:GraphBLAS) for the
sparse algebra. Keep the Python implementation as the reference and test oracle when
a Rust core is written later.

## Consequences
- Faster iteration on the model and tools; most heavy lifting is still native code.
- Performance claims are not made for v0.1.
- Engine interfaces must stay narrow so a Rust core can replace them.
