from pathlib import Path

import yaml

import mds
from mds.cli import main


def test_version() -> None:
    assert mds.__version__


def test_cli_help() -> None:
    assert main([]) == 0


def test_dimension_registry_matches_design() -> None:
    path = Path(mds.__file__).parent / "model" / "dimensions.yaml"
    dims = yaml.safe_load(path.read_text())
    assert [d["id"] for d in dims] == ["r0", "r1", "r2", "r3", "r4"]
    assert [d["name"] for d in dims] == [
        "contains",
        "references",
        "co_occurs",
        "derived_from",
        "supersedes",
    ]
    assert [d["name"] for d in dims if d["time_bound"]] == ["supersedes"]


def test_graphblas_masked_traversal() -> None:
    """Smoke test: a masked vector-matrix product hides an invisible atom."""
    import graphblas as gb

    # 0 -> 1, 0 -> 2 in one dimension; atom 2 is invisible to the principal.
    a = gb.Matrix.from_coo([0, 0], [1, 2], [True, True], nrows=3, ncols=3)
    seed = gb.Vector.from_coo([0], [True], size=3)
    visible = gb.Vector.from_coo([0, 1], [True, True], size=3)
    out = gb.Vector(bool, size=3)
    out(mask=visible.S) << seed.vxm(a, gb.semiring.lor_land)
    assert list(out.to_coo()[0]) == [1]
