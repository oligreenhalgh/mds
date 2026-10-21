"""Errors raised by the graph engine."""


class EngineError(ValueError):
    """A traversal request is malformed (bad step, direction, hops or operator)."""


class LimitExceededError(EngineError):
    """`k`, path length or another bound is above its maximum (INTERFACES §5)."""
