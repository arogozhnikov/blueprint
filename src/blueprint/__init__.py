"""
Blueprint: typed, immutable-by-default config objects for Python.
The implementation lives in `blueprint.blueprint`; this module only re-exports

Module defines a config schemas as classes with type-annotated fields (similar to a
dataclass). Instances are frozen after construction; changes only happen
inside an explicit `mutable_copy()` block, which yields an independent,
deep, mutable copy and re-validates it (including any custom `check()`
logic) when the block exits.

"""

from ._blueprint import (
    MISSING,
    BlueprintCfg,
    ConfigDict,
    ConfigList,
    FieldInfo,
    check_type,
    field,
    format,
)

__all__ = [
    "BlueprintCfg",
    "ConfigDict",
    "ConfigList",
    "FieldInfo",
    "MISSING",
    "check_type",
    "field",
    "format",
]
