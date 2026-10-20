"""Principals, ACL classes and mask computation (DESIGN.md section 7)."""

from mds.acl.classes import ClassTable, acl_class_for_path
from mds.acl.config import (
    AclRule,
    Config,
    Principal,
    Trust,
    load_config,
    lookup_principal,
    parse_config,
    parse_config_text,
)
from mds.acl.errors import (
    ConfigError,
    UnknownConfigDimensionError,
    UnknownPrincipalError,
    UnknownTrustError,
)
from mds.acl.glob import compile_glob, normalise_path
from mds.acl.masks import MaskCache

__all__ = [
    "AclRule",
    "ClassTable",
    "Config",
    "ConfigError",
    "MaskCache",
    "Principal",
    "Trust",
    "UnknownConfigDimensionError",
    "UnknownPrincipalError",
    "UnknownTrustError",
    "acl_class_for_path",
    "compile_glob",
    "load_config",
    "lookup_principal",
    "normalise_path",
    "parse_config",
    "parse_config_text",
]
