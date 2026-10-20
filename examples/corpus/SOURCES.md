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

## Selected corpus (T01)

- **Source:** [python/peps](https://github.com/python/peps) at commit
  `5bc3e11b80e1` (86 PEPs, about 2.5 MB). Each included PEP states in its
  Copyright section that it is in the public domain or under CC0-1.0; the test
  `test_corpus_licence_statements` enforces this.
- **Selection:** every PEP with a `Replaces` or `Superseded-By` header, the PEPs
  those headers point to, the PEPs they `Require`, and eight typing PEPs for
  cross-references.
- **Excluded for lack of a licence statement:** PEPs 5, 216, 600. PEP 600 remains
  referenced by PEPs 513, 571 and 599, so it is filed as a placeholder atom.
- **Restricted folder** (`docs/restricted/`, hidden from principal `bob`): PEPs 615 and 749.
- **Stage 1 history** (`history/stage1/`): PEPs 563 and 649 at commit
  `8e51108bbc8d`, before PEP 649 was recorded as replacing PEP 563.
- **Ground truth:** `manifest.json` lists each PEP's title, status, dates and
  supersession headers.

PEPs: 101, 102, 215, 241, 245, 246, 247, 248, 249, 287, 291, 292, 314, 333, 345, 354, 381, 382, 386, 387, 397, 402, 409, 415, 422, 426, 431, 433, 435, 438, 440, 443, 446, 449, 452, 464, 470, 484, 486, 487, 501, 508, 509, 513, 518, 526, 543, 544, 554, 563, 566, 571, 585, 599, 601, 604, 612, 615, 621, 622, 631, 634, 646, 649, 665, 695, 699, 701, 722, 723, 724, 734, 742, 748, 749, 750, 751, 765, 773, 3107, 3115, 3119, 3124, 3153, 3156, 3333.
