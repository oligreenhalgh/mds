"""Errors raised by seeding."""


class SeedError(ValueError):
    """A seed request failed validation (error code `bad_request`, INTERFACES §5)."""

    code = "bad_request"


class LimitExceededError(SeedError):
    """`k` is above the maximum allowed by `mds_seed` (INTERFACES §2)."""

    code = "limit_exceeded"
