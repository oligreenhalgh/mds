"""reStructuredText section headings, without docutils.

A section title is a non-indented text line with an adornment line under it
(and optionally a matching one over it), made of one repeated punctuation
character at least as long as the title. As in docutils, levels follow the
order in which adornment styles first appear.
"""

from collections.abc import Sequence

from mds.ingest.parse import Heading, line_starts

ADORNMENT_CHARS = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _adornment(line: str) -> str | None:
    """The adornment character if `line` is an adornment line, else None."""
    if len(line) < 2 or line[0] not in ADORNMENT_CHARS or line.strip(line[0]):
        return None
    return line[0]


def _is_title_text(line: str) -> bool:
    return bool(line) and not line[0].isspace() and _adornment(line) is None


def rst_headings(lines: Sequence[str], body_start: int = 0) -> list[Heading]:
    """Headings in document order, skipping a leading header block that ends at `body_start`."""
    starts = line_starts(lines)
    styles: list[tuple[str, bool]] = []
    headings: list[Heading] = []
    i = body_start
    while i < len(lines):
        found = _heading_at(lines, i)
        if found is None:
            i += 1
            continue
        title_line, char, overline = found
        title = lines[title_line].strip()
        style = (char, overline)
        if style not in styles:
            styles.append(style)
        first = title_line - 1 if overline else title_line
        headings.append(
            Heading(
                level=styles.index(style) + 1,
                title=title,
                start=starts[first],
                origin={"line": first + 1},
            )
        )
        i = title_line + 2
    return headings


def _heading_at(lines: Sequence[str], i: int) -> tuple[int, str, bool] | None:
    """If a heading starts at line `i`, return (title line, adornment char, has overline)."""
    if i > 0 and lines[i - 1]:
        return None
    line = lines[i]
    over = _adornment(line)
    if over is not None and i + 2 < len(lines):
        # Overline style: the title may be inset, so only the underline must match.
        text = lines[i + 1].strip()
        under = lines[i + 2]
        if text and _adornment(under) == over and len(under) == len(line) >= len(text):
            return i + 1, over, True
        return None
    if _is_title_text(line) and i + 1 < len(lines):
        under = lines[i + 1]
        char = _adornment(under)
        if char is not None and len(under) >= len(line):
            return i, char, False
    return None
