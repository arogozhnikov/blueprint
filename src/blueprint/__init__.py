"""
Blueprint: typed, immutable-by-default config objects for Python.
The implementation lives in `blueprint._blueprint`; this module only re-exports
its public API.

Module defines config schemas as classes with type-annotated fields (similar to a
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
    InvalidBlueprintError,
    check_type,
    field,
    format,
)

__all__ = [
    "BlueprintCfg",
    "ConfigDict",
    "ConfigList",
    "FieldInfo",
    "InvalidBlueprintError",
    "MISSING",
    "check_type",
    "field",
    "format",
]
