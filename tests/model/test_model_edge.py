import dataclasses
from datetime import date, datetime
from typing import Any

import pytest

from mds.model import (
    ATOM_ID_LIMIT,
    AuthorKind,
    Edge,
    EdgeState,
    ModelError,
    UnknownDimensionError,
)


def make_edge(**overrides: Any) -> Edge:
    fields: dict[str, Any] = {
        "edge_id": 1,
        "dim": "r1",
        "src": 3,
        "dst": 4,
        "weight": 1.0,
        "author_kind": AuthorKind.EXTRACTOR,
        "author_id": "refs-extractor/0.1",
        "state": EdgeState.CONFIRMED,
        "csn_from": 5,
    }
    fields.update(overrides)
    return Edge(**fields)


def test_valid_edge_defaults() -> None:
    edge = make_edge()
    assert edge.evidence == (3, 4)
    assert edge.valid_from is None and edge.valid_to is None
    assert edge.csn_to is None
    assert edge.note == ""


# Dimension ----------------------------------------------------------------


def test_dim_name_normalised_to_id() -> None:
    assert make_edge(dim="references").dim == "r1"
    assert make_edge(dim="r1").dim == "r1"


def test_unknown_dim_rejected() -> None:
    with pytest.raises(UnknownDimensionError, match="unknown dimension 'cites'"):
        make_edge(dim="cites")


# Atom ids and evidence ----------------------------------------------------


@pytest.mark.parametrize("field", ["src", "dst"])
@pytest.mark.parametrize("value", [-1, ATOM_ID_LIMIT])
def test_endpoint_out_of_range(field: str, value: int) -> None:
    with pytest.raises(ModelError, match=f"{field} must be in"):
        make_edge(**{field: value})


def test_evidence_explicit_kept_as_tuple() -> None:
    assert make_edge(evidence=[3, 4, 9]).evidence == (3, 4, 9)


def test_evidence_empty_rejected() -> None:
    with pytest.raises(ModelError, match="evidence must not be empty"):
        make_edge(evidence=())


def test_evidence_atom_out_of_range_rejected() -> None:
    with pytest.raises(ModelError, match="evidence atom_id must be in"):
        make_edge(evidence=(3, ATOM_ID_LIMIT))


def test_edge_id_non_negative() -> None:
    with pytest.raises(ModelError, match="edge_id must be >= 0"):
        make_edge(edge_id=-1)


# Weight -------------------------------------------------------------------


@pytest.mark.parametrize("weight", [1.0, 0.5, 1e-9, 1])
def test_weight_in_range_accepted(weight: float) -> None:
    assert make_edge(weight=weight).weight == weight


@pytest.mark.parametrize("weight", [0.0, -0.1, 1.0001, 2, float("nan"), float("inf")])
def test_weight_out_of_range_rejected(weight: float) -> None:
    with pytest.raises(ModelError, match=r"weight must be in \(0, 1\]"):
        make_edge(weight=weight)


@pytest.mark.parametrize("weight", ["1.0", True, None])
def test_weight_must_be_number(weight: Any) -> None:
    with pytest.raises(ModelError, match="weight must be a number"):
        make_edge(weight=weight)


# Valid time ---------------------------------------------------------------


def test_time_bound_dimension_accepts_valid_interval() -> None:
    edge = make_edge(dim="supersedes", valid_from=date(2023, 3, 1), valid_to=date(2024, 1, 1))
    assert edge.valid_from == date(2023, 3, 1)


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    [(date(2023, 3, 1), None), (None, date(2023, 3, 1)), (None, None)],
)
def test_time_bound_dimension_accepts_open_intervals(
    valid_from: date | None, valid_to: date | None
) -> None:
    make_edge(dim="r4", valid_from=valid_from, valid_to=valid_to)


@pytest.mark.parametrize("valid_to", [date(2023, 3, 1), date(2023, 2, 28)])
def test_valid_from_must_be_before_valid_to(valid_to: date) -> None:
    with pytest.raises(ModelError, match="valid_from .* must be before valid_to"):
        make_edge(dim="r4", valid_from=date(2023, 3, 1), valid_to=valid_to)


@pytest.mark.parametrize("dim", ["r0", "r1", "r2", "r3"])
@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
def test_valid_time_rejected_on_non_time_bound_dimension(dim: str, field: str) -> None:
    with pytest.raises(ModelError, match=f"dimension {dim} is not time-bound"):
        make_edge(dim=dim, **{field: date(2023, 3, 1)})


@pytest.mark.parametrize("value", ["2023-03-01", datetime(2023, 3, 1, 12, 0)])
def test_valid_time_must_be_a_date(value: Any) -> None:
    with pytest.raises(ModelError, match="valid_from must be a date"):
        make_edge(dim="r4", valid_from=value)


# Transaction time ---------------------------------------------------------


def test_csn_interval_accepted() -> None:
    assert make_edge(csn_from=5, csn_to=6).csn_to == 6


@pytest.mark.parametrize("csn_to", [5, 4])
def test_csn_from_must_be_before_csn_to(csn_to: int) -> None:
    with pytest.raises(ModelError, match="csn_from .* must be before csn_to"):
        make_edge(csn_from=5, csn_to=csn_to)


def test_negative_csn_rejected() -> None:
    with pytest.raises(ModelError, match="csn_to must be >= 0"):
        make_edge(csn_from=0, csn_to=-1)


# Enums and author ---------------------------------------------------------


@pytest.mark.parametrize("state", list(EdgeState))
def test_every_state_accepted(state: EdgeState) -> None:
    assert make_edge(state=state).state is state


@pytest.mark.parametrize("author_kind", list(AuthorKind))
def test_every_author_kind_accepted(author_kind: AuthorKind) -> None:
    assert make_edge(author_kind=author_kind).author_kind is author_kind


def test_enums_coerced_from_strings() -> None:
    edge = make_edge(state="pending", author_kind="agent")
    assert edge.state is EdgeState.PENDING
    assert edge.author_kind is AuthorKind.AGENT


def test_unknown_state_rejected() -> None:
    with pytest.raises(ModelError, match="state must be one of confirmed, pending, rejected"):
        make_edge(state="approved")


def test_unknown_author_kind_rejected() -> None:
    with pytest.raises(ModelError, match="author_kind must be one of extractor, agent, person"):
        make_edge(author_kind="robot")


def test_author_id_required() -> None:
    with pytest.raises(ModelError, match="author_id must not be empty"):
        make_edge(author_id="")


def test_edge_is_frozen_and_hashable() -> None:
    edge = make_edge()
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.state = EdgeState.REJECTED  # type: ignore[misc]
    assert hash(edge) == hash(make_edge())
