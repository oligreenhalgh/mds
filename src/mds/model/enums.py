"""Enumerations used by the data model (DESIGN.md sections 2, 4 and 5.3)."""

from enum import StrEnum


class AtomKind(StrEnum):
    """What an atom is (DESIGN §2)."""

    DOCUMENT = "document"
    SECTION = "section"
    ITEM = "item"
    ENTITY = "entity"
    PLACEHOLDER = "placeholder"


class AuthorKind(StrEnum):
    """Who made an edge (DESIGN §4)."""

    EXTRACTOR = "extractor"
    AGENT = "agent"
    PERSON = "person"


class EdgeState(StrEnum):
    """Review state of an edge (DESIGN §4)."""

    CONFIRMED = "confirmed"
    PENDING = "pending"
    REJECTED = "rejected"


class Operator(StrEnum):
    """Semiring used to combine paths in a traversal (DESIGN §5.3, INTERFACES §2)."""

    BOOLEAN = "boolean"
    COUNT = "count"
    MAX_PLUS = "max_plus"
    BOTTLENECK = "bottleneck"
