# Sample corpus sources

Task T01 picks the final corpus. For every document included, record: title,
source URL, licence, and which dimensions it exercises. Only include documents
whose licence permits redistribution.

## Candidates (surveyed 2026-10-20)

| Candidate | Licence | Size | Dimensions it exercises | Fit |
| --- | --- | --- | --- | --- |
| [Python PEPs](https://github.com/python/peps) | Each PEP: public domain or CC0-1.0 (stated in its Copyright section) | ~700 reStructuredText documents | `contains` (sections), `references` ("PEP 484", links), `supersedes` (explicit `Replaces` / `Superseded-By` headers, e.g. PEP 563 → 649, 749), `derived_from` (git history), `co_occurs` (topics, authors) | **Primary.** Small, clean, domain-neutral, ground truth for supersession is in the headers. Includes a branching chain (563 → 649 and 749) for conflict tests. |
| [do-me/EUR-LEX](https://huggingface.co/datasets/do-me/EUR-LEX) | CC BY 4.0 | 328k documents, 6.7 GB | `contains` (articles, annexes), `references` ("Article 5", "Annex II"), `supersedes` (amending and repealing acts) | **Secondary.** Realistic legal amendment language. Use a small subset: one regulation family with its amending acts, selected by CELEX number. Attribution required. |
| [alea-institute/alea-legal-amendments](https://huggingface.co/datasets/alea-institute/alea-legal-amendments) | CC BY 4.0 (sources are US government works) | 16,500 task rows | `supersedes` only: before/after section text with the amending instruction | **Extractor fixtures**, not a filing corpus. Useful for testing the supersedes extractor (T23). |
| [theatticusproject/cuad](https://huggingface.co/datasets/theatticusproject/cuad) | CC BY 4.0 | 510 contracts | `contains`, `references`; few amendments | Optional. Good structure, weak on supersession. |

## Recommendation

Start with a PEP subset of about 60–100 documents chosen around supersession
chains and heavily cross-referenced PEPs (typing, packaging, annotations).
Add one EUR-Lex regulation family later to test legal amendment language.
Golden questions (T02) can be checked mechanically against PEP headers.
