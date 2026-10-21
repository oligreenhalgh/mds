"""Tokenisers shared by indexing and querying, so both sides see the same terms."""

import tantivy

LEXICAL_TOKENIZER = "mds_lexical"
MAX_TOKEN_LENGTH = 40


def lexical_analyzer() -> tantivy.TextAnalyzer:
    """Split on non-alphanumerics, lowercase, fold accents, English stemming."""
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(MAX_TOKEN_LENGTH))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.ascii_fold())
        .filter(tantivy.Filter.stemmer("english"))
        .build()
    )


def name_analyzer() -> tantivy.TextAnalyzer:
    """Like `lexical_analyzer` without stemming: entity names match word for word."""
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(MAX_TOKEN_LENGTH))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.ascii_fold())
        .build()
    )


def first_line(text: str) -> str:
    """The first non-blank line of `text`, stripped."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
