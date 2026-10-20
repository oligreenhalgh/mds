import pytest
from pyroaring import BitMap

from mds.acl import ClassTable, MaskCache, Principal, Trust, UnknownPrincipalError
from mds.model import ModelError


def principals(**groups: list[str]) -> dict[str, Principal]:
    return {
        name: Principal(name=name, groups=frozenset(g), trust=Trust.PERSON)
        for name, g in groups.items()
    }


@pytest.fixture
def table() -> ClassTable:
    # 0: legal only, 1: staff, 2: legal or hr, 3: nobody
    return ClassTable({0: ["legal"], 1: ["staff"], 2: ["legal", "hr"], 3: []})


@pytest.fixture
def cache(table: ClassTable) -> MaskCache:
    atoms = [(10, 0), (11, 0), (20, 1), (21, 1), (22, 1), (30, 2), (40, 3)]
    return MaskCache(
        principals(alice=["staff", "legal"], agent=["staff"], hr=["hr"], nobody=[]),
        table,
        atoms,
    )


def test_mask_is_union_of_readable_classes(cache: MaskCache) -> None:
    assert cache.mask("alice") == BitMap([10, 11, 20, 21, 22, 30])
    assert cache.mask("agent") == BitMap([20, 21, 22])
    assert cache.mask("hr") == BitMap([30])
    assert cache.mask("nobody") == BitMap()


def test_class_with_no_groups_is_hidden_from_everyone(cache: MaskCache) -> None:
    for name in ("alice", "agent", "hr", "nobody"):
        assert 40 not in cache.mask(name)


def test_mask_is_cached_and_immutable(cache: MaskCache) -> None:
    first = cache.mask("agent")
    assert cache.is_cached("agent")
    assert cache.mask("agent") is first
    with pytest.raises(AttributeError):
        first.add(10)  # type: ignore[attr-defined]


def test_unknown_principal(cache: MaskCache) -> None:
    with pytest.raises(UnknownPrincipalError, match="'mallory'"):
        cache.mask("mallory")


def test_new_atom_invalidates_only_its_readers(cache: MaskCache) -> None:
    for name in ("alice", "agent", "hr"):
        cache.mask(name)
    cache.set_atoms([(12, 0)])  # legal only
    assert not cache.is_cached("alice")
    assert cache.is_cached("agent")
    assert cache.is_cached("hr")
    assert 12 in cache.mask("alice")
    assert 12 not in cache.mask("agent")


def test_moving_an_atom_invalidates_old_and_new_readers(cache: MaskCache) -> None:
    for name in ("alice", "agent", "hr"):
        cache.mask(name)
    cache.set_atoms([(20, 0)])  # staff -> legal
    assert not cache.is_cached("alice")
    assert not cache.is_cached("agent")
    assert cache.is_cached("hr")
    assert 20 not in cache.mask("agent")
    assert 20 in cache.mask("alice")
    assert cache.acl_class_of(20) == 0


def test_unchanged_pairs_keep_the_cache(cache: MaskCache) -> None:
    before = cache.mask("alice")
    cache.set_atoms([(10, 0), (20, 1)])
    assert cache.mask("alice") is before


def test_atom_in_newly_interned_class(table: ClassTable, cache: MaskCache) -> None:
    cache.mask("hr")
    cache.mask("agent")
    hr_only = table.intern(["hr"])
    cache.set_atoms([(50, hr_only)])
    assert not cache.is_cached("hr")
    assert cache.is_cached("agent")
    assert cache.mask("hr") == BitMap([30, 50])


def test_membership_change_drops_every_mask(cache: MaskCache) -> None:
    assert 10 not in cache.mask("agent")
    cache.set_principals(principals(agent=["staff", "legal"]))
    assert not cache.is_cached("agent")
    assert cache.mask("agent") == BitMap([10, 11, 20, 21, 22, 30])
    with pytest.raises(UnknownPrincipalError):
        cache.mask("alice")


def test_rule_change_is_applied_by_reclassifying_atoms(table: ClassTable) -> None:
    # Rules changed so legal/ is now readable by staff too: atoms move class.
    cache = MaskCache(principals(agent=["staff"]), table, [(10, 0), (20, 1)])
    assert cache.mask("agent") == BitMap([20])
    shared = table.intern(["legal", "staff"])
    cache.set_atoms([(10, shared)])
    assert cache.mask("agent") == BitMap([10, 20])


def test_clear_drops_everything(cache: MaskCache) -> None:
    cache.mask("alice")
    cache.clear()
    assert not cache.is_cached("alice")


def test_bad_pair_is_rejected_before_anything_changes(cache: MaskCache) -> None:
    before = cache.mask("agent")
    with pytest.raises(LookupError, match="unknown acl_class 99"):
        cache.set_atoms([(20, 0), (60, 99)])
    assert cache.acl_class_of(20) == 1
    assert cache.mask("agent") is before
    with pytest.raises(ModelError):
        cache.set_atoms([(-1, 1)])
