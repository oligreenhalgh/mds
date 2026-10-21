"""Typed traversal paths: ordered steps over dimensions (DESIGN §5.2, INTERFACES §2)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mds.engine.errors import EngineError, LimitExceededError
from mds.model import DimensionRegistry

MAX_PATH_STEPS = 8
STAR: Literal["*"] = "*"

Direction = Literal["fwd", "rev"]
Hops = int | Literal["*"]


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a path. `dim` is a dimension id; `hops` is `n >= 1` or `"*"`.

    An integer `n` follows exactly `n` hops. `"*"` is the closure: the frontier
    itself plus everything reachable in one or more hops, repeated until it
    stops changing or the hop limit is reached.
    """

    dim: str
    direction: Direction
    hops: Hops


StepLike = Step | Sequence[Any] | Mapping[str, Any]


def parse_step(raw: StepLike, registry: DimensionRegistry, hop_limit: int) -> Step:
    """Normalise a step given as a `Step`, a `(dim, direction, hops)` tuple or a mapping."""
    if isinstance(raw, Step):
        dim, direction, hops = raw.dim, raw.direction, raw.hops
    elif isinstance(raw, Mapping):
        try:
            dim, direction, hops = raw["dim"], raw["direction"], raw["hops"]
        except KeyError as exc:
            raise EngineError(f"step is missing {exc.args[0]!r}") from None
    elif isinstance(raw, Sequence) and not isinstance(raw, str) and len(raw) == 3:
        dim, direction, hops = raw
    else:
        raise EngineError(f"a step needs dim, direction and hops, got {raw!r}")

    if not isinstance(dim, str):
        raise EngineError(f"dim must be a dimension id or name, got {dim!r}")
    dim_id = registry.get(dim).id
    if direction not in ("fwd", "rev"):
        raise EngineError(f"direction must be 'fwd' or 'rev', got {direction!r}")
    return Step(dim=dim_id, direction=direction, hops=_check_hops(hops, hop_limit))


def parse_path(
    raw: Sequence[StepLike], registry: DimensionRegistry, hop_limit: int
) -> tuple[Step, ...]:
    """Normalise a whole path of 1 to `MAX_PATH_STEPS` steps."""
    if isinstance(raw, str | bytes) or not isinstance(raw, Sequence):
        raise EngineError("path must be a list of steps")
    if not raw:
        raise EngineError("path needs at least one step")
    if len(raw) > MAX_PATH_STEPS:
        raise LimitExceededError(f"path has {len(raw)} steps; the maximum is {MAX_PATH_STEPS}")
    return tuple(parse_step(s, registry, hop_limit) for s in raw)


def _check_hops(hops: object, hop_limit: int) -> Hops:
    if hops == STAR:
        return STAR
    if not isinstance(hops, int) or isinstance(hops, bool) or hops < 1:
        raise EngineError(f"hops must be an int >= 1 or '*', got {hops!r}")
    if hops > hop_limit:
        raise LimitExceededError(f"hops is {hops}; the maximum is {hop_limit}")
    return hops
