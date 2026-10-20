import pytest

from mds.acl import compile_glob


@pytest.mark.parametrize(
    ("glob", "path", "expected"),
    [
        # `**` alone matches everything.
        ("**", "a.md", True),
        ("**", "a/b/c.md", True),
        # Trailing `**` matches anything below the directory, not siblings.
        ("legal/**", "legal/a.pdf", True),
        ("legal/**", "legal/x/y/a.pdf", True),
        ("legal/**", "legal-old/a.pdf", False),
        ("legal/**", "other/legal/a.pdf", False),
        # Leading and middle `**` match zero or more segments.
        ("**/draft.md", "draft.md", True),
        ("**/draft.md", "a/b/draft.md", True),
        ("**/draft.md", "a/b/final-draft.md", False),
        ("a/**/z.md", "a/z.md", True),
        ("a/**/z.md", "a/b/c/z.md", True),
        ("a/**/z.md", "ab/z.md", False),
        # `*` and `?` stay inside one segment.
        ("*.md", "a.md", True),
        ("*.md", "dir/a.md", False),
        ("legal/*.pdf", "legal/a.pdf", True),
        ("legal/*.pdf", "legal/x/a.pdf", False),
        ("policy-20??.md", "policy-2021.md", True),
        ("policy-20??.md", "policy-202.md", False),
        ("a?b", "a/b", False),
        # Character classes, negation and literal brackets.
        ("v[12].md", "v1.md", True),
        ("v[12].md", "v3.md", False),
        ("v[!12].md", "v3.md", True),
        ("v[!12].md", "v1.md", False),
        ("[a-c]*.md", "beta.md", True),
        ("[a-c]*.md", "delta.md", False),
        ("x[]]y", "x]y", True),
        # Regex metacharacters are literal; matching is case-sensitive.
        ("notes (v1).md", "notes (v1).md", True),
        ("a+b.md", "aab.md", False),
        ("Legal/**", "legal/a.pdf", False),
        # Whole-path match: no partial prefixes or suffixes.
        ("legal", "legal/a.pdf", False),
        ("a.md", "xa.md", False),
    ],
)
def test_glob_matching(glob: str, path: str, expected: bool) -> None:
    assert (compile_glob(glob).fullmatch(path) is not None) is expected
