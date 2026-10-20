from pathlib import Path

import pytest

from mds.acl import (
    ConfigError,
    Trust,
    UnknownConfigDimensionError,
    UnknownPrincipalError,
    UnknownTrustError,
    load_config,
    parse_config_text,
)
from mds.model import EdgeState, UnknownDimensionError, default_registry

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mds.toml"

PRINCIPAL = """
[[principals]]
name = "alice"
groups = ["staff"]
trust = "person"
"""


def test_loads_interfaces_example() -> None:
    cfg = load_config(FIXTURE)
    assert cfg.data_dir == FIXTURE.parent / ".mds"
    alice = cfg.principal("alice")
    assert alice.groups == {"staff", "legal"}
    assert alice.trust is Trust.PERSON
    assert cfg.principal("research-agent").trust is Trust.AGENT
    assert [r.glob for r in cfg.acl_rules] == ["legal/**", "**"]
    assert cfg.dimension_overrides == {"r4": {"agent_default_state": EdgeState.PENDING}}


def test_empty_config_uses_defaults() -> None:
    cfg = parse_config_text("")
    assert cfg.data_dir == Path(".mds")
    assert cfg.principals == {}
    assert cfg.acl_rules == ()


def test_absolute_data_dir_is_not_rebased(tmp_path: Path) -> None:
    cfg = parse_config_text(f'[store]\ndata_dir = "{tmp_path}"', base_dir=Path("/elsewhere"))
    assert cfg.data_dir == tmp_path


def test_first_matching_rule_wins() -> None:
    cfg = load_config(FIXTURE)
    assert cfg.groups_for_path("legal/contracts/acme.pdf") == {"legal"}
    assert cfg.groups_for_path("policies/retention.md") == {"staff"}


def test_rule_order_decides_when_both_match() -> None:
    cfg = parse_config_text(
        """
        [[acl_rules]]
        glob = "**"
        groups = ["staff"]

        [[acl_rules]]
        glob = "legal/**"
        groups = ["legal"]
        """
    )
    # The catch-all comes first, so the narrower rule never applies.
    assert cfg.groups_for_path("legal/acme.pdf") == {"staff"}


def test_unmatched_path_is_readable_by_nobody() -> None:
    cfg = parse_config_text('[[acl_rules]]\nglob = "public/*"\ngroups = ["staff"]')
    assert cfg.groups_for_path("private/secret.md") == frozenset()


def test_groups_for_path_normalises_and_rejects_bad_paths() -> None:
    cfg = load_config(FIXTURE)
    assert cfg.groups_for_path("./legal/acme.pdf") == {"legal"}
    with pytest.raises(ValueError, match="relative"):
        cfg.groups_for_path("/legal/acme.pdf")
    with pytest.raises(ValueError, match=r"\.\."):
        cfg.groups_for_path("public/../legal/acme.pdf")


def test_unknown_principal_lists_known_names() -> None:
    cfg = load_config(FIXTURE)
    with pytest.raises(UnknownPrincipalError, match="'bob'.*alice, research-agent"):
        cfg.principal("bob")


def test_unknown_principal_is_a_lookup_and_config_error() -> None:
    err = UnknownPrincipalError("x")
    assert isinstance(err, LookupError)
    assert isinstance(err, ConfigError)
    assert err.code == "bad_request"


def test_unknown_trust_level() -> None:
    with pytest.raises(UnknownTrustError, match="'superuser'.*person, agent_high, agent"):
        parse_config_text(PRINCIPAL.replace('"person"', '"superuser"'))


def test_duplicate_principal() -> None:
    with pytest.raises(ConfigError, match="duplicate principal name 'alice'"):
        parse_config_text(PRINCIPAL + PRINCIPAL)


@pytest.mark.parametrize("missing", ["name", "groups", "trust"])
def test_principal_missing_field(missing: str) -> None:
    lines = [ln for ln in PRINCIPAL.splitlines() if not ln.startswith(missing)]
    with pytest.raises(ConfigError, match=f"missing {missing}"):
        parse_config_text("\n".join(lines))


def test_principal_groups_must_be_strings() -> None:
    with pytest.raises(ConfigError, match="list of non-empty strings"):
        parse_config_text(PRINCIPAL.replace('["staff"]', "[1]"))


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[seed]\nk = 5", "unknown keys seed"),
        ('[store]\ndata_dir = ".mds"\npath = "x"', r"\[store\]: unknown keys path"),
        ('[store]\ndata_dir = ""', "non-empty string"),
        (PRINCIPAL + 'role = "admin"', "unknown keys role"),
        ('[[acl_rules]]\nglob = "**"', "missing groups"),
        ('[[acl_rules]]\nglob = "/abs/**"\ngroups = []', "relative to the ingest root"),
        ('[[acl_rules]]\nglob = "a/**b"\ngroups = []', "whole path segment"),
        ('[[acl_rules]]\nglob = "a//b"\ngroups = []', "empty segment"),
        ('[[acl_rules]]\nglob = "[ab"\ngroups = []', "unclosed"),
        ("principals = 3", "array of tables"),
        ("store = 3", "must be a table"),
        ("not toml =", "invalid TOML"),
    ],
)
def test_invalid_input_raises_config_error(text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config_text(text)


def test_invalid_toml_file_names_the_file(tmp_path: Path) -> None:
    bad = tmp_path / "mds.toml"
    bad.write_text("[[principals]\n")
    with pytest.raises(ConfigError, match="mds.toml: invalid TOML"):
        load_config(bad)


def test_unknown_dimension() -> None:
    with pytest.raises(UnknownConfigDimensionError, match=r"\[dimensions.r9\].*known dimensions"):
        parse_config_text('[dimensions.r9]\nagent_default_state = "pending"')


def test_unknown_dimension_error_carries_its_code() -> None:
    err = UnknownConfigDimensionError("x")
    assert isinstance(err, UnknownDimensionError)
    assert isinstance(err, ConfigError)
    assert err.code == "unknown_dimension"


def test_dimension_override_by_name_is_stored_by_id() -> None:
    cfg = parse_config_text('[dimensions.references]\nagent_default_state = "confirmed"')
    assert cfg.dimension_overrides == {"r1": {"agent_default_state": EdgeState.CONFIRMED}}


def test_dimension_configured_twice_by_id_and_name() -> None:
    with pytest.raises(ConfigError, match="configured twice"):
        parse_config_text(
            '[dimensions.r1]\nagent_default_state = "pending"\n'
            '[dimensions.references]\nagent_default_state = "pending"'
        )


def test_dimension_override_rejects_bad_state_and_fixed_fields() -> None:
    with pytest.raises(ConfigError, match="agent_default_state must be one of"):
        parse_config_text('[dimensions.r4]\nagent_default_state = "maybe"')
    with pytest.raises(ConfigError, match="unknown keys symmetric"):
        parse_config_text("[dimensions.r4]\nsymmetric = true")


def test_registry_applies_overrides_only_where_set() -> None:
    cfg = parse_config_text('[dimensions.r1]\nagent_default_state = "confirmed"')
    reg = cfg.registry()
    base = default_registry()
    assert reg["r1"].agent_default_state is EdgeState.CONFIRMED
    assert base["r1"].agent_default_state is EdgeState.PENDING
    assert reg["r4"] == base["r4"]
    assert [d.id for d in reg] == [d.id for d in base]
