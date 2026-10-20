"""Errors raised while loading config and computing permissions."""

from mds.model import UnknownDimensionError


class ConfigError(ValueError):
    """`mds.toml` failed validation (error code `bad_request`, INTERFACES §5)."""

    code = "bad_request"


class UnknownPrincipalError(ConfigError, LookupError):
    """A principal name is not declared in `[[principals]]`."""


class UnknownTrustError(ConfigError):
    """A principal's `trust` is not one of `person`, `agent_high`, `agent`."""


class UnknownConfigDimensionError(ConfigError, UnknownDimensionError):
    """A `[dimensions.<id>]` table names a dimension that is not in the registry."""

    code = "unknown_dimension"
