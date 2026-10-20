# 0006: MDS does not call language models

Status: accepted · 2026-10-20

## Decision
Agents call MDS; MDS never calls an LLM. Ingestion uses deterministic extractors plus
small local models (embeddings, entity recognition). Semantic structure that rules
cannot find is added by agents through assertions.

## Consequences
- Ingestion is cheap, repeatable and explainable.
- Every edge says whether a rule, an agent or a person made it.
- Agent assertions need trust levels and review (DESIGN §10.2).
