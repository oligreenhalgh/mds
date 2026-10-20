"""Load and validate `mds.toml` (INTERFACES.md section 4, DESIGN.md section 7.1)."""

import dataclasses
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from mds.acl.errors import (
    ConfigError,
    UnknownConfigDimensionError,
    UnknownPrincipalError,
    UnknownTrustError,
)
from mds.acl.glob import compile_glob, normalise_path
from mds.model import (
    DimensionRegistry,
    EdgeState,
    UnknownDimensionError,
    default_registry,
)

DEFAULT_DATA_DIR = ".mds"

_TOP_LEVEL = {"store", "principals", "acl_rules", "dimensions"}
_STORE_KEYS = {"data_dir"}
_PRINCIPAL_KEYS = {"name", "groups", "trust"}
_RULE_KEYS = {"glob", "groups"}
# Registry fields a deployment may change per dimension. Meaning and shape stay fixed.
_DIMENSION_OVERRIDES = {"agent_default_state"}


class Trust(StrEnum):
    """How far MDS trusts a principal's writes (DESIGN §10.2)."""

    PERSON = "person"
    AGENT_HIGH = "agent_high"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class Principal:
    """A user or service an agent acts for, with the groups it belongs to."""

    name: str
    groups: frozenset[str]
    trust: Trust


@dataclass(frozen=True, slots=True)
class AclRule:
    """One `[[acl_rules]]` entry: paths matching `glob` are readable by `groups`."""

    glob: str
    groups: frozenset[str]
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern", compile_glob(self.glob))

    def matches(self, path: str) -> bool:
        """True if `path` (already normalised) matches this rule's glob."""
        return self.pattern.fullmatch(path) is not None


@dataclass(frozen=True, slots=True)
class Config:
    """A validated `mds.toml`."""

    data_dir: Path
    principals: Mapping[str, Principal]
    acl_rules: tuple[AclRule, ...]
    dimension_overrides: Mapping[str, Mapping[str, Any]]

    def principal(self, name: str) -> Principal:
        """Return the named principal, or raise `UnknownPrincipalError`."""
        return lookup_principal(self.principals, name)

    def groups_for_path(self, path: str | PurePosixPath) -> frozenset[str]:
        """Groups allowed to read `path`: the first matching rule wins.

        A path no rule matches is readable by nobody (fail closed).
        """
        norm = normalise_path(path)
        for rule in self.acl_rules:
            if rule.matches(norm):
                return rule.groups
        return frozenset()

    def registry(self, base: DimensionRegistry | None = None) -> DimensionRegistry:
        """The dimension registry with this config's `[dimensions.<id>]` overrides applied."""
        base = base or default_registry()
        return DimensionRegistry(
            [dataclasses.replace(d, **self.dimension_overrides.get(d.id, {})) for d in base]
        )


def lookup_principal(principals: Mapping[str, Principal], name: str) -> Principal:
    """Return `principals[name]`, or raise `UnknownPrincipalError` listing the known names."""
    try:
        return principals[name]
    except KeyError:
        known = ", ".join(sorted(principals)) or "none"
        raise UnknownPrincipalError(
            f"unknown principal {name!r}; known principals: {known}"
        ) from None


def load_config(path: str | Path, registry: DimensionRegistry | None = None) -> Config:
    """Read and validate `mds.toml`. `data_dir` is resolved against the file's directory."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return parse_config(raw, base_dir=path.parent, registry=registry)


def parse_config_text(
    text: str, base_dir: Path | None = None, registry: DimensionRegistry | None = None
) -> Config:
    """Parse `mds.toml` content from a string."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    return parse_config(raw, base_dir=base_dir, registry=registry)


def parse_config(
    raw: Mapping[str, Any],
    base_dir: Path | None = None,
    registry: DimensionRegistry | None = None,
) -> Config:
    """Validate an already-parsed TOML document and build a `Config`."""
    _check_keys(raw, _TOP_LEVEL, "mds.toml")
    store = _table(raw.get("store", {}), "[store]")
    _check_keys(store, _STORE_KEYS, "[store]")
    data_dir = Path(_string(store.get("data_dir", DEFAULT_DATA_DIR), "[store] data_dir"))
    if base_dir is not None and not data_dir.is_absolute():
        data_dir = base_dir / data_dir

    principals: dict[str, Principal] = {}
    for i, entry in enumerate(_array(raw.get("principals", []), "[[principals]]")):
        where = f"[[principals]] #{i + 1}"
        entry = _table(entry, where)
        _check_keys(entry, _PRINCIPAL_KEYS, where, required=_PRINCIPAL_KEYS)
        name = _string(entry["name"], f"{where} name")
        if name in principals:
            raise ConfigError(f"{where}: duplicate principal name {name!r}")
        principals[name] = Principal(
            name=name,
            groups=_groups(entry["groups"], f"principal {name!r} groups"),
            trust=_trust(entry["trust"], name),
        )

    rules: list[AclRule] = []
    for i, entry in enumerate(_array(raw.get("acl_rules", []), "[[acl_rules]]")):
        where = f"[[acl_rules]] #{i + 1}"
        entry = _table(entry, where)
        _check_keys(entry, _RULE_KEYS, where, required=_RULE_KEYS)
        glob = _string(entry["glob"], f"{where} glob")
        groups = _groups(entry["groups"], f"{where} groups")
        try:
            rules.append(AclRule(glob=glob, groups=groups))
        except ConfigError as exc:
            raise ConfigError(f"{where}: {exc}") from exc

    overrides = _dimension_overrides(
        _table(raw.get("dimensions", {}), "[dimensions]"), registry or default_registry()
    )
    return Config(
        data_dir=data_dir,
        principals=MappingProxyType(principals),
        acl_rules=tuple(rules),
        dimension_overrides=MappingProxyType(overrides),
    )


def _dimension_overrides(
    raw: Mapping[str, Any], registry: DimensionRegistry
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, table in raw.items():
        try:
            dim = registry.get(key)
        except UnknownDimensionError as exc:
            raise UnknownConfigDimensionError(f"[dimensions.{key}]: {exc}") from None
        if dim.id in out:
            raise ConfigError(f"[dimensions.{key}]: {dim.id} ({dim.name}) is configured twice")
        where = f"[dimensions.{key}]"
        table = _table(table, where)
        _check_keys(table, _DIMENSION_OVERRIDES, where)
        values: dict[str, Any] = {}
        if "agent_default_state" in table:
            state = table["agent_default_state"]
            try:
                values["agent_default_state"] = EdgeState(state)
            except (ValueError, TypeError):
                allowed = ", ".join(s.value for s in EdgeState)
                raise ConfigError(
                    f"{where} agent_default_state must be one of {allowed}, got {state!r}"
                ) from None
        out[dim.id] = values
    return out


def _check_keys(
    table: Mapping[str, Any],
    allowed: set[str],
    where: str,
    required: set[str] | None = None,
) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown keys {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )
    missing = sorted((required or set()) - set(table))
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(missing)}")


def _table(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table")
    return value


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{where} must be an array of tables")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a non-empty string")
    return value


def _groups(value: Any, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(g, str) and g for g in value):
        raise ConfigError(f"{where} must be a list of non-empty strings")
    return frozenset(value)


def _trust(value: Any, name: str) -> Trust:
    try:
        return Trust(value)
    except (ValueError, TypeError):
        allowed = ", ".join(t.value for t in Trust)
        raise UnknownTrustError(
            f"principal {name!r}: unknown trust level {value!r}; allowed: {allowed}"
        ) from None
