"""Lexical, dense and entity seeding with rank fusion (DESIGN.md section 8)."""

from mds.seed.bm25 import ClassStats
from mds.seed.dense import DenseIndex, Embedder, dense_text
from mds.seed.entity import EntityIndex
from mds.seed.errors import LimitExceededError, SeedError
from mds.seed.fusion import RRF_K, reciprocal_rank_fusion
from mds.seed.hits import Hit, Match, SeedHit
from mds.seed.lexical import LexicalIndex
from mds.seed.seeder import DEFAULT_K, MAX_K, Seeder, build_seeder, parse_kinds

__all__ = [
    "DEFAULT_K",
    "MAX_K",
    "RRF_K",
    "ClassStats",
    "DenseIndex",
    "Embedder",
    "EntityIndex",
    "Hit",
    "LexicalIndex",
    "LimitExceededError",
    "Match",
    "SeedError",
    "SeedHit",
    "Seeder",
    "build_seeder",
    "dense_text",
    "parse_kinds",
    "reciprocal_rank_fusion",
]
