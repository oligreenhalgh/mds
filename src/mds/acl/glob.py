"""Path globs for `[[acl_rules]]`, matched against paths relative to the ingest root.

Syntax (case-sensitive, `/` separates segments):

- `*` matches any run of characters within one segment.
- `?` matches one character within a segment.
- `[abc]`, `[a-z]`, `[!abc]` match one character from (or not from) a set.
- `**` as a whole segment matches zero or more segments: `legal/**` matches
  everything under `legal/`, `**/draft.md` matches `draft.md` at any depth and
  `**` alone matches every path.
"""

import re
from pathlib import PurePosixPath

from mds.acl.errors import ConfigError


def compile_glob(glob: str) -> re.Pattern[str]:
    """Compile an ACL glob to a regex that must match a whole relative path."""
    if not glob:
        raise ConfigError("acl rule glob must not be empty")
    if glob.startswith("/"):
        raise ConfigError(f"acl rule glob must be relative to the ingest root, got {glob!r}")
    segments = glob.split("/")
    if "" in segments:
        raise ConfigError(f"acl rule glob has an empty segment: {glob!r}")
    parts: list[str] = []
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        if segment == "**":
            if i == last:
                # `a/**` is everything under `a/`; `**` alone is everything.
                parts.append(".*" if i == 0 else "/.*")
            else:
                parts.append("(?:.*/)?" if i == 0 else "/(?:.*/)?")
            continue
        if "**" in segment:
            raise ConfigError(f"'**' must be a whole path segment in glob {glob!r}")
        sep = "" if i == 0 or segments[i - 1] == "**" else "/"
        parts.append(sep + _translate_segment(segment, glob))
    return re.compile("".join(parts))


def _translate_segment(segment: str, glob: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        c = segment[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            start = i + 1
            negate = segment[start : start + 1] == "!"
            if negate:
                start += 1
            # A `]` straight after the opening bracket is a literal member.
            end = segment.find("]", start + 1)
            if end == -1:
                raise ConfigError(f"unclosed '[' in glob {glob!r}")
            body = "".join("\\" + ch if ch in "\\^[]" else ch for ch in segment[start:end])
            out.append(f"[^/{body}]" if negate else f"[{body}]")
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def normalise_path(path: str | PurePosixPath) -> str:
    """Return `path` as a clean relative POSIX path, rejecting absolute and `..` paths."""
    p = PurePosixPath(path)
    if p.is_absolute():
        raise ValueError(f"path must be relative to the ingest root, got {str(path)!r}")
    if ".." in p.parts:
        raise ValueError(f"path must not contain '..', got {str(path)!r}")
    text = str(p)
    if text == ".":
        raise ValueError("path must not be empty")
    return text
