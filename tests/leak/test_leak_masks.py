"""Masks built from `mds.toml` never include atoms a principal's groups cannot read."""

from pathlib import Path

from mds.acl import ClassTable, MaskCache, acl_class_for_path, load_config

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mds.toml"

PATHS = {
    1: "legal/acme-contract.pdf",
    2: "legal/disputes/acme.md",
    3: "policies/retention.md",
    4: "readme.md",
}


def _cache() -> tuple[MaskCache, ClassTable]:
    cfg = load_config(FIXTURE)
    table = ClassTable()
    pairs = [(atom_id, acl_class_for_path(cfg, table, p)) for atom_id, p in PATHS.items()]
    return MaskCache(cfg.principals, table, pairs), table


def test_agent_without_legal_never_sees_legal_atoms() -> None:
    cache, _ = _cache()
    mask = cache.mask("research-agent")
    assert set(mask) == {3, 4}


def test_reclassification_to_a_hidden_class_removes_atom_from_mask() -> None:
    cache, table = _cache()
    assert 3 in cache.mask("research-agent")
    legal = table.intern(["legal"])
    cache.set_atoms([(3, legal)])
    assert 3 not in cache.mask("research-agent")
