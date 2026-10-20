from pathlib import Path

import pytest

from mds.acl import ClassTable, acl_class_for_path, load_config

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mds.toml"


def test_intern_assigns_small_sequential_ids() -> None:
    table = ClassTable()
    assert table.intern(["legal"]) == 0
    assert table.intern(["staff"]) == 1
    assert table.intern([]) == 2
    assert len(table) == 3


def test_intern_is_stable_and_order_insensitive() -> None:
    table = ClassTable()
    first = table.intern(["staff", "legal"])
    assert table.intern(["legal", "staff"]) == first
    assert table.intern(["legal", "staff", "legal"]) == first
    assert len(table) == 1


def test_groups_round_trip() -> None:
    table = ClassTable()
    c = table.intern(["staff", "legal"])
    assert table.groups(c) == {"staff", "legal"}
    assert c in table
    with pytest.raises(LookupError, match="unknown acl_class 7"):
        table.groups(7)


def test_reloaded_table_keeps_ids_and_continues_after_the_highest() -> None:
    table = ClassTable()
    table.intern(["legal"])
    table.intern(["staff"])
    persisted = dict(table.items())

    reloaded = ClassTable(persisted)
    # A new interning order must not renumber existing classes.
    assert reloaded.intern(["staff"]) == 1
    assert reloaded.intern(["legal"]) == 0
    assert reloaded.intern(["hr"]) == 2


def test_ids_are_never_reused_after_gaps() -> None:
    table = ClassTable({0: ["legal"], 5: ["staff"]})
    assert table.intern(["hr"]) == 6


def test_reloaded_table_rejects_duplicate_group_sets_and_bad_ids() -> None:
    with pytest.raises(ValueError, match="same groups"):
        ClassTable({0: ["a", "b"], 1: ["b", "a"]})
    with pytest.raises(ValueError, match="acl_class"):
        ClassTable({-1: ["a"]})


def test_items_are_in_id_order() -> None:
    table = ClassTable({3: ["c"], 1: ["a"]})
    assert [c for c, _ in table.items()] == [1, 3]


def test_readable_by_needs_one_shared_group() -> None:
    table = ClassTable()
    legal = table.intern(["legal"])
    staff = table.intern(["staff"])
    both = table.intern(["legal", "staff"])
    nobody = table.intern([])
    assert table.readable_by(["staff"]) == {staff, both}
    assert table.readable_by(["staff", "legal"]) == {legal, staff, both}
    assert table.readable_by([]) == frozenset()
    assert nobody not in table.readable_by(["staff", "legal", "hr"])


def test_class_for_path_uses_rules_and_shares_ids() -> None:
    cfg = load_config(FIXTURE)
    table = ClassTable()
    legal = acl_class_for_path(cfg, table, "legal/acme.pdf")
    staff = acl_class_for_path(cfg, table, "policies/retention.md")
    assert legal != staff
    assert acl_class_for_path(cfg, table, "legal/x/other.pdf") == legal
    assert acl_class_for_path(cfg, table, "readme.md") == staff
    assert table.groups(legal) == {"legal"}
