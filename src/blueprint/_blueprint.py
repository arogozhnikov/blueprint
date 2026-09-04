import contextlib
import copy
import dataclasses
import enum
import types
import typing
import warnings
from abc import ABCMeta
from collections.abc import Callable, Generator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Self,
    TypeVar,
    Union,
    dataclass_transform,
    get_args,
    get_origin,
    overload,
)


class _Unused:
    def __repr__(self):
        return "unused"


class InvalidBlueprintError(TypeError):
    """Raised by `_validate_self()` when one or more fields fail their type check."""

    def __init__(self, errors: tuple[str, ...]):
        self.errors = errors
        super().__init__("\n".join(errors))

    def __repr__(self):
        # The inherited BaseException.__repr__ shows args via repr(), so the embedded
        # "\n" between errors would render as a literal "\n" instead of a line break.
        # Mirror __str__'s one-error-per-line layout here too.
        body = "\n".join(f"  {error}" for error in self.errors)
        return f"{type(self).__name__}(\n{body}\n)"

    def __str__(self):
        return self.__repr__()


unused = _Unused()


# Depth counters backing the two global escape hatches below. Plain module-level ints (not
# contextvars) are enough here: like the rest of blueprint, these are not designed to be
# thread-safe, and nesting is handled by incrementing/decrementing rather than storing a bool,
# so nested `with` blocks of the same kind compose correctly.
class _BlueprintState:
    _global_mutable_depth = 0
    _global_prohibit_mutability_depth = 0


@contextlib.contextmanager
def dangerously_all_mutable():
    """Context manager that makes *every* BlueprintCfg / ConfigList / ConfigDict instance
    mutable for as long as it's active, regardless of whether it came from `mutable_copy()`.

        cfg = ServerCfg(host="localhost", port=8080)
        with blueprint.dangerously_all_mutable():
            cfg.port = 9000  # normally raises AttributeError -- allowed here
    """
    _BlueprintState._global_mutable_depth += 1
    try:
        yield
    finally:
        _BlueprintState._global_mutable_depth -= 1


@contextlib.contextmanager
def debug_prohibit_mutability():
    """Debug helper: while active, any call to `mutable_copy()` (on any instance) raises
    `RuntimeError` instead of yielding a mutable copy. Recommended only for debugging.

        with blueprint.debug_prohibit_mutability():
            with cfg.mutable_copy() as y:  # raises RuntimeError immediately
                ...
    Ignores `dangerously_all_mutable`.
    """
    _BlueprintState._global_prohibit_mutability_depth += 1
    try:
        yield
    finally:
        _BlueprintState._global_prohibit_mutability_depth -= 1


@dataclasses.dataclass
class FieldInfo:
    default: Any = unused
    default_factory: Callable[[], Any] | _Unused = unused
    lt: Any = unused
    le: Any = unused
    gt: Any = unused
    ge: Any = unused
    unchecked: bool = False  # we have unchecked_field
    # Resolved later, from the field's type annotation -- not a constructor argument.
    type: Any = dataclasses.field(default=Any, init=False)

    def __repr__(self):
        result = (
            f"FieldInfo(default={repr(self.default)}, "
            f"default_factory={repr(self.default_factory)}, "
            f"type={repr(self.type)})"
        )
        if self.lt is not unused:
            result += f", x < {self.lt}"
        if self.le is not unused:
            result += f", x <= {self.le}"
        if self.gt is not unused:
            result += f", x > {self.gt}"
        if self.ge is not unused:
            result += f", x >= {self.ge}"
        if self.unchecked:
            result += ", unchecked=True"
        return result

    def __post_init__(self):
        assert self.default is unused or self.default_factory is unused, (
            "can't specify default value and factory at the same time"
        )

    def _merge_in_parent_fieldinfo(self, parent: "FieldInfo") -> None:
        """Fills in any of this FieldInfo's not-set (`unused`) attributes from `parent`, used
        by `__init_subclass__` when a subclass doesn't override every part of an inherited
        field. Attributes explicitly set on `self` are left untouched."""
        if self.default is unused and self.default_factory is unused:
            self.default = parent.default
            self.default_factory = parent.default_factory
        if self.lt is unused:
            self.lt = parent.lt
        if self.le is unused:
            self.le = parent.le
        if self.gt is unused:
            self.gt = parent.gt
        if self.ge is unused:
            self.ge = parent.ge
        # do not use parent's unchecked

    def check_bound_error(self, cls_name: str, field_name: str, value: Any) -> list[str]:
        """Checks a single already-type-checked value against this field's lt/le/gt/ge bounds."""
        if value is None:  # do not check Nones
            return []
        errors = []
        if self.lt is not unused and not (value < self.lt):
            errors.append(f"Invalid {cls_name}.{field_name}: expected x < {self.lt!r}, got {value!r}")
        if self.le is not unused and not (value <= self.le):
            errors.append(f"Invalid {cls_name}.{field_name}: expected x <= {self.le!r}, got {value!r}")
        if self.gt is not unused and not (value > self.gt):
            errors.append(f"Invalid {cls_name}.{field_name}: expected x > {self.gt!r}, got {value!r}")
        if self.ge is not unused and not (value >= self.ge):
            errors.append(f"Invalid {cls_name}.{field_name}: expected x >= {self.ge!r}, got {value!r}")
        return errors


_T = TypeVar("_T")


# overloads allow default of default_factory, but not both
@overload
def field(
    *,
    default: _T | _Unused = unused,
    lt: Any = unused,
    le: Any = unused,
    gt: Any = unused,
    ge: Any = unused,
) -> _T: ...
@overload
def field(
    *,
    default_factory: Callable[[], _T],
    lt: Any = unused,
    le: Any = unused,
    gt: Any = unused,
    ge: Any = unused,
) -> _T: ...
def field(
    *,
    default: Any = unused,
    default_factory: Callable[[], Any] | _Unused = unused,
    lt: Any = unused,
    le: Any = unused,
    gt: Any = unused,
    ge: Any = unused,
) -> Any:
    """Helper to define field metadata."""

    return FieldInfo(default=default, default_factory=default_factory, lt=lt, le=le, gt=gt, ge=ge)


# overloads allow default or default_factory, but not both
@overload
def unchecked_field(*, default: _T | _Unused = unused) -> _T: ...
@overload
def unchecked_field(*, default_factory: Callable[[], _T]) -> _T: ...
def unchecked_field(
    *,
    default: Any = unused,
    default_factory: Callable[[], Any] | _Unused = unused,
) -> Any:
    """Bypass normal checks, use as a last resort.
    Only type is checked, immutability isn't.

        class TrainCfg(BlueprintCfg):
            model: torch.nn.Module = unchecked_field()
    """

    return FieldInfo(default=default, default_factory=default_factory, unchecked=True)


def _substitute_typevars(tp: Any, typevar_mapping: dict[TypeVar, Any]) -> Any:
    """Recursively replaces TypeVars in type `tp` using typevar_mapping;
    Recursion is needed for nested generics (`list[T]`, `dict[str, T]`, `T | None`, ...)
    """
    if isinstance(tp, TypeVar):
        return typevar_mapping.get(tp, tp)

    args = get_args(tp)
    if not args:
        return tp

    new_args = tuple(_substitute_typevars(arg, typevar_mapping) for arg in args)
    if new_args == args:
        return tp

    if isinstance(tp, types.UnionType):
        # `X | Y` reconstructs via the `|` operator -- types.UnionType isn't subscriptable
        result = new_args[0]
        for arg in new_args[1:]:
            result = result | arg
        return result

    origin = get_origin(tp)
    if origin is None:
        # get_args() returned something (we wouldn't be here otherwise) but get_origin()
        # doesn't recognize it -- not expected to happen for any real type expression, but
        # nothing to substitute into if it does.
        return tp
    return origin[new_args]


def _build_typevar_mapping(cls) -> dict[TypeVar, Any]:
    """Composes one TypeVar -> concrete-type mapping for `cls`, by walking every generic
    parameterization from `cls` itself up through its whole MRO."""
    mapping: dict[TypeVar, Any] = {}
    for klass in cls.__mro__:
        for orig_base in getattr(klass, "__orig_bases__", ()):
            origin = get_origin(orig_base)
            if origin is None:
                continue
            params = getattr(origin, "__parameters__", ())
            if not params:
                continue
            for param, arg in zip(params, get_args(orig_base)):
                if param not in mapping:  # a more-derived class already won for this TypeVar
                    mapping[param] = _substitute_typevars(arg, mapping)
    return mapping


additional_typechecks: list[Callable] = []


def _typecheck_for_pydantic(value, expected_type) -> bool | None:
    # generic pydantics ... require separate handling. Model[T] is not generic alias.
    meta = getattr(expected_type, "__pydantic_generic_metadata__", None)
    if meta is not None and meta.get("origin") is not None:
        return isinstance(value, meta["origin"])
    return None  # None means "this rule is not appliable", bool is valid/invalid


# example: how one can add handling of custom classes
additional_typechecks.append(_typecheck_for_pydantic)


def check_type(value: Any, expected_type: Any) -> bool:
    """Recursively validates if a value matches the expected type constraint."""
    if expected_type is None or expected_type is type(None):
        return value is None

    if expected_type is Any:
        return True

    if isinstance(expected_type, TypeVar):  # unresolved typevar
        if expected_type.__constraints__:  # any of constraints
            return any(check_type(value, c) for c in expected_type.__constraints__)
        if expected_type.__bound__ is not None:  # check bound (can be only one)
            return check_type(value, expected_type.__bound__)
        return True  # no bounds and no constraints

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # Unpack Annotated
    if origin is Annotated:
        return check_type(value, args[0])

    # Handle Union and UnionType (e.g. Union[int, str] or int | str)
    is_union = origin is Union or isinstance(expected_type, types.UnionType)

    if is_union:
        return any(check_type(value, arg) for arg in args)

    # Handle Literal
    if origin is Literal:
        return any(type(value) is type(arg) and value == arg for arg in args)

    # Handle Collection types
    if origin in (list, tuple, dict, frozenset, Mapping, Sequence, AbstractSet):
        if origin is list:
            if not isinstance(value, list):
                return False
            if args:
                item_type = args[0]
                return all(check_type(item, item_type) for item in value)
            return True

        elif origin is tuple:
            if not isinstance(value, tuple):
                return False
            if not args:
                return True
            # Arbitrary-length tuple (e.g. tuple[int, ...])
            if len(args) == 2 and args[1] is Ellipsis:
                item_type = args[0]
                return all(check_type(item, item_type) for item in value)
            # Fixed-length tuple
            if len(value) != len(args):
                return False
            return all(check_type(v, arg) for v, arg in zip(value, args))

        elif origin is dict:
            if not isinstance(value, dict):
                return False
            if args:
                key_type, val_type = args
                return all(check_type(k, key_type) and check_type(v, val_type) for k, v in value.items())
            return True

        elif origin is frozenset:
            if not isinstance(value, frozenset):
                return False
            if args:
                item_type = args[0]
                return all(check_type(item, item_type) for item in value)
            return True

        elif origin is Mapping:
            # Accepts any object satisfying the Mapping protocol (dict, MappingProxyType,
            # ConfigDict, ...), not just plain dict -- unlike the `dict` branch above.
            if not isinstance(value, Mapping):
                return False
            if args:
                key_type, val_type = args
                return all(check_type(k, key_type) and check_type(v, val_type) for k, v in value.items())
            return True

        elif origin is Sequence:
            # str/bytes/bytearray technically satisfy the Sequence protocol, but treating a
            # single string as a "sequence of its characters" is virtually never what a
            # Sequence[T]-typed field means -- exclude them, matching e.g. pydantic's convention.
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                return False
            if args:
                item_type = args[0]
                return all(check_type(item, item_type) for item in value)
            return True

        elif origin is AbstractSet:
            # typing.AbstractSet[T]: accepts anything satisfying the
            # Set protocol (set, frozenset, ...), not just plain set
            if not isinstance(value, AbstractSet):
                return False
            if args:
                item_type = args[0]
                return all(check_type(item, item_type) for item in value)
            return True

        # TODO yell if provided deprecated typing.List/Dict/Set (recognize by qualname, do not import them)

    # Handle Enum
    if isinstance(expected_type, type) and issubclass(expected_type, enum.Enum):
        return isinstance(value, expected_type)

    # Handle BlueprintCfg subclasses
    if isinstance(expected_type, type) and issubclass(expected_type, BlueprintCfg):
        return isinstance(value, expected_type)

    # Callables - do not check arguments, only check if it is a callable
    if origin is Callable:
        return callable(value)

    for check_callable in additional_typechecks:
        result = check_callable(value, expected_type)
        if result is not None:
            return result

    # Handle standard type checking
    if isinstance(expected_type, type):
        # Strict checking to prevent bool matching int
        if expected_type is int and type(value) is bool:
            return False
        return isinstance(value, expected_type)

    return False


class ConfigList(list):
    """A list proxy that converts and validates elements on modification.
    Default is immutable, but can be made mutable, just as other classes.
    """

    _is_blueprint_mutable = False

    def __init__(self, iterable, item_type):
        self._item_type = item_type
        # Convert initial items
        converted = []
        for item in iterable:
            conv = _convert_value(item, item_type)
            if not check_type(conv, item_type):
                raise TypeError(f"Invalid item type: expected {item_type}, got {type(conv).__name__} ({repr(conv)})")
            converted.append(conv)
        super().__init__(converted)

    def _convert_and_validate(self, item):
        item = copy.deepcopy(item)
        conv = _convert_value(item, self._item_type)
        if not check_type(conv, self._item_type):
            raise TypeError(f"Invalid item type: expected {self._item_type}, got {type(conv).__name__} ({repr(conv)})")
        self.__assert_mutable()
        for node in _flat_iter_containers(conv):
            _set_mutable(node, True)
        return conv

    def append(self, item):
        self.__assert_mutable()
        super().append(self._convert_and_validate(item))

    def extend(self, iterable):
        self.__assert_mutable()
        converted = [self._convert_and_validate(item) for item in iterable]
        super().extend(converted)

    def insert(self, index, item):
        self.__assert_mutable()
        super().insert(index, self._convert_and_validate(item))

    def __setitem__(self, index, val):
        self.__assert_mutable()
        if isinstance(index, slice):
            converted = [self._convert_and_validate(item) for item in val]
            super().__setitem__(index, converted)
        else:
            super().__setitem__(index, self._convert_and_validate(val))

    def pop(self, index=-1):
        self.__assert_mutable()
        return super().pop(index)

    def remove(self, item):
        self.__assert_mutable()
        super().remove(item)

    def clear(self):
        self.__assert_mutable()
        super().clear()

    def __delitem__(self, index):
        self.__assert_mutable()
        super().__delitem__(index)

    def __assert_mutable(self):
        if not (self._is_blueprint_mutable or _BlueprintState._global_mutable_depth > 0):
            raise AttributeError("Cannot modify ConfigList outside of a mutable_copy() block")

    def __reduce__(self):
        """Supports copy.deepcopy() and pickle. Without this, both fall back to the default
        list-subclass protocol, which reconstructs via an empty instance plus extend() -- and
        extend() is mutation-guarded, so it raises on the not-yet-mutable fresh instance.
        Reconstructing through the constructor instead re-runs item conversion/validation."""
        return (ConfigList, (list(self), self._item_type))


class ConfigDict(dict):
    """A dict proxy that converts and validates keys/values on modification.
    Default is immutable, but can be made mutable, just as other classes.
    """

    _is_blueprint_mutable = False

    def __init__(self, mapping_or_iterable, key_type, value_type):
        self._key_type = key_type
        self._value_type = value_type

        if isinstance(mapping_or_iterable, dict):
            iterable = mapping_or_iterable.items()
        else:
            iterable = mapping_or_iterable

        converted = {}
        for k, v in iterable:
            if not check_type(k, key_type):
                raise TypeError(f"Invalid key type: expected {key_type}, got {type(k).__name__} ({repr(k)})")
            conv_v = _convert_value(v, value_type)
            if not check_type(conv_v, value_type):
                raise TypeError(
                    f"Invalid value type: expected {value_type}, got {type(conv_v).__name__} ({repr(conv_v)})"
                )
            converted[k] = conv_v
        super().__init__(converted)

    def _validate_key(self, key):
        if not check_type(key, self._key_type):
            raise TypeError(f"Invalid key type: expected {self._key_type}, got {type(key).__name__} ({repr(key)})")

    def _convert_and_validate_val(self, value):
        value = copy.deepcopy(value)
        conv = _convert_value(value, self._value_type)
        if not check_type(conv, self._value_type):
            raise TypeError(f"Invalid value type: expected {self._value_type}, got {repr(conv)}")
        self.__assert_mutable()
        for node in _flat_iter_containers(conv):
            _set_mutable(node, True)
        return conv

    def __setitem__(self, key, value):
        self.__assert_mutable()
        self._validate_key(key)
        super().__setitem__(key, self._convert_and_validate_val(value))

    def __delitem__(self, key):
        self.__assert_mutable()
        super().__delitem__(key)

    def update(self, *args, **kwargs):
        self.__assert_mutable()
        temp = {}
        if args:
            if len(args) > 1:
                raise TypeError(f"update expected at most 1 argument, got {len(args)}")
            other = args[0]
            if hasattr(other, "keys"):
                for k in other:
                    self._validate_key(k)
                    temp[k] = self._convert_and_validate_val(other[k])
            else:
                for k, v in other:
                    self._validate_key(k)
                    temp[k] = self._convert_and_validate_val(v)
        for k, v in kwargs.items():
            self._validate_key(k)
            temp[k] = self._convert_and_validate_val(v)

        super().update(temp)

    def clear(self):
        self.__assert_mutable()
        super().clear()

    def pop(self, key, *args):
        self.__assert_mutable()
        return super().pop(key, *args)

    def popitem(self):
        self.__assert_mutable()
        return super().popitem()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default  # assignment checks mutability
        return self[key]

    def __assert_mutable(self):
        if not (self._is_blueprint_mutable or _BlueprintState._global_mutable_depth > 0):
            raise AttributeError("Cannot modify ConfigDict outside of a mutable_copy() block")

    def __reduce__(self):
        """Supports copy.deepcopy() and pickle -- see ConfigList.__reduce__ for why this is
        needed (same issue, dict subclass instead of list)."""
        return (ConfigDict, (dict(self), self._key_type, self._value_type))


def _set_mutable(node, mutable):
    node.__dict__["_is_blueprint_mutable"] = mutable


def _flat_iter_containers(value):
    """Post-order walk of container hierarchy"""
    if isinstance(value, BlueprintCfg):
        for name in value.__blueprint_fields__:
            child = value.__dict__.get(name, unused)
            if child is not unused:
                yield from _flat_iter_containers(child)
        yield value
    elif isinstance(value, ConfigList):
        for item in value:
            yield from _flat_iter_containers(item)
        yield value
    elif isinstance(value, ConfigDict):
        for item in value.values():
            yield from _flat_iter_containers(item)
        yield value
    elif isinstance(value, tuple):
        for item in value:
            yield from _flat_iter_containers(item)
    elif isinstance(value, frozenset):
        for item in value:
            yield from _flat_iter_containers(item)
    # list/dict/set can't appear here, and are expected to be converted before calling this func


def _convert_value(value, expected_type):
    """Recursively converts lists/tuples/dicts into their internal ConfigList/ConfigDict/tuple
    representation. A field typed as a BlueprintCfg subclass is passed through unchanged -- it
    must already be an instance of the right class; dict inputs are not auto-converted into one
    (check_type()/_validate_self() rejects anything else with a clear TypeError).

    Shall be run only within "mutable" context.
    """
    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # Handle Annotated
    if origin is Annotated:
        expected_type = args[0]
        origin = get_origin(expected_type)
        args = get_args(expected_type)

    # Bare `list`/`dict`/`tuple`/`frozenset` (no subscript) are plain classes, not generic
    # aliases -- get_origin() returns None for them, unlike their typing.List/Dict/Tuple
    # counterparts or a subscripted list[...]/dict[...]/tuple[...]/frozenset[...]. Treat them
    # the same as their unconstrained (Any-typed) parameterized form so they still get wrapped
    # in the validating/immutable proxy instead of silently passing through as plain containers.
    if expected_type in (list, dict, tuple, frozenset, Mapping, Sequence, AbstractSet):
        origin = expected_type

    if origin in (list, Sequence):
        if isinstance(value, list):
            item_type = args[0] if args else Any
            return ConfigList(value, item_type)
        return value

    if origin is tuple:
        if isinstance(value, tuple):
            if args:
                if len(args) == 2 and args[1] is Ellipsis:
                    item_type = args[0]
                    return tuple(_convert_value(item, item_type) for item in value)
                if len(value) != len(args):
                    raise TypeError(
                        f"Invalid number of elements for {expected_type}: "
                        f"expected {len(args)}, got {len(value)} ({repr(value)})"
                    )
                return tuple(_convert_value(item, arg) for item, arg in zip(value, args, strict=True))
            return value

    if origin in (dict, Mapping):
        # TODO check satisfies Mapping protocol?
        key_type = args[0] if args and len(args) >= 1 else Any
        val_type = args[1] if args and len(args) >= 2 else Any
        return ConfigDict(value, key_type, val_type)

    if origin in (frozenset, AbstractSet):
        # TODO check satisfies AbstractSet
        item_type = args[0] if args else Any
        return frozenset(_convert_value(item, item_type) for item in value)

    return value


def _is_classvar(annotated_type) -> bool:
    """True for `ClassVar` and `ClassVar[...]` annotations (bare or subscripted)."""
    return annotated_type is ClassVar or get_origin(annotated_type) is ClassVar


def _process_classvar(
    cls,
    name: str,
    annotated_type,
    combined_classvars: dict[str, Any],
    missing_classvars: dict[str, Any],
):
    """Type-checks a ClassVar-annotated attribute once, at class-creation time, and records
    it in `combined_classvars` so `_BlueprintCfgMeta` can lock it against reassignment/deletion.

    A ClassVar with no value anywhere in the MRO isn't an error, but we make class "abstract".
    """
    args = get_args(annotated_type)
    inner_type = args[0] if args else Any

    # Prefer the value set directly on this class; fall back to whatever it inherits.
    value = cls.__dict__.get(name, unused)
    if value is unused:
        value = getattr(cls, name, unused)
    if value is unused:  # nothing inherited either
        missing_classvars[name] = inner_type
        return

    if not check_type(value, inner_type):
        raise InvalidBlueprintError(
            (
                f"Invalid type for ClassVar field {cls.__name__}.{name}: "
                f"Expected {inner_type}, got {type(value).__name__} ({repr(value)})",
            )
        )

    combined_classvars[name] = inner_type


class _BlueprintCfgMeta(ABCMeta):
    """Metaclass backing BlueprintCfg. Blocks modification / update of class attributes, and
    can mark a class abstract -- uninstantiable, via the same `__abstractmethods__` mechanism."""

    def __new__(mcls, name, bases, namespace, **kwargs):
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)

        no_defaults = getattr(cls, "__blueprint_classvars_without_values__", ())
        if no_defaults:
            # no default for classvar -> make class abstract (can't create instances)
            # actual job is done by ABCMeta in this case.
            cls.__abstractmethods__ = frozenset(cls.__abstractmethods__) | frozenset(no_defaults)
        return cls

    def __setattr__(cls, name, value):
        classvars = getattr(cls, "__blueprint_classvars__", None)
        if classvars and name in classvars:
            raise AttributeError(f"Cannot reassign ClassVar field {name!r} of {cls.__name__} after class creation")
        super().__setattr__(name, value)

    def __delattr__(cls, name):
        classvars = getattr(cls, "__blueprint_classvars__", None)
        if classvars and name in classvars:
            raise AttributeError(f"Cannot delete ClassVar field {name!r} of {cls.__name__} after class creation")
        super().__delattr__(name)


@dataclass_transform(kw_only_default=True, field_specifiers=(field, unchecked_field))
class BlueprintCfg(metaclass=_BlueprintCfgMeta):
    __blueprint_fields__: dict[str, FieldInfo]
    __blueprint_classvars__: dict[str, Any]
    __blueprint_classvars_without_values__: tuple[str, ...]
    # allows mutations on this instance; mutable_copy() cascades this to the whole
    # nested tree (see _iter_containers/_set_mutable), so children get their own flag too
    _is_blueprint_mutable: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        combined_fields = {}
        combined_classvars = {}
        missing_classvars: dict[str, Any] = {}

        # Inherit fields from parents in MRO (parents evaluated first so subclasses override them).
        # `cls` itself is excluded: __blueprint_fields__ doesn't exist yet (this loop is part of what builds it)
        for base in reversed(cls.__mro__):
            if base is object or base is BlueprintCfg or base is cls:
                continue
            # issubclass(), not isinstance(): `base` is itself a class (an MRO entry)
            if issubclass(base, BlueprintCfg):
                combined_fields.update(base.__blueprint_fields__)
                combined_classvars.update(base.__blueprint_classvars__)

        # Get local type annotations
        _local_annotations = getattr(cls, "__annotations__", {})
        try:
            _resolved_hints = typing.get_type_hints(cls, include_extras=True)
            # replace with resolved hints if those are available
            annotations = {**_local_annotations, **_resolved_hints}
        except NameError:  # in case forward-references can't be resovled, or "a local class"
            annotations = _local_annotations

        # Mapping typevars to resolved types, e.g. for MyClass(BlueprintCfg, Generic[T]);
        #   and then Derived(Base[float])
        typevar_mapping = _build_typevar_mapping(cls)

        # Process field metadata and defaults
        for name, annotated_type in annotations.items():
            if name.startswith("_"):
                continue

            if typevar_mapping:
                annotated_type = _substitute_typevars(annotated_type, typevar_mapping)

            if _is_classvar(annotated_type):
                _process_classvar(cls, name, annotated_type, combined_classvars, missing_classvars)
                continue

            actual_type = annotated_type
            new_field_info = None
            if get_origin(annotated_type) is Annotated:
                args = get_args(annotated_type)
                actual_type = args[0]
                for meta in args[1:]:
                    if isinstance(meta, FieldInfo):
                        new_field_info = meta
                        break
                # else: the Annotated[...] metadata carries no FieldInfo of its own -- fall
                # through below to check other options, assuming first argument of Annotated is a type.

            if new_field_info is None:
                if hasattr(cls, name) and name in cls.__dict__:
                    val = cls.__dict__[name]
                    if isinstance(val, FieldInfo):
                        new_field_info = val
                    else:
                        new_field_info = FieldInfo(default=val)
                else:
                    # just type annotation
                    new_field_info = FieldInfo()

            # If field already existed, merge inherited values
            if name in combined_fields:
                new_field_info._merge_in_parent_fieldinfo(combined_fields[name])

            combined_fields[name] = new_field_info
            combined_fields[name].type = actual_type

            # Clean up the FieldInfo from class level
            if name in cls.__dict__:
                val = cls.__dict__[name]
                if isinstance(val, FieldInfo):
                    if val.default is not unused:
                        setattr(cls, name, val.default)
                    else:
                        delattr(cls, name)

        # Handle overridden fields without local type annotations
        for name in combined_fields:
            if name in cls.__dict__ and name not in annotations:
                val = cls.__dict__[name]
                if isinstance(val, FieldInfo):
                    if val.default is not unused:
                        combined_fields[name].default = val.default
                    if val.default_factory is not unused:
                        combined_fields[name].default_factory = val.default_factory
                    if val.lt is not unused:
                        combined_fields[name].lt = val.lt
                    if val.le is not unused:
                        combined_fields[name].le = val.le
                    if val.gt is not unused:
                        combined_fields[name].gt = val.gt
                    if val.ge is not unused:
                        combined_fields[name].ge = val.ge
                    if val.default is not unused:
                        setattr(cls, name, val.default)
                    else:
                        delattr(cls, name)
                else:
                    combined_fields[name].default = val
                    combined_fields[name].default_factory = unused

        cls.__blueprint_fields__ = combined_fields
        cls.__blueprint_classvars__ = combined_classvars
        cls.__blueprint_classvars_without_values__ = tuple(missing_classvars)

    if not typing.TYPE_CHECKING:
        # type checking sees dataclass transform
        # but we need to add a validation layer.
        def __init__(self, **kwargs):
            fields = self.__blueprint_fields__

            # Validate that all arguments passed are recognized fields
            extra = set(kwargs.keys()) - set(fields.keys())
            if extra:
                raise TypeError(f"__init__() got unexpected keyword arguments: {', '.join(map(repr, sorted(extra)))}")

            # Populate fields
            for name, field_info in fields.items():
                if name in kwargs:
                    val = kwargs[name]
                elif field_info.default is not unused:
                    val = field_info.default
                elif field_info.default_factory is not unused:
                    val = field_info.default_factory()
                else:
                    raise TypeError(f"__init__() missing required keyword-only argument: {repr(name)}")

                # Deep-copy first so construction can't alias external mutable state
                # (mirrors __setattr__, which does the same for the same reason).
                val = copy.deepcopy(val)
                val = _convert_value(val, field_info.type)
                self.__dict__[name] = val

            self._validate_self()

    @staticmethod
    def __construct__(cls, _dict):
        """Used during unpickle and similar. Enforces check."""
        result = cls.__new__(cls)
        result.__dict__.update(_dict)
        result._validate_self()  # does not check types though!
        return result

    def _validate_self(self):
        """Non-recursive validation (all fields + every check() along the MRO)."""
        errors = []
        for name, field_info in self.__blueprint_fields__.items():
            value = getattr(self, name)
            if not check_type(value, field_info.type):
                errors.append(
                    f"Invalid type for field {self.__class__.__name__}.{name}: "
                    f"Expected {field_info.type}, got {type(value).__name__} ({repr(value)})"
                )
                continue
            errors.extend(field_info.check_bound_error(self.__class__.__name__, name, value))
        if errors:
            raise InvalidBlueprintError(tuple(errors))

        # Run every check() defined along the MRO, base class first.
        # Breaks usual convention, so users didn't call super().check()
        for klass in reversed(type(self).__mro__):
            if "check" in klass.__dict__:
                klass.__dict__["check"](self)

    def check(self) -> None:
        """Custom post-validation hook. Subclasses override this to implement cross-field checks."""
        pass

    def __setattr__(self, name, value):
        if name not in self.__blueprint_fields__:
            if name.startswith("_"):
                super().__setattr__(name, value)
                return
            raise AttributeError(f"{self.__class__.__name__} has no field {repr(name)}")

        if not (self._is_blueprint_mutable or _BlueprintState._global_mutable_depth > 0):
            raise AttributeError(
                f"Cannot assign to field {repr(name)} of {self.__class__.__name__} outside of a mutable_copy() block"
            )

        field_info = self.__blueprint_fields__[name]
        # Deep-copy first so we didn't modify or link some external object
        value = copy.deepcopy(value)
        value = _convert_value(value, field_info.type)
        # We already know we're mutable here (checked above), so cascade that onto any
        # freshly-created or freshly-attached nested value too, for the rest of this block.
        for node in _flat_iter_containers(value):
            _set_mutable(node, True)

        # Field-level checks run always
        if not check_type(value, field_info.type):
            raise InvalidBlueprintError(
                errors=(
                    f"Invalid type for field {self.__class__.__name__}.{name}: "
                    f"Expected {field_info.type}, got {type(value).__name__} ({repr(value)})",
                )
            )
        bound_errors = field_info.check_bound_error(self.__class__.__name__, name, value)
        if bound_errors:
            raise InvalidBlueprintError(errors=tuple(bound_errors))

        super().__setattr__(name, value)
        # we don't validate full instance here, only field.

    def __delattr__(self, name):
        if name in self.__blueprint_fields__:
            raise AttributeError(f"Cannot delete field {repr(name)} of {self.__class__.__name__}")
        super().__delattr__(name)

    def __repr__(self):
        field_strs = []
        for name in self.__blueprint_fields__:
            val = getattr(self, name)
            field_strs.append(f"{name}={repr(val)}")
        return f"{self.__class__.__name__}({', '.join(field_strs)})"

    def as_dict(self) -> dict[str, Any]:
        """Recursively converts this config into plain `dict` / `list` / `tuple` / `frozenset` containers."""
        return {name: _as_dict_value(getattr(self, name)) for name in self.__blueprint_fields__}

    def as_dict_selected_fields(self, selected: list[str]) -> dict[str, Any]:
        """Like `as_dict()`, but includes only the given fields."""
        unknown = [name for name in selected if name not in self.__blueprint_fields__]
        if unknown:
            raise TypeError(f"as_dict_selected_fields() got unexpected field names: {', '.join(map(repr, unknown))}")
        return {name: _as_dict_value(getattr(self, name)) for name in selected}

    def __eq__(self, other):
        if self.__class__ is not other.__class__:
            return NotImplemented
        fields = self.__blueprint_fields__
        return all(getattr(self, name) == getattr(other, name) for name in fields)

    def __reduce__(self):
        """Supports copy.deepcopy() and pickle. We enforce check after the instance is reconstructed"""
        return (BlueprintCfg.__construct__, (type(self), self.__dict__))

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler):
        """Lets a BlueprintCfg subclass be used as a field type in a pydantic BaseModel/dataclass:

            class Settings(pydantic.BaseModel):
                server: ServerCfg

        Note: you can serialize this model, can't deserialize - because Blueprint is not deserializable.

        - model_dump_json() / model_dump(mode="json") serialize it back via `as_dict()`;
        - model_dump(mode="python") (the default) keeps the instance as-is.
        """
        from pydantic import GetCoreSchemaHandler  # type: ignore
        from pydantic_core import core_schema  # type: ignore

        handler = typing.cast(GetCoreSchemaHandler, handler)

        def _validate(value: Any) -> "BlueprintCfg":
            if isinstance(value, cls):
                return value
            # ValueError (not TypeError): pydantic only converts ValueError/AssertionError
            # raised by a validator function into its own ValidationError.
            raise ValueError(
                f"Expected an instance of {cls.__name__}, got {type(value).__name__}. "
                f"blueprint configs have no dict/JSON deserialization -- construct a "
                f"{cls.__name__}(...) instance and pass that in."
            )

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda instance: instance.as_dict(),
                info_arg=False,
                when_used="json",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler) -> dict[str, Any]:
        """Backs `model_json_schema()`, this documents the *output* shape, matching `as_dict()`."""
        return {"type": "object", "title": cls.__name__}

    @contextlib.contextmanager
    def mutable_copy(self) -> Generator[Self]:
        """Context manager yielding an independent, deep, mutable copy of this instance.

            with x.mutable_copy() as y:
                y.some_field = ...
                y.child.name = "..."       # cascades: nested configs are mutable too
                y.children.append(...)     # ...and so are nested list/dict fields

        Result is (recursively) validated at exit.
        """
        if _BlueprintState._global_prohibit_mutability_depth > 0:
            raise RuntimeError(
                f"mutable_copy() was called on a {self.__class__.__name__} inside a debug_prohibit_mutability() block"
            )

        # this includes full validation of result
        clone = copy.deepcopy(self)

        initial_mutables = []
        for node in _flat_iter_containers(clone):
            initial_mutables.append(node)
            _set_mutable(node, True)

        exception: BaseException | None = None
        try:
            yield clone
        except BaseException as exc:
            # Stash into the outer `exception` variable before re-raising
            exception = exc
            raise
        finally:
            # reset to immutable, protection for the case of mutables
            for node in initial_mutables:
                _set_mutable(node, False)
            for node in _flat_iter_containers(clone):
                _set_mutable(node, False)

            try:
                for node in _flat_iter_containers(clone):
                    if isinstance(node, BlueprintCfg):
                        node._validate_self()
            except BaseException as new_validation_error:
                if exception is None:
                    raise
                else:
                    # An exception was already propagating out of the `with` block (`exception`);
                    # don't let this secondary validation failure during cleanup mask it -- just
                    # warn about it. Note: we deliberately do NOT `return` here -- a `return`
                    # inside a `finally` block silently swallows any exception that was
                    # propagating through it, which would suppress `exception` instead of letting
                    # it keep propagating as intended.
                    warnings.warn(
                        f"{clone.__class__.__name__} was left in an invalid state after "
                        f"mutable_copy() exited because of {exception!r}: {new_validation_error!r}",
                        stacklevel=2,
                    )
                    # just keep propagating previous exception


def _as_dict_value(value: Any) -> Any:
    """Recursive helper backing `BlueprintCfg.as_dict()` / `as_dict_selected_fields()`."""
    if isinstance(value, BlueprintCfg):
        return value.as_dict()
    if isinstance(value, (ConfigList, list)):
        return [_as_dict_value(item) for item in value]
    if isinstance(value, (ConfigDict, dict)):
        return {key: _as_dict_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return tuple(_as_dict_value(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_as_dict_value(item) for item in value)
    return value


def _format_leaf(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)


def _format_wrap(opening: str, parts: list, closing: str, indent: int, linewidth: int, level: int) -> str:
    """Renders `opening + ", ".join(parts) + closing` on one line if it fits within
    `linewidth`; otherwise renders one part per line, indented by `indent` spaces
    per nesting level."""
    if not parts:
        return opening + closing
    one_line = opening + ", ".join(parts) + closing
    if len(one_line) <= linewidth and "\n" not in one_line:
        return one_line
    pad = " " * (indent * (level + 1))
    closing_pad = " " * (indent * level)
    body = ",\n".join(pad + part for part in parts)
    return f"{opening}\n{body},\n{closing_pad}{closing}"


def _format_value(value: Any, indent: int, linewidth: int, level: int) -> str:
    if isinstance(value, BlueprintCfg):
        fields = value.__blueprint_fields__
        parts = [
            f"{name}={_format_value(value.__dict__[name], indent, linewidth, level + 1)}"
            for name in fields
            if name in value.__dict__
        ]
        return _format_wrap(f"{type(value).__name__}(", parts, ")", indent, linewidth, level)
    if isinstance(value, (ConfigList, list)):
        parts = [_format_value(item, indent, linewidth, level + 1) for item in value]
        return _format_wrap("[", parts, "]", indent, linewidth, level)
    if isinstance(value, tuple):
        parts = [_format_value(item, indent, linewidth, level + 1) for item in value]
        if len(parts) == 1:
            return _format_wrap("(", parts, ",)", indent, linewidth, level)
        return _format_wrap("(", parts, ")", indent, linewidth, level)
    if isinstance(value, (ConfigDict, dict)):
        parts = [
            f"{_format_value(k, indent, linewidth, level + 1)}: {_format_value(v, indent, linewidth, level + 1)}"
            for k, v in value.items()
        ]
        return _format_wrap("{", parts, "}", indent, linewidth, level)
    if isinstance(value, frozenset):
        if not value:
            return "frozenset()"
        # Iteration order isn't guaranteed (and elements aren't always mutually orderable) --
        # sort by repr() so format() stays deterministic.
        parts = [_format_value(item, indent, linewidth, level + 1) for item in sorted(value, key=repr)]
        return _format_wrap("frozenset({", parts, "})", indent, linewidth, level)
    return _format_leaf(value)


def format(cfg: BlueprintCfg, indent: int = 2, linewidth: int = 100) -> str:
    """Pretty-prints a BlueprintCfg (and any nested configs/containers) as a
    readable, deterministic string."""
    if not isinstance(cfg, BlueprintCfg):
        raise TypeError(f"format() expects a BlueprintCfg instance, got {type(cfg)}")
    return _format_value(cfg, indent, linewidth, 0)
