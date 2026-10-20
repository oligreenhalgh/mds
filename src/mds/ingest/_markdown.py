"""Markdown section headings (ATX `#` and setext underlines), skipping fenced code blocks."""

import re
from collections.abc import Sequence

from mds.ingest.parse import Heading, line_starts

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")


def markdown_headings(lines: Sequence[str]) -> list[Heading]:
    """Headings in document order. Levels are the Markdown levels (1-6)."""
    starts = line_starts(lines)
    headings: list[Heading] = []
    fence: str | None = None
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            continue
        if match:
            fence = match.group(1)
            continue
        atx = _ATX.match(line)
        if atx:
            title = (atx.group(2) or "").strip()
            if title:
                headings.append(Heading(len(atx.group(1)), title, starts[i], {"line": i + 1}))
            continue
        setext = _SETEXT.match(line)
        if setext and i > 0 and _paragraph_line(lines, i - 1):
            level = 1 if setext.group(1)[0] == "=" else 2
            title = lines[i - 1].strip()
            headings.append(Heading(level, title, starts[i - 1], {"line": i}))
    return headings


def _paragraph_line(lines: Sequence[str], i: int) -> bool:
    """A setext title is a single non-blank, non-indented line after a blank line or the start."""
    line = lines[i]
    if not line.strip() or line.startswith("    ") or _ATX.match(line) or _FENCE.match(line):
        return False
    return i == 0 or not lines[i - 1].strip()
