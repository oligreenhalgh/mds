import pytest

from mds.model import (
    AuthorKind,
    Dimension,
    DimensionRegistry,
    EdgeState,
    ModelError,
    Operator,
    UnknownDimensionError,
    default_registry,
)


def test_default_registry_has_five_builtin_dimensions() -> None:
    reg = default_registry()
    assert len(reg) == 5
    assert [d.id for d in reg] == ["r0", "r1", "r2", "r3", "r4"]


@pytest.mark.parametrize(
    ("dim_id", "name"),
    [
        ("r0", "contains"),
        ("r1", "references"),
        ("r2", "co_occurs"),
        ("r3", "derived_from"),
        ("r4", "supersedes"),
    ],
)
def test_lookup_by_id_and_name_returns_same_dimension(dim_id: str, name: str) -> None:
    reg = default_registry()
    assert reg.get(dim_id) is reg.get(name)
    assert reg[dim_id].name == name
    assert reg[name].id == dim_id
    assert dim_id in reg and name in reg


def test_supersedes_exposes_all_fields() -> None:
    r4 = default_registry().get("supersedes")
    assert r4.time_bound is True
    assert r4.symmetric is False
    assert r4.default_operator is Operator.BOOLEAN
    assert r4.writers == (AuthorKind.EXTRACTOR, AuthorKind.AGENT, AuthorKind.PERSON)
    assert r4.agent_default_state is EdgeState.PENDING
    assert r4.description.startswith("a replaces or amends b")
    assert not r4.description.endswith("\n")


def test_only_supersedes_is_time_bound() -> None:
    assert [d.name for d in default_registry() if d.time_bound] == ["supersedes"]


def test_co_occurs_is_symmetric_max_plus() -> None:
    r2 = default_registry().get("r2")
    assert r2.symmetric is True
    assert r2.default_operator is Operator.MAX_PLUS
    assert [d.id for d in default_registry() if d.symmetric] == ["r2"]


@pytest.mark.parametrize("key", ["r9", "cites", "", "R4", "Supersedes"])
def test_unknown_dimension_raises_clear_error(key: str) -> None:
    with pytest.raises(UnknownDimensionError, match="unknown dimension") as info:
        default_registry().get(key)
    assert "r4 (supersedes)" in str(info.value)
    assert key not in default_registry()


def test_unknown_dimension_via_getitem() -> None:
    with pytest.raises(UnknownDimensionError):
        default_registry()["nope"]


def test_default_registry_is_cached() -> None:
    assert default_registry() is default_registry()


def test_dimension_is_frozen() -> None:
    r0 = default_registry().get("r0")
    with pytest.raises(AttributeError):
        r0.time_bound = True  # type: ignore[misc]


VALID_ENTRY = """
- id: r9
  name: cites
  symmetric: false
  time_bound: false
  default_operator: count
  writers: [agent]
  agent_default_state: confirmed
  description: a cites b.
"""


def test_from_yaml_parses_custom_registry() -> None:
    reg = DimensionRegistry.from_yaml(VALID_ENTRY)
    cites = reg.get("cites")
    assert cites == Dimension(
        id="r9",
        name="cites",
        symmetric=False,
        time_bound=False,
        default_operator=Operator.COUNT,
        writers=(AuthorKind.AGENT,),
        agent_default_state=EdgeState.CONFIRMED,
        description="a cites b.",
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("  default_operator: count\n", "", "missing default_operator"),
        ("  description: a cites b.\n", "  description: x\n  colour: red\n", "unknown fields"),
        ("default_operator: count", "default_operator: min_plus", "min_plus"),
        ("writers: [agent]", "writers: [robot]", "robot"),
        ("agent_default_state: confirmed", "agent_default_state: maybe", "maybe"),
        ("time_bound: false", "time_bound: 'no'", "time_bound must be true or false"),
    ],
)
def test_from_yaml_rejects_malformed_entries(old: str, new: str, message: str) -> None:
    with pytest.raises(ModelError, match=message):
        DimensionRegistry.from_yaml(VALID_ENTRY.replace(old, new))


def test_from_yaml_rejects_non_list() -> None:
    with pytest.raises(ModelError, match="list of mappings"):
        DimensionRegistry.from_yaml("id: r0\n")


def test_duplicate_ids_or_names_rejected() -> None:
    clash = VALID_ENTRY + VALID_ENTRY.replace("id: r9", "id: r8")
    with pytest.raises(ModelError, match="duplicate dimension id or name: cites"):
        DimensionRegistry.from_yaml(clash)
