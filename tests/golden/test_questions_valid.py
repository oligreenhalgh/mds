"""Check questions.yaml against the sample corpus, independently of MDS itself.

Expected answers are recomputed from PEP headers and text using the conventions
in README.md, so a typo or a wrong expectation fails here rather than in the
golden runner.
"""

import re
from datetime import date, datetime
from functools import cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "examples" / "corpus"
DOCS = CORPUS / "docs"
STAGE1 = CORPUS / "history" / "stage1"
QUESTIONS = yaml.safe_load((Path(__file__).parent / "questions.yaml").read_text())

CATEGORIES = {
    "contains",
    "references",
    "co_occurs",
    "derived_from",
    "supersedes",
    "time",
    "revision",
    "permissions",
    "seed",
}
RESTRICTED_FOR = {"alice": set(), "bob": {"restricted"}}
EXCLUDED_PEPS = {5, 216, 600}  # no licence statement; may appear only as placeholders


def pep_number(hint: str) -> int:
    m = re.fullmatch(r"pep-(\d{4})\.rst", hint.split(" › ")[0])
    assert m, f"bad hint {hint!r}"
    return int(m.group(1))


@cache
def files(stage: str) -> dict[int, Path]:
    out = {int(p.stem[4:]): p for p in DOCS.rglob("pep-*.rst")}
    if stage == "stage1":
        out |= {int(p.stem[4:]): p for p in STAGE1.rglob("pep-*.rst")}
    return out


def folder(n: int) -> str:
    return files("latest")[n].parent.name


@cache
def headers(stage: str, n: int) -> dict[str, str]:
    h: dict[str, str] = {}
    key = None
    for line in files(stage)[n].read_text().splitlines():
        if not line.strip():
            break
        m = re.match(r"^([A-Za-z-]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            h[key] = m.group(2).strip()
        elif key:
            h[key] += " " + line.strip()
    return h


def nums(value: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", value)]


def created(stage: str, n: int) -> date:
    return datetime.strptime(headers(stage, n)["Created"].split()[0], "%d-%b-%Y").date()


def supersedes_edges(stage: str) -> set[tuple[int, int]]:
    """(a, b) means a supersedes b, from Replaces and Superseded-By headers."""
    edges = set()
    for n in files(stage):
        h = headers(stage, n)
        edges |= {(n, m) for m in nums(h.get("Replaces", ""))}
        edges |= {(m, n) for m in nums(h.get("Superseded-By", ""))}
    return edges


def visible(principal: str, n: int) -> bool:
    if n not in files("latest"):
        return n in EXCLUDED_PEPS  # placeholders carry no content to hide
    return folder(n) not in RESTRICTED_FOR[principal]


def in_force(stage: str, principal: str, n: int, at: date) -> tuple[str, set[str]]:
    edges = {
        (a, b)
        for a, b in supersedes_edges(stage)
        if visible(principal, a)
        and visible(principal, b)
        and (a not in files(stage) or created(stage, a) <= at)
    }
    seen, frontier, sinks = {n}, {n}, set()
    while frontier:
        nxt = set()
        for b in frontier:
            succ = {a for a, bb in edges if bb == b}
            if not succ and b != n:
                sinks.add(b)
            nxt |= succ - seen
        seen |= nxt
        frontier = nxt
    names = {f"placeholder:PEP {s}" if s not in files(stage) else f"pep-{s:04d}.rst" for s in sinks}
    if not names:
        return "unchanged", {f"pep-{n:04d}.rst"}
    return ("resolved" if len(names) == 1 else "conflict"), names


def references(src: int, dst: int) -> bool:
    text = files("latest")[src].read_text()
    pat = rf"(\bPEP[ -]?0*{dst}\b|:pep:`(?:[^`<]*<)?0*{dst}(?:#[^`>]*)?>?`)"
    return bool(re.search(pat, text)) or dst in nums(headers("latest", src).get("Requires", ""))


def headings(n: int) -> set[str]:
    lines = files("latest")[n].read_text().splitlines()
    return {
        lines[i].strip().lower()
        for i in range(len(lines) - 1)
        if lines[i].strip()
        and re.fullmatch(r"([=\-~^\"'`#*+])\1{2,}", lines[i + 1].strip())
        and len(lines[i + 1].strip()) >= len(lines[i].strip())
    }


def all_hints(q: dict) -> list[str]:
    c = q["check"]
    hints = [c.get("atom")] + c.get("seeds", []) + c.get("atoms", [])
    exp = c["expect"]
    hints += exp.get("include", []) + exp.get("exclude", []) + exp.get("current", [])
    return [h for h in hints if h and not h.startswith("placeholder:")]


def test_ids_unique_and_fields_valid() -> None:
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))
    assert 30 <= len(ids) <= 50
    for q in QUESTIONS:
        assert q["category"] in CATEGORIES, q["id"]
        assert q.get("principal", "alice") in RESTRICTED_FOR, q["id"]
        assert q["check"]["tool"] in {"in_force", "traverse", "seed", "hydrate"}, q["id"]
        assert q["question"].strip(), q["id"]


def test_every_category_covered() -> None:
    assert {q["category"] for q in QUESTIONS} == CATEGORIES


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_hints_exist(q: dict) -> None:
    for hint in all_hints(q):
        n = pep_number(hint)
        assert n in files("latest"), f"{q['id']}: {hint} not in corpus"
        if " › " in hint:
            heading = hint.split(" › ", 1)[1].lower()
            assert heading in headings(n), f"{q['id']}: no heading {heading!r} in {hint}"


@pytest.mark.parametrize(
    "q", [q for q in QUESTIONS if q["check"]["tool"] == "in_force"], ids=lambda q: q["id"]
)
def test_in_force_expectations(q: dict) -> None:
    c = q["check"]
    at = c.get("valid_at") or date.today()
    status, current = in_force(
        c.get("as_of", "latest"), q.get("principal", "alice"), pep_number(c["atom"]), at
    )
    assert c["expect"]["status"] == status, q["id"]
    assert set(c["expect"]["current"]) == current, q["id"]


@pytest.mark.parametrize(
    "q",
    [
        q
        for q in QUESTIONS
        if q["check"]["tool"] == "traverse"
        and "seeds" in q["check"]
        and q["check"]["path"][0][0] == "references"
        or q["check"]["tool"] == "traverse"
        and q["check"]["path"][-1][0] == "references"
    ],
    ids=lambda q: q["id"],
)
def test_reference_expectations(q: dict) -> None:
    c = q["check"]
    seed = pep_number(c["seeds"][0])
    direction = c["path"][0][1] if c["path"][0][0] == "references" else c["path"][-1][1]
    for hint in c["expect"]["include"]:
        n = pep_number(hint)
        ok = references(n, seed) if direction == "rev" else references(seed, n)
        assert ok, f"{q['id']}: no reference between {n} and {seed}"


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_permissions_consistent(q: dict) -> None:
    principal = q.get("principal", "alice")
    exp = q["check"]["expect"]
    for hint in exp.get("include", []) + [
        h for h in exp.get("current", []) if not h.startswith("placeholder:")
    ]:
        assert visible(principal, pep_number(hint)), f"{q['id']}: {hint} invisible to {principal}"
    if q["check"]["tool"] == "hydrate" and exp.get("error") == "not_found":
        assert not all(visible(principal, pep_number(h)) for h in q["check"]["atoms"])


def test_stage1_predates_supersession_headers() -> None:
    assert (649, 563) in supersedes_edges("latest")
    assert (649, 563) not in supersedes_edges("stage1")


def test_corpus_licence_statements() -> None:
    for n, path in files("latest").items():
        text = path.read_text().lower()
        assert "public domain" in text or "cc0" in text, f"PEP {n} lacks a licence statement"
