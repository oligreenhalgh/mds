"""Atoms, versions, edges and the dimension registry (DESIGN.md sections 2-4). Pure data, no I/O."""

from mds.model.atom import Atom, AtomVersion
from mds.model.breadcrumb import Breadcrumb, ReadSetEntry
from mds.model.dimensions import Dimension, DimensionRegistry, default_registry
from mds.model.edge import Edge
from mds.model.enums import AtomKind, AuthorKind, EdgeState, Operator
from mds.model.errors import ATOM_ID_LIMIT, ModelError, UnknownDimensionError

__all__ = [
    "ATOM_ID_LIMIT",
    "Atom",
    "AtomKind",
    "AtomVersion",
    "AuthorKind",
    "Breadcrumb",
    "Dimension",
    "DimensionRegistry",
    "Edge",
    "EdgeState",
    "ModelError",
    "Operator",
    "ReadSetEntry",
    "UnknownDimensionError",
    "default_registry",
]
