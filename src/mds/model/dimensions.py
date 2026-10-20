"""Dimension registry loaded from `dimensions.yaml` (DESIGN.md section 3.2)."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

import yaml

from mds.model.enums import AuthorKind, EdgeState, Operator
from mds.model.errors import ModelError, UnknownDimensionError

_FIELDS = (
    "id",
    "name",
    "symmetric",
    "time_bound",
    "default_operator",
    "writers",
    "agent_default_state",
    "description",
)


@dataclass(frozen=True, slots=True)
class Dimension:
    """A kind of relationship with fixed meaning and rules."""

    id: str
    name: str
    symmetric: bool
    time_bound: bool
    default_operator: Operator
    writers: tuple[AuthorKind, ...]
    agent_default_state: EdgeState
    description: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Dimension":
        """Build a dimension from one registry entry, raising `ModelError` if malformed."""
        missing = [f for f in _FIELDS if f not in raw]
        if missing:
            raise ModelError(f"dimension {raw.get('id', '?')} is missing {', '.join(missing)}")
        unknown = sorted(set(raw) - set(_FIELDS))
        if unknown:
            raise ModelError(f"dimension {raw['id']} has unknown fields {', '.join(unknown)}")
        for flag in ("symmetric", "time_bound"):
            if not isinstance(raw[flag], bool):
                raise ModelError(f"dimension {raw['id']}: {flag} must be true or false")
        try:
            return cls(
                id=str(raw["id"]),
                name=str(raw["name"]),
                symmetric=raw["symmetric"],
                time_bound=raw["time_bound"],
                default_operator=Operator(raw["default_operator"]),
                writers=tuple(AuthorKind(w) for w in raw["writers"]),
                agent_default_state=EdgeState(raw["agent_default_state"]),
                description=str(raw["description"]).strip(),
            )
        except ValueError as exc:
            raise ModelError(f"dimension {raw['id']}: {exc}") from exc


class DimensionRegistry:
    """Dimensions looked up by id (`r4`) or name (`supersedes`)."""

    def __init__(self, dimensions: list[Dimension]) -> None:
        self._dims: tuple[Dimension, ...] = tuple(dimensions)
        self._by_key: dict[str, Dimension] = {}
        for dim in self._dims:
            for key in (dim.id, dim.name):
                if key in self._by_key:
                    raise ModelError(f"duplicate dimension id or name: {key}")
                self._by_key[key] = dim

    def get(self, key: str) -> Dimension:
        """Return the dimension with this id or name, or raise `UnknownDimensionError`."""
        try:
            return self._by_key[key]
        except KeyError:
            known = ", ".join(f"{d.id} ({d.name})" for d in self._dims)
            raise UnknownDimensionError(
                f"unknown dimension {key!r}; known dimensions: {known}"
            ) from None

    def __getitem__(self, key: str) -> Dimension:
        return self.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __iter__(self) -> Iterator[Dimension]:
        return iter(self._dims)

    def __len__(self) -> int:
        return len(self._dims)

    @classmethod
    def from_yaml(cls, text: str) -> "DimensionRegistry":
        """Parse a registry from YAML text: a list of dimension entries."""
        raw = yaml.safe_load(text)
        if not isinstance(raw, list) or not all(isinstance(e, dict) for e in raw):
            raise ModelError("dimension registry must be a list of mappings")
        return cls([Dimension.from_mapping(entry) for entry in raw])


@cache
def default_registry() -> DimensionRegistry:
    """The built-in dimensions shipped in `mds/model/dimensions.yaml`."""
    text = files("mds.model").joinpath("dimensions.yaml").read_text(encoding="utf-8")
    return DimensionRegistry.from_yaml(text)
