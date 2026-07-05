import enum
import typing
from datetime import datetime, timedelta
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Tuple,
    Union,
    get_args,
    get_origin,
    Annotated,
)

from typing import dataclass_transform


class MissingType:
    def __repr__(self):
        return "MISSING"


MISSING = MissingType()


class FieldInfo:
    def __init__(self, default=MISSING, default_factory=MISSING, description=None):
        self.default = default
        self.default_factory = default_factory
        self.description = description
        self.type = Any

    def __repr__(self):
        return (
            f"FieldInfo(default={repr(self.default)}, "
            f"default_factory={repr(self.default_factory)}, "
            f"description={repr(self.description)}, "
            f"type={repr(self.type)})"
        )


def field(*, default=MISSING, default_factory=MISSING, description=None) -> Any:
    """Helper to define field metadata such as default values, factories, or descriptions."""
    return FieldInfo(default=default, default_factory=default_factory, description=description)


def check_type(value: Any, expected_type: Any) -> bool:
    """Recursively validates if a value matches the expected type constraint."""
    if expected_type is None or expected_type is type(None):
        return value is None

    if expected_type is Any:
        return True

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # Unpack Annotated
    if origin is Annotated:
        return check_type(value, args[0])

    # Handle Union and UnionType (e.g. Union[int, str] or int | str)
    is_union = False
    if origin is Union:
        is_union = True
    else:
        tp_name = getattr(expected_type, "__class__", None).__name__
        if tp_name == "UnionType":
            is_union = True

    if is_union:
        return any(check_type(value, arg) for arg in args)

    # Handle Literal
    if origin is Literal:
        return any(type(value) is type(arg) and value == arg for arg in args)

    # Normalize typing collection generics
    if origin is not None:
        try:
            if origin is typing.List:
                origin = list
            elif origin is typing.Tuple:
                origin = tuple
            elif origin is typing.Dict:
                origin = dict
        except Exception:
            pass

    # Handle Collection types (list, tuple, dict)
    if origin in (list, tuple, dict):
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

    # Handle Enum
    if isinstance(expected_type, type) and issubclass(expected_type, enum.Enum):
        return isinstance(value, expected_type)

    # Handle BlueprintCfg subclasses
    if isinstance(expected_type, type) and issubclass(expected_type, BlueprintCfg):
        return isinstance(value, expected_type)

    # Handle standard type checking
    if isinstance(expected_type, type):
        # Strict checking to prevent bool matching int
        if expected_type is int and type(value) is bool:
            return False
        return isinstance(value, expected_type)

    return False


class ObservableList(list):
    """A list proxy that converts and validates elements on modification, and triggers validation callbacks."""
    def __init__(self, iterable, callback, item_type, converter_func):
        self._callback = callback
        self._item_type = item_type
        self._converter_func = converter_func
        # Convert initial items
        converted = []
        for item in iterable:
            conv = converter_func(item, item_type, callback)
            if not check_type(conv, item_type):
                raise TypeError(
                    f"Invalid item type: expected {item_type}, "
                    f"got {type(conv).__name__} ({repr(conv)})"
                )
            converted.append(conv)
        super().__init__(converted)

    def _convert_and_validate(self, item):
        conv = self._converter_func(item, self._item_type, self._callback)
        if not check_type(conv, self._item_type):
            raise TypeError(
                f"Invalid item type: expected {self._item_type}, "
                f"got {type(conv).__name__} ({repr(conv)})"
            )
        return conv

    def append(self, item):
        super().append(self._convert_and_validate(item))
        self._callback()

    def extend(self, iterable):
        converted = [self._convert_and_validate(item) for item in iterable]
        super().extend(converted)
        self._callback()

    def insert(self, index, item):
        super().insert(index, self._convert_and_validate(item))
        self._callback()

    def __setitem__(self, index, val):
        if isinstance(index, slice):
            converted = [self._convert_and_validate(item) for item in val]
            super().__setitem__(index, converted)
        else:
            super().__setitem__(index, self._convert_and_validate(val))
        self._callback()

    def pop(self, index=-1):
        val = super().pop(index)
        self._callback()
        return val

    def remove(self, item):
        super().remove(item)
        self._callback()

    def clear(self):
        super().clear()
        self._callback()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._callback()


class ObservableDict(dict):
    """A dict proxy that converts and validates keys/values on modification, and triggers validation callbacks."""
    def __init__(self, mapping_or_iterable, callback, key_type, value_type, converter_func):
        self._callback = callback
        self._key_type = key_type
        self._value_type = value_type
        self._converter_func = converter_func

        if isinstance(mapping_or_iterable, dict):
            iterable = mapping_or_iterable.items()
        else:
            iterable = mapping_or_iterable

        converted = {}
        for k, v in iterable:
            if not check_type(k, key_type):
                raise TypeError(
                    f"Invalid key type: expected {key_type}, "
                    f"got {type(k).__name__} ({repr(k)})"
                )
            conv_v = converter_func(v, value_type, callback)
            if not check_type(conv_v, value_type):
                raise TypeError(
                    f"Invalid value type: expected {value_type}, "
                    f"got {type(conv_v).__name__} ({repr(conv_v)})"
                )
            converted[k] = conv_v
        super().__init__(converted)

    def _validate_key(self, key):
        if not check_type(key, self._key_type):
            raise TypeError(
                f"Invalid key type: expected {self._key_type}, "
                f"got {type(key).__name__} ({repr(key)})"
            )

    def _convert_and_validate_val(self, value):
        conv = self._converter_func(value, self._value_type, self._callback)
        if not check_type(conv, self._value_type):
            raise TypeError(
                f"Invalid value type: expected {self._value_type}, "
                f"got {type(conv).__name__} ({repr(value)})"
            )
        return conv

    def __setitem__(self, key, value):
        self._validate_key(key)
        super().__setitem__(key, self._convert_and_validate_val(value))
        self._callback()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._callback()

    def update(self, *args, **kwargs):
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
        self._callback()

    def clear(self):
        super().clear()
        self._callback()

    def pop(self, key, *args):
        val = super().pop(key, *args)
        self._callback()
        return val

    def popitem(self):
        val = super().popitem()
        self._callback()
        return val

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]


@dataclass_transform(
    kw_only_default=True,
    field_specifiers=(field,)
)
class BlueprintCfg:
    __blueprint_fields__: Dict[str, FieldInfo] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        combined_fields = {}

        # 1. Inherit fields from parents in MRO (parents evaluated first so subclasses override them)
        for base in reversed(cls.__mro__):
            if base is object or base is BlueprintCfg:
                continue
            if hasattr(base, "__blueprint_fields__"):
                combined_fields.update(base.__blueprint_fields__)

        # 2. Get local type annotations
        try:
            local_annotations = getattr(cls, "__annotations__", {})
            resolved_hints = typing.get_type_hints(cls, include_extras=True)
            annotations = {k: resolved_hints[k] for k in local_annotations if k in resolved_hints}
        except Exception:
            annotations = local_annotations

        # 3. Process field metadata and defaults
        for name, annotated_type in annotations.items():
            if name.startswith("_"):
                continue

            default = MISSING
            default_factory = MISSING
            description = None

            actual_type = annotated_type
            if get_origin(annotated_type) is Annotated:
                args = get_args(annotated_type)
                actual_type = args[0]
                for meta in args[1:]:
                    if isinstance(meta, str):
                        description = meta
                    elif isinstance(meta, FieldInfo):
                        if meta.description:
                            description = meta.description
                        if meta.default is not MISSING:
                            default = meta.default
                        if meta.default_factory is not MISSING:
                            default_factory = meta.default_factory

            if hasattr(cls, name) and name in cls.__dict__:
                val = cls.__dict__[name]
                if isinstance(val, FieldInfo):
                    if val.default is not MISSING:
                        default = val.default
                    if val.default_factory is not MISSING:
                        default_factory = val.default_factory
                    if val.description:
                        description = val.description
                else:
                    default = val

            # If field already existed, merge inherited values
            if name in combined_fields:
                parent_field = combined_fields[name]
                if default is MISSING and default_factory is MISSING:
                    default = parent_field.default
                    default_factory = parent_field.default_factory
                if not description:
                    description = parent_field.description

            combined_fields[name] = FieldInfo(
                default=default,
                default_factory=default_factory,
                description=description
            )
            combined_fields[name].type = actual_type

            # Clean up the FieldInfo from class level
            if name in cls.__dict__:
                val = cls.__dict__[name]
                if isinstance(val, FieldInfo):
                    if val.default is not MISSING:
                        setattr(cls, name, val.default)
                    else:
                        delattr(cls, name)

        # 4. Handle overridden fields without local type annotations
        for name in combined_fields:
            if name in cls.__dict__ and name not in annotations:
                val = cls.__dict__[name]
                if isinstance(val, FieldInfo):
                    if val.default is not MISSING:
                        combined_fields[name].default = val.default
                    if val.default_factory is not MISSING:
                        combined_fields[name].default_factory = val.default_factory
                    if val.description:
                        combined_fields[name].description = val.description
                    if val.default is not MISSING:
                        setattr(cls, name, val.default)
                    else:
                        delattr(cls, name)
                else:
                    combined_fields[name].default = val
                    combined_fields[name].default_factory = MISSING

        cls.__blueprint_fields__ = combined_fields

    if not typing.TYPE_CHECKING:
        def __init__(self, **kwargs):
            fields = self.__blueprint_fields__

            # Validate that all arguments passed are recognized fields
            extra = set(kwargs.keys()) - set(fields.keys())
            if extra:
                raise TypeError(f"__init__() got unexpected keyword arguments: {', '.join(map(repr, sorted(extra)))}")

            # Initialize private state
            self.__dict__["_initialized"] = False
            self.__dict__["_change_callbacks"] = []
            self.__dict__["_in_validation"] = False

            # Populate fields
            for name, field_info in fields.items():
                if name in kwargs:
                    val = kwargs[name]
                elif field_info.default is not MISSING:
                    val = field_info.default
                elif field_info.default_factory is not MISSING:
                    val = field_info.default_factory()
                else:
                    raise TypeError(f"__init__() missing required keyword-only argument: {repr(name)}")

                # Convert dicts or register callbacks recursively
                val = self._convert_value(val, field_info.type, self._validate_all)
                self.__dict__[name] = val

            self.__dict__["_initialized"] = True
            self._validate_all()

    def _convert_value(self, value, expected_type, callback):
        """Recursively converts input dicts into target BlueprintCfg classes and wraps lists/dicts in observable proxies."""
        origin = get_origin(expected_type)
        args = get_args(expected_type)

        # Handle Annotated
        if origin is Annotated:
            expected_type = args[0]
            origin = get_origin(expected_type)
            args = get_args(expected_type)

        # 1. Direct BlueprintCfg subclass
        if isinstance(expected_type, type) and issubclass(expected_type, BlueprintCfg):
            if isinstance(value, dict):
                inst = expected_type(**value)
                inst._register_change_callback(callback)
                return inst
            elif isinstance(value, BlueprintCfg):
                value._register_change_callback(callback)
                return value
            return value

        # 2. Union / UnionType
        is_union = False
        if origin is Union:
            is_union = True
        else:
            tp_name = getattr(expected_type, "__class__", None).__name__
            if tp_name == "UnionType":
                is_union = True

        if is_union:
            cfg_types = [arg for arg in args if isinstance(arg, type) and issubclass(arg, BlueprintCfg)]
            if len(cfg_types) == 1 and isinstance(value, dict):
                try:
                    inst = cfg_types[0](**value)
                    inst._register_change_callback(callback)
                    return inst
                except Exception:
                    pass
            elif len(cfg_types) > 1 and isinstance(value, dict):
                for cfg_type in cfg_types:
                    cfg_fields = getattr(cfg_type, "__blueprint_fields__", {})
                    if set(value.keys()).issubset(set(cfg_fields.keys())):
                        try:
                            inst = cfg_type(**value)
                            inst._register_change_callback(callback)
                            return inst
                        except Exception:
                            continue
            if isinstance(value, BlueprintCfg):
                value._register_change_callback(callback)
            return value

        # 3. Collection types
        if origin is list:
            if isinstance(value, list):
                item_type = args[0] if args else Any
                return ObservableList(value, callback, item_type, self._convert_value)
            return value

        elif origin is tuple:
            if isinstance(value, tuple):
                if args:
                    if len(args) == 2 and args[1] is Ellipsis:
                        item_type = args[0]
                        return tuple(self._convert_value(item, item_type, callback) for item in value)
                    return tuple(
                        self._convert_value(item, arg, callback)
                        for item, arg in zip(value, args)
                    )
                return value

        elif origin is dict:
            if isinstance(value, dict):
                key_type = args[0] if args and len(args) >= 1 else Any
                val_type = args[1] if args and len(args) >= 2 else Any
                return ObservableDict(value, callback, key_type, val_type, self._convert_value)
            return value

        return value

    def _register_change_callback(self, callback):
        callbacks = self.__dict__.get("_change_callbacks", [])
        if callback not in callbacks:
            callbacks.append(callback)

    def _notify_change(self):
        for cb in self.__dict__.get("_change_callbacks", []):
            cb()

    def _validate_all(self):
        """Validates all field types and triggers the custom validation check() hook."""
        if self.__dict__.get("_in_validation", False):
            return
        self.__dict__["_in_validation"] = True
        try:
            fields = getattr(self, "__blueprint_fields__", {})
            for name, field_info in fields.items():
                value = getattr(self, name)
                if not check_type(value, field_info.type):
                    raise TypeError(
                        f"Invalid type for field {repr(name)} in {self.__class__.__name__}. "
                        f"Expected {field_info.type}, got {type(value).__name__} ({repr(value)})"
                    )
            self.check()
            self._notify_change()
        finally:
            self.__dict__["_in_validation"] = False

    def check(self):
        """Custom post-validation hook. Subclasses override this to implement cross-field checks."""
        pass

    def __setattr__(self, name, value):
        fields = getattr(self, "__blueprint_fields__", {})
        if name not in fields:
            if name.startswith("_"):
                super().__setattr__(name, value)
                return
            raise AttributeError(f"{self.__class__.__name__} has no field {repr(name)}")

        field_info = fields[name]
        value = self._convert_value(value, field_info.type, self._validate_all)

        # Validate the new value type before assigning
        if not check_type(value, field_info.type):
            raise TypeError(
                f"Invalid type for field {repr(name)} in {self.__class__.__name__}. "
                f"Expected {field_info.type}, got {type(value).__name__} ({repr(value)})"
            )

        super().__setattr__(name, value)

        if self.__dict__.get("_initialized", False):
            self._validate_all()

    def __delattr__(self, name):
        fields = getattr(self, "__blueprint_fields__", {})
        if name in fields:
            raise AttributeError(f"Cannot delete field {repr(name)} of {self.__class__.__name__}")
        super().__delattr__(name)

    def __repr__(self):
        fields = getattr(self, "__blueprint_fields__", {})
        field_strs = []
        for name in fields:
            val = getattr(self, name)
            field_strs.append(f"{name}={repr(val)}")
        return f"{self.__class__.__name__}({', '.join(field_strs)})"

    def __eq__(self, other):
        if self.__class__ is not other.__class__:
            return NotImplemented
        fields = getattr(self, "__blueprint_fields__", {})
        return all(getattr(self, name) == getattr(other, name) for name in fields)
