"""Errors raised by the data model, plus small shared checks."""

from enum import StrEnum

ATOM_ID_LIMIT = 2**32


class ModelError(ValueError):
    """A record breaks a rule in DESIGN.md."""


class UnknownDimensionError(LookupError):
    """A dimension id or name is not in the registry."""


def check_atom_id(value: int, field: str = "atom_id") -> None:
    """Raise `ModelError` unless `value` is an int in [0, 2**32)."""
    # bool is an int subclass; True as an atom id is always a bug.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ModelError(f"{field} must be an int, got {type(value).__name__}")
    if not 0 <= value < ATOM_ID_LIMIT:
        raise ModelError(f"{field} must be in [0, 2**32), got {value}")


def check_non_negative(value: int, field: str) -> None:
    """Raise `ModelError` unless `value` is an int >= 0."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ModelError(f"{field} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ModelError(f"{field} must be >= 0, got {value}")


def check_csn_interval(csn_from: int, csn_to: int | None) -> None:
    """Raise `ModelError` unless `csn_from` is before `csn_to` (when set)."""
    check_non_negative(csn_from, "csn_from")
    if csn_to is not None:
        check_non_negative(csn_to, "csn_to")
        if csn_to <= csn_from:
            raise ModelError(f"csn_from ({csn_from}) must be before csn_to ({csn_to})")


def as_enum[E: StrEnum](enum_cls: type[E], value: str, field: str) -> E:
    """Return `value` as a member of `enum_cls`, raising `ModelError` if it is not one."""
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ModelError(f"{field} must be one of {allowed}, got {value!r}") from None
