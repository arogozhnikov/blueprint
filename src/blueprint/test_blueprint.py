# This file deliberately constructs invalid configs to exercise runtime validation
# (InvalidBlueprintError) -- pytest covers its correctness, not a static type checker.
# mypy: ignore-errors
# pyrefly: ignore-errors

import collections
import copy
import enum
import inspect
import pickle
import typing
import unittest
import warnings
from collections.abc import Mapping, MutableMapping, MutableSequence, MutableSet, Sequence
from collections.abc import Set as AbstractSet
from typing import Annotated, ClassVar, Generic, TypeVar

import blueprint
from blueprint import BlueprintCfg, ConfigDict, ConfigList, InvalidBlueprintError, field


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class ChildCfg(BlueprintCfg):
    name: str
    value: int = 10


class ParentCfg(BlueprintCfg):
    child: ChildCfg
    tag_list: Sequence[str] = field(default_factory=list)


class CustomCheckCfg(BlueprintCfg):
    min_val: int
    max_val: int

    def check(self):
        if self.min_val > self.max_val:
            raise ValueError("min_val cannot be greater than max_val")


def _make_cfg_class(**annotations):
    """Builds a BlueprintCfg subclass with the given field annotations, without needing a
    class statement at the call site -- lets rejection tests be written as one-liners that
    parametrize cleanly over many annotations."""
    return type("Cfg", (BlueprintCfg,), {"__annotations__": dict(annotations)})


class TestCollectionTypeSpellings(unittest.TestCase):
    """Covers the collection-field-type rules from MIGRATION_PLAN_COLLECTIONS.md: `tuple` is
    unchanged; list-like fields must be spelled `Sequence[T]` or `ConfigList[T]` (fully
    interchangeable, both stored as ConfigList); dict-like fields must be spelled
    `Mapping[K, V]` or `ConfigDict[K, V]` (interchangeable, stored as ConfigDict); set-like
    fields must be spelled `frozenset[T]` (strict) or `AbstractSet[T]` (loose, accepts a
    plain set too), both stored as frozenset. `list`, `dict`, `set`, and the `Mutable*` ABCs
    (bare, subscripted, or via their `typing.*` aliases) are all rejected at class-definition
    time -- blueprint has no mutable-tracked proxy for them.
    """

    # ---- tuple: unchanged -------------------------------------------------------------

    def test_tuple_field_type_checking_and_mutation(self):
        class TupleCfg(BlueprintCfg):
            pair: tuple[int, str]
            arbitrary: tuple[str, ...]

        cfg = TupleCfg(pair=(10, "ten"), arbitrary=("x", "y", "z"))
        self.assertEqual(cfg.pair, (10, "ten"))
        self.assertEqual(cfg.arbitrary, ("x", "y", "z"))

        with self.assertRaises(TypeError):
            TupleCfg(pair=(10, 20), arbitrary=())  # second element must be str
        with self.assertRaises(TypeError):
            TupleCfg(pair=(10, "ten"), arbitrary=("a", 2))  # wrong item type

        with cfg.mutable_copy() as y:
            y.arbitrary = ("a",)
            with self.assertRaises(TypeError):
                y.pair = (1, 2)
        self.assertEqual(cfg.arbitrary, ("x", "y", "z"))  # original untouched

    # ---- Sequence / ConfigList: interchangeable, list-like -----------------------------

    def test_sequence_field_wraps_list_and_leaves_tuple_alone(self):
        class SequenceCfg(BlueprintCfg):
            items: Sequence[int]

        cfg = SequenceCfg(items=[1, 2, 3])
        self.assertIsInstance(cfg.items, ConfigList)
        self.assertEqual(cfg.items, [1, 2, 3])

        cfg_tuple = SequenceCfg(items=(1, 2, 3))
        self.assertIsInstance(cfg_tuple.items, tuple)
        self.assertNotIsInstance(cfg_tuple.items, ConfigList)

        with self.assertRaises(TypeError):
            SequenceCfg(items=["a", "b"])  # wrong item type
        with self.assertRaises(TypeError):
            SequenceCfg(items="12")  # str satisfies Sequence but is deliberately excluded
        with self.assertRaises(TypeError):
            SequenceCfg(items={1: 2})  # a dict isn't a Sequence

        with cfg.mutable_copy() as y:
            y.items.append(4)
            with self.assertRaises(TypeError):
                y.items.append("nope")
        self.assertEqual(cfg.items, [1, 2, 3])  # original untouched

    def test_configlist_is_interchangeable_with_sequence(self):
        # Per the migration plan, `ConfigList[T]` is a pure alias of `Sequence[T]`: same
        # construction, same rejection, same mutation, and -- unlike the old strict `list[T]`
        # spelling it replaces -- a tuple is accepted here too.
        class ConfigListCfg(BlueprintCfg):
            items: ConfigList[int]

        cfg = ConfigListCfg(items=[1, 2, 3])
        self.assertIsInstance(cfg.items, ConfigList)

        cfg_tuple = ConfigListCfg(items=(1, 2, 3))
        self.assertIsInstance(cfg_tuple.items, tuple)

        with self.assertRaises(TypeError):
            ConfigListCfg(items=["a"])  # wrong item type

        with cfg.mutable_copy() as y:
            y.items.append(4)
        self.assertEqual(y.items, [1, 2, 3, 4])
        self.assertEqual(cfg.items, [1, 2, 3])

    def test_bare_sequence_and_configlist_annotations_are_wrapped_and_exclude_str(self):
        # Bare `Sequence`/`ConfigList` (no subscript) behave like their Any-typed
        # parameterized form -- still wrapped in ConfigList, and str/bytes/bytearray still
        # excluded even without an item type to check.
        class BareCfg(BlueprintCfg):
            a: Sequence
            b: ConfigList

        cfg = BareCfg(a=[1, "two"], b=[3, "four"])
        self.assertIsInstance(cfg.a, ConfigList)
        self.assertIsInstance(cfg.b, ConfigList)

        with self.assertRaises(TypeError):
            BareCfg(a="hello", b=[])
        with self.assertRaises(AttributeError):
            cfg.a.append(1)  # frozen outside mutable_copy()

    # ---- Mapping / ConfigDict: interchangeable, dict-like ------------------------------

    def test_mapping_field_wraps_dict_and_rejects_wrong_shapes(self):
        class MappingCfg(BlueprintCfg):
            data: Mapping[str, int]

        cfg = MappingCfg(data={"a": 1, "b": 2})
        self.assertIsInstance(cfg.data, ConfigDict)
        self.assertEqual(cfg.data, {"a": 1, "b": 2})

        with self.assertRaises(TypeError):
            MappingCfg(data={"a": "not-an-int"})
        with self.assertRaises(TypeError):
            MappingCfg(data={1: 1})
        with self.assertRaises(TypeError):
            MappingCfg(data=[("a", 1)])  # a list of pairs isn't a Mapping

        with cfg.mutable_copy() as y:
            y.data["c"] = 3
            with self.assertRaises(TypeError):
                y.data["d"] = "nope"
        self.assertEqual(cfg.data, {"a": 1, "b": 2})  # original untouched

    def test_configdict_is_interchangeable_with_mapping(self):
        class ConfigDictCfg(BlueprintCfg):
            data: ConfigDict[str, int]

        cfg = ConfigDictCfg(data={"a": 1})
        self.assertIsInstance(cfg.data, ConfigDict)

        with self.assertRaises(TypeError):
            ConfigDictCfg(data={"a": "nope"})

        with cfg.mutable_copy() as y:
            y.data["b"] = 2
        self.assertEqual(y.data, {"a": 1, "b": 2})
        self.assertEqual(cfg.data, {"a": 1})

    def test_bare_mapping_and_configdict_annotations_are_wrapped(self):
        class BareCfg(BlueprintCfg):
            a: Mapping
            b: ConfigDict

        cfg = BareCfg(a={"x": 1}, b={"y": 2})
        self.assertIsInstance(cfg.a, ConfigDict)
        self.assertIsInstance(cfg.b, ConfigDict)
        with self.assertRaises(AttributeError):
            cfg.a["z"] = 3  # frozen outside mutable_copy()

    # ---- frozenset / AbstractSet: unchanged, set-like -----------------------------------

    def test_frozenset_field_is_strict_and_does_not_alias_source(self):
        class FrozenSetCfg(BlueprintCfg):
            tags: frozenset[str]

        source = frozenset({"a", "b"})
        cfg = FrozenSetCfg(tags=source)
        self.assertEqual(cfg.tags, source)
        self.assertIsNot(cfg.tags, source)
        FrozenSetCfg(tags={"a", "b"})  # can assign plain set, that's fine.

        with self.assertRaises(TypeError):
            FrozenSetCfg(tags=frozenset({"a", 1}))  # wrong item type

    def test_bare_frozenset_annotation_is_still_type_checked(self):
        class BareCfg(BlueprintCfg):
            items: frozenset

        cfg = BareCfg(items=frozenset({1, "two"}))
        self.assertEqual(cfg.items, frozenset({1, "two"}))
        with self.assertRaises(TypeError):
            BareCfg(items=[1, 2])

    def test_abstractset_field_accepts_set_and_frozenset(self):
        class AbstractSetCfg(BlueprintCfg):
            tags: AbstractSet[str]

        cfg = AbstractSetCfg(tags={"a", "b"})
        self.assertIsInstance(cfg.tags, frozenset)
        self.assertEqual(cfg.tags, {"a", "b"})

        cfg_frozen = AbstractSetCfg(tags=frozenset({"a", "b"}))
        self.assertIsInstance(cfg_frozen.tags, frozenset)

        with self.assertRaises(TypeError):
            AbstractSetCfg(tags={"a", 1})  # wrong item type
        with self.assertRaises(TypeError):
            AbstractSetCfg(tags=["a", "b"])  # a list satisfies neither set nor frozenset

    def test_bare_abstractset_annotation_is_still_type_checked(self):
        class BareCfg(BlueprintCfg):
            items: AbstractSet

        cfg = BareCfg(items={1, "two"})
        self.assertIsInstance(cfg.items, frozenset)
        with self.assertRaises(TypeError):
            BareCfg(items=[1, 2])

    # ---- optional collections, nested configs ------------------------------------------

    def test_optional_collections_accept_none(self):
        class OptionalCfg(BlueprintCfg):
            mapping: Mapping[str, int] | None
            sequence: Sequence[int] | None

        cfg_none = OptionalCfg(mapping=None, sequence=None)
        self.assertIsNone(cfg_none.mapping)
        self.assertIsNone(cfg_none.sequence)

        cfg = OptionalCfg(mapping={"a": 1}, sequence=[1, 2])
        self.assertEqual(cfg.mapping, {"a": 1})
        self.assertEqual(cfg.sequence, [1, 2])

        with cfg.mutable_copy() as y:
            y.mapping = None
            self.assertIsNone(y.mapping)
            with self.assertRaises(TypeError):
                y.sequence = {"not": "a-sequence"}

    def test_nested_configs_in_sequence_and_mapping_fields(self):
        class GroupCfg(BlueprintCfg):
            members: Sequence[ChildCfg]
            by_name: Mapping[str, ChildCfg]

        cfg = GroupCfg(
            members=[ChildCfg(name="C1"), ChildCfg(name="C2", value=50)],
            by_name={"c1": ChildCfg(name="C1")},
        )
        self.assertIsInstance(cfg.members[0], ChildCfg)
        self.assertEqual(cfg.members[1].value, 50)
        self.assertIsInstance(cfg.by_name["c1"], ChildCfg)

        with cfg.mutable_copy() as y:
            y.members.append(ChildCfg(name="C3"))
            y.members[0].value = 999  # cascaded direct mutation
            y.by_name["c2"] = ChildCfg(name="C2")
        self.assertEqual(len(cfg.members), 2)  # original untouched
        self.assertEqual(cfg.members[0].value, 10)
        self.assertEqual(len(y.members), 3)
        self.assertEqual(y.by_name["c2"].name, "C2")

    # ---- rejections: list/dict/set and the Mutable* ABCs -------------------------------

    def test_list_type_is_rejected(self):
        for annotation in (list, list[int], typing.List, typing.List[int]):  # noqa: UP006
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_dict_type_is_rejected(self):
        for annotation in (dict, dict[str, int], typing.Dict, typing.Dict[str, int]):  # noqa: UP006
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_set_type_is_rejected(self):
        # blueprint intentionally does NOT support `set`/`set[T]` as a field type: there is
        # no mutable-tracked set proxy (unlike ConfigList/ConfigDict), and silently storing
        # it as an immutable frozenset would make the annotation lie about what the field
        # holds. Use `frozenset[T]`/`AbstractSet[T]` instead.
        for annotation in (set, set[int], typing.Set, typing.Set[int]):  # noqa: UP006
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_mutable_abc_types_are_rejected(self):
        for annotation in (
            MutableSequence,
            MutableSequence[int],
            MutableMapping,
            MutableMapping[str, int],
            MutableSet,
            MutableSet[int],
        ):
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_structurally_mutable_types_are_rejected(self):
        # Not an enumerated name -- caught by the structural `issubclass(...,
        # MutableSequence | MutableMapping | MutableSet)` fallback, proving the check isn't
        # just a hardcoded list of names.
        for annotation in (bytearray, collections.deque[int]):
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_disallowed_types_are_rejected_when_nested(self):
        # The rejection recurses into a container's item/key/value type, Union members, etc.
        # -- not just a field's top-level annotation.
        for annotation in (
            Sequence[dict[str, int]],
            Mapping[str, list[int]],
            list[int] | None,
            MutableMapping[str, int] | None,
        ):
            with self.subTest(annotation=annotation), self.assertRaises(TypeError):
                _make_cfg_class(x=annotation)

    def test_item_level_errors_are_plain_typeerror_not_invalidblueprinterror(self):
        # An item/key/value type mismatch inside an otherwise-correctly-shaped container is
        # a plain TypeError raised by ConfigList/ConfigDict itself -- both at construction and
        # during mutation -- distinct from the InvalidBlueprintError _validate_self() raises
        # for a whole-field shape mismatch (e.g. a dict given where a Sequence was expected).
        class SequenceCfg(BlueprintCfg):
            items: Sequence[int]

        with self.assertRaises(InvalidBlueprintError):
            SequenceCfg(items={1: 2})  # whole-field shape mismatch

        with self.assertRaises(TypeError) as ctx:
            SequenceCfg(items=["a", "b"])  # item-level mismatch, still at construction
        self.assertNotIsInstance(ctx.exception, InvalidBlueprintError)

        cfg = SequenceCfg(items=[1, 2, 3])
        with cfg.mutable_copy() as y:
            with self.assertRaises(TypeError) as ctx2:
                y.items.append("nope")  # item-level mismatch, during mutation
            self.assertNotIsInstance(ctx2.exception, InvalidBlueprintError)

    def test_optional_collections_accept_none_for_every_spelling(self):
        class OptionalCfg(BlueprintCfg):
            a: ConfigList[int] | None
            b: ConfigDict[str, int] | None
            c: frozenset[int] | None
            d: AbstractSet[int] | None

        cfg_none = OptionalCfg(a=None, b=None, c=None, d=None)
        self.assertIsNone(cfg_none.a)
        self.assertIsNone(cfg_none.b)
        self.assertIsNone(cfg_none.c)
        self.assertIsNone(cfg_none.d)

        cfg = OptionalCfg(a=[1, 2], b={"x": 1}, c=frozenset({1}), d={1, 2})
        self.assertEqual(cfg.a, [1, 2])
        self.assertEqual(cfg.b, {"x": 1})
        self.assertEqual(cfg.c, frozenset({1}))
        self.assertEqual(cfg.d, frozenset({1, 2}))


class TestCollectionMutability(unittest.TestCase):
    """Covers: `mutable_copy()` mechanics as they interact with ConfigList/ConfigDict fields
    and configs nested inside them -- mutation is prohibited outside the block, cascades to
    nested items inside it, and never touches the original."""

    def test_sequence_field_mutation_outside_mutable_copy_raises(self):
        p = ParentCfg(child=ChildCfg(name="A"), tag_list=["x"])
        with self.assertRaises(AttributeError):
            p.tag_list.append("y")
        with self.assertRaises(AttributeError):
            p.tag_list[0] = "z"
        with self.assertRaises(AttributeError):
            del p.tag_list[0]
        self.assertEqual(p.tag_list, ["x"])

    def test_mapping_field_mutation_outside_mutable_copy_raises(self):
        class RegistryCfg(BlueprintCfg):
            items: Mapping[str, int]

        reg = RegistryCfg(items={"a": 1})
        with self.assertRaises(AttributeError):
            reg.items["b"] = 2
        with self.assertRaises(AttributeError):
            del reg.items["a"]
        with self.assertRaises(AttributeError):
            reg.items.update({"c": 3})
        self.assertEqual(reg.items, {"a": 1})

    def test_sequence_field_type_checked_always_and_locked_after_exit(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with p.mutable_copy() as y:
            y.tag_list = ["a", "b", "c"]
            with self.assertRaises(TypeError):
                y.tag_list.append(123)  # field-level checks always run, even mid-edit
            self.assertEqual(y.tag_list, ["a", "b", "c"])
        self.assertEqual(y.tag_list, ["a", "b", "c"])
        with self.assertRaises(AttributeError):
            y.tag_list.append("d")  # locked again after exit, even for a valid mutation

    def test_lists_are_independent_between_original_and_copy(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with p.mutable_copy() as y:
            y.tag_list.append("y")
        self.assertEqual(p.tag_list, ["x"])
        self.assertEqual(y.tag_list, ["x", "y"])

    def test_nested_list_of_configs_direct_mutation_and_append(self):
        class GroupCfg(BlueprintCfg):
            children: Sequence[ChildCfg]

        cfg = GroupCfg(children=[ChildCfg(name="C1", value=1), ChildCfg(name="C2", value=2)])
        with cfg.mutable_copy() as y:
            y.children[0].value = 999  # direct mutation of a list item, cascaded
            y.children.append(ChildCfg(name="C3", value=3))
            y.children[2].value = 30  # freshly appended item is mutable too
        self.assertEqual(y.children[0].value, 999)
        self.assertEqual(len(y.children), 3)
        self.assertEqual(y.children[2].value, 30)
        self.assertEqual(cfg.children[0].value, 1)  # original untouched
        self.assertEqual(len(cfg.children), 2)

    def test_mapping_field_of_nested_configs(self):
        class RegistryCfg(BlueprintCfg):
            items: Mapping[str, ChildCfg]

        reg = RegistryCfg(items={"a": ChildCfg(name="A", value=1)})
        with reg.mutable_copy() as y:
            y.items["a"].value = 42  # direct mutation of a dict value, cascaded

            y.items["b"] = ChildCfg(name="B", value=2)
            y.items["b"].value = 20  # freshly attached value is mutable too

            # assigning a reference to an existing, external instance deep-copies it on
            # the way in, so it's independently mutable here without touching the source
            y.items["c"] = reg.items["a"]
            y.items["c"].value = 43

        self.assertEqual(y.items["a"].value, 42)
        self.assertEqual(y.items["b"].value, 20)
        self.assertEqual(y.items["c"].value, 43)
        self.assertEqual(reg.items["a"].value, 1)  # original untouched
        self.assertEqual(len(reg.items), 1)

    def test_deep_nested_cascade_and_deferred_check(self):
        class GrandchildCfg(BlueprintCfg):
            score: int

            def check(self):
                if self.score < 0:
                    raise ValueError("score cannot be negative")

        class MiddleCfg(BlueprintCfg):
            grandchildren: Sequence[GrandchildCfg]

        class TopCfg(BlueprintCfg):
            middle: MiddleCfg

        top = TopCfg(middle=MiddleCfg(grandchildren=[GrandchildCfg(score=5)]))

        with top.mutable_copy() as y:
            y.middle.grandchildren[0].score = -1  # temporarily invalid; check() is deferred
            y.middle.grandchildren[0].score = 7  # fixed before the block exits
        self.assertEqual(y.middle.grandchildren[0].score, 7)
        self.assertEqual(top.middle.grandchildren[0].score, 5)  # original untouched

        with self.assertRaises(ValueError):
            with top.mutable_copy() as y2:
                y2.middle.grandchildren[0].score = -5  # left invalid at exit
        self.assertEqual(top.middle.grandchildren[0].score, 5)  # original untouched

    def test_observable_configlist_has_own_is_blueprint_mutable_flag(self):
        p = ParentCfg(child=ChildCfg(name="A"), tag_list=["x"])
        self.assertFalse(p.tag_list._is_blueprint_mutable)
        with p.mutable_copy() as y:
            self.assertTrue(y.tag_list._is_blueprint_mutable)
        self.assertFalse(y.tag_list._is_blueprint_mutable)

    def test_observable_configdict_has_own_is_blueprint_mutable_flag(self):
        class RegistryCfg(BlueprintCfg):
            items: Mapping[str, int]

        reg = RegistryCfg(items={"a": 1})
        self.assertFalse(reg.items._is_blueprint_mutable)
        with reg.mutable_copy() as y:
            self.assertTrue(y.items._is_blueprint_mutable)
            y.items["b"] = 2
        self.assertFalse(y.items._is_blueprint_mutable)

    def test_freshly_attached_nested_config_is_blueprint_mutable_for_rest_of_block(self):
        x = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with x.mutable_copy() as y:
            y.child = ChildCfg(name="B", value=2)  # freshly attached instance
            y.child.name = "C"  # immediately editable within the same block
        self.assertEqual(y.child.name, "C")
        self.assertEqual(x.child.name, "A")  # original untouched
        with self.assertRaises(AttributeError):
            y.child.name = "D"

    def test_temporarily_inconsistent_cross_field_state_allowed(self):
        x = CustomCheckCfg(min_val=1, max_val=10)
        with x.mutable_copy() as y:
            y.min_val = 100  # temporarily invalid: 100 > 10
            y.max_val = 200  # valid again: 100 <= 200
        self.assertEqual((y.min_val, y.max_val), (100, 200))
        self.assertEqual((x.min_val, x.max_val), (1, 10))  # original untouched

    def test_final_invalid_cross_field_state_raises(self):
        x = CustomCheckCfg(min_val=1, max_val=10)
        with self.assertRaises(ValueError):
            with x.mutable_copy() as y:
                y.min_val = 100  # left invalid (100 > 10) at exit

    def test_invalid_type_raises_immediately_not_deferred(self):
        x = ChildCfg(name="A", value=1)
        with x.mutable_copy() as y:
            with self.assertRaises(TypeError):
                y.value = "nope"
            self.assertEqual(y.value, 1)  # the failed assignment didn't happen
            y.value = 2
        self.assertEqual(y.value, 2)
        self.assertEqual(x.value, 1)  # original untouched throughout

    def test_unrelated_exception_leaves_original_untouched_and_unlocks_copy(self):
        x = ChildCfg(name="A", value=1)
        with self.assertRaises(KeyError):
            with x.mutable_copy() as y:
                y.name = "B"
                raise KeyError("boom")
        self.assertEqual(x.name, "A")
        self.assertFalse(y._is_blueprint_mutable)
        with self.assertRaises(AttributeError):
            y.name = "C"


class TestImmutableByDefault(unittest.TestCase):
    """Covers: every container type the package ships -- ConfigList, ConfigDict, and
    BlueprintCfg -- is immutable unless explicitly unlocked via mutable_copy()."""

    def test_class_level_flag_defaults_to_immutable(self):
        for cls in (ConfigList, ConfigDict, BlueprintCfg):
            with self.subTest(cls=cls.__name__):
                self.assertFalse(cls._is_blueprint_mutable)

    def test_mutation_raises_by_default(self):
        lst = ConfigList([1, 2], int)
        with self.assertRaises(AttributeError):
            lst.append(3)
        with self.assertRaises(AttributeError):
            lst[0] = 9
        self.assertEqual(lst, [1, 2])

        dct = ConfigDict({"a": 1}, str, int)
        with self.assertRaises(AttributeError):
            dct["b"] = 2
        with self.assertRaises(AttributeError):
            del dct["a"]
        self.assertEqual(dct, {"a": 1})

    def test_nested_containers_of_a_fresh_cfg_are_also_immutable(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        self.assertFalse(p._is_blueprint_mutable)
        self.assertFalse(p.child._is_blueprint_mutable)
        self.assertFalse(p.tag_list._is_blueprint_mutable)


class TestNoAliasingAndSerialization(unittest.TestCase):
    """Covers: deepcopy/pickle/as_dict()/format() all treat ConfigList/ConfigDict fields
    with the same "everything is independently owned, nothing is aliased" guarantee as
    plain fields."""

    def test_deepcopy_produces_independent_copy(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        d = copy.deepcopy(p)

        self.assertEqual(d, p)
        self.assertIsNot(d, p)
        self.assertIsNot(d.child, p.child)
        self.assertIsNot(d.tag_list, p.tag_list)

        with d.mutable_copy() as dm:
            dm.child.value = 99
            dm.tag_list.append("z")
        self.assertEqual(p.child.value, 1)
        self.assertEqual(p.tag_list, ["x", "y"])
        self.assertEqual(d.tag_list, ["x", "y"])  # d itself untouched -- dm is its own clone

    def test_construction_does_not_alias_source_container(self):
        # Everything assigned into a config is deep-copied on the way in -- mutating the
        # caller's original list after construction must never be visible on the field.
        source_tags = ["a", "b"]
        cfg = ParentCfg(child=ChildCfg(name="A"), tag_list=source_tags)
        source_tags.append("c")
        self.assertEqual(cfg.tag_list, ["a", "b"])

    def test_pickle_roundtrip_produces_independent_copy(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        p2 = pickle.loads(pickle.dumps(p))

        self.assertEqual(p2, p)
        self.assertIsNot(p2, p)
        self.assertIsNot(p2.tag_list, p.tag_list)

        with self.assertRaises(AttributeError):
            p2.tag_list.append("z")
        with p2.mutable_copy() as p2m:
            p2m.tag_list.append("z")
        self.assertEqual(p.tag_list, ["x", "y"])
        self.assertEqual(p2m.tag_list, ["x", "y", "z"])

    def test_as_dict_nested_containers(self):
        class Nested(BlueprintCfg):
            color: Color = Color.RED
            point: tuple[int, int] = (1, 2)
            mapping: Mapping[str, int] = field(default_factory=dict)
            children: Sequence[ChildCfg] = field(default_factory=list)
            tags: frozenset[str] = field(default_factory=frozenset)

        n = Nested(mapping={"a": 1}, children=[ChildCfg(name="c1")], tags=frozenset({"x", "y"}))
        d = n.as_dict()
        self.assertEqual(
            d,
            {
                "color": Color.RED,
                "point": (1, 2),
                "mapping": {"a": 1},
                "children": [{"name": "c1", "value": 10}],
                "tags": frozenset({"x", "y"}),
            },
        )
        self.assertIs(type(d["mapping"]), dict)
        self.assertIs(type(d["children"]), list)
        self.assertIs(type(d["children"][0]), dict)
        self.assertIs(type(d["tags"]), frozenset)

    def test_as_dict_does_not_alias_internal_containers(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        d = p.as_dict()
        d["tag_list"].append("z")
        d["child"]["value"] = 999
        self.assertEqual(p.tag_list, ["x", "y"])
        self.assertEqual(p.child.value, 1)

    def test_as_dict_selected_fields(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        self.assertEqual(
            p.as_dict_selected_fields(["child", "tag_list"]),
            {"child": {"name": "A", "value": 1}, "tag_list": ["x", "y"]},
        )
        with self.assertRaises(TypeError):
            p.as_dict_selected_fields(["child", "nope"])

    def test_format_frozenset_is_sorted_and_deterministic(self):
        class FrozenSetCfg(BlueprintCfg):
            tags: frozenset[str] = field(default_factory=frozenset)

        cfg = FrozenSetCfg(tags=frozenset({"charlie", "alpha", "bravo"}))
        self.assertEqual(
            blueprint.format(cfg),
            "FrozenSetCfg(tags=frozenset({'alpha', 'bravo', 'charlie'}))",
        )
        self.assertEqual(blueprint.format(FrozenSetCfg()), "FrozenSetCfg(tags=frozenset())")


class TestInvalidBlueprintError(unittest.TestCase):
    """Covers: InvalidBlueprintError, raised by _validate_self() for field type mismatches."""

    def test_is_a_type_error_subclass(self):
        self.assertTrue(issubclass(InvalidBlueprintError, TypeError))
        with self.assertRaises(TypeError):
            ChildCfg(name="Test", value="not an int")

    def test_collects_every_failing_field_not_just_the_first(self):
        class TwoBadFields(BlueprintCfg):
            a: int
            b: str

        with self.assertRaises(InvalidBlueprintError) as ctx:
            TwoBadFields(a="not an int", b=123)

        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertIn("TwoBadFields.a", ctx.exception.errors[0])
        self.assertIn("TwoBadFields.b", ctx.exception.errors[1])


class TestClassVar(unittest.TestCase):
    """Covers: `ClassVar`-annotated attributes -- class-level state, type-checked once at
    class-creation time, locked against reassignment/deletion afterward. A ClassVar with no
    value anywhere in the MRO makes the class abstract instead of raising."""

    def test_excluded_from_fields_and_instance_construction(self):
        class ServerCfg(BlueprintCfg):
            kind: ClassVar[str] = "server"
            host: str = "localhost"

        self.assertNotIn("kind", ServerCfg.__blueprint_fields__)
        cfg = ServerCfg()
        self.assertEqual(cfg.kind, "server")
        with self.assertRaises(TypeError):
            ServerCfg(kind="other")  # not a constructor kwarg

    def test_bad_type_raises_at_class_creation_time(self):
        with self.assertRaises(InvalidBlueprintError):

            class BadCfg(BlueprintCfg):
                kind: ClassVar[str] = 123  # type: ignore

    def test_missing_value_makes_class_abstract_instead_of_raising(self):
        class NoValueCfg(BlueprintCfg):
            kind: ClassVar[str]

        self.assertTrue(inspect.isabstract(NoValueCfg))
        with self.assertRaises(TypeError):
            NoValueCfg()

    def test_subclass_supplying_missing_value_becomes_instantiable(self):
        class NoValueCfg(BlueprintCfg):
            kind: ClassVar[str]

        class ServerCfg(NoValueCfg):
            kind: ClassVar[str] = "server"

        self.assertFalse(inspect.isabstract(ServerCfg))
        self.assertEqual(ServerCfg().kind, "server")

    def test_grandchild_without_override_stays_abstract(self):
        # Recomputed fresh for every class in the chain -- a grandchild that still doesn't
        # supply a value stays abstract, regardless of whether its immediate parent is itself
        # abstract or the class that first declared the ClassVar.
        class NoValueCfg(BlueprintCfg):
            kind: ClassVar[str]

        class StillAbstractCfg(NoValueCfg):
            host: str = "localhost"

        self.assertTrue(inspect.isabstract(StillAbstractCfg))
        with self.assertRaises(TypeError):
            StillAbstractCfg()

    def test_reassignment_and_deletion_after_class_creation_raises(self):
        class ServerCfg(BlueprintCfg):
            kind: ClassVar[str] = "server"

        with self.assertRaises(AttributeError):
            ServerCfg.kind = "other"
        with self.assertRaises(AttributeError):
            del ServerCfg.kind
        cfg = ServerCfg()
        with self.assertRaises(AttributeError):
            cfg.kind = "other"

    def test_subclass_can_override_independently_in_its_own_class_body(self):
        class ServerCfg(BlueprintCfg):
            kind: ClassVar[str] = "server"

        class WorkerCfg(ServerCfg):
            kind: ClassVar[str] = "worker"

        self.assertEqual(WorkerCfg.kind, "worker")
        self.assertEqual(ServerCfg.kind, "server")  # base class untouched


class TestUncheckedField(unittest.TestCase):
    """Covers: `unchecked_field()`, the per-field escape hatch that skips check_type()
    entirely for one field."""

    def test_accepts_values_field_would_reject(self):
        class Handle:
            def __init__(self, label):
                self.label = label

        class JobCfg(BlueprintCfg):
            name: str
            handle: Handle = blueprint.unchecked_field(default_factory=lambda: Handle("none"))

        cfg = JobCfg(name="name", handle=Handle("handle"))  # happy path
        with self.assertRaises(InvalidBlueprintError):
            JobCfg(name="name", handle="not a handle")

        with self.assertRaises(AttributeError):
            cfg.handle = object()  # still raises on plain assignment
        cfg.handle.label = "object itself is not checked"

        with cfg.mutable_copy() as m:
            m.handle = Handle(label="still can reassign")
        self.assertEqual(m.handle.label, "still can reassign")

    def test_default_and_default_factory_still_work(self):
        class Cfg(BlueprintCfg):
            a: object = blueprint.unchecked_field(default=1)
            b: object = blueprint.unchecked_field(default_factory=list)

        cfg = Cfg()
        self.assertEqual(cfg.a, 1)
        self.assertEqual(cfg.b, [])

    def test_repr_marks_unchecked(self):
        self.assertIn("unchecked=True", repr(blueprint.unchecked_field(default=1)))
        self.assertNotIn("unchecked=True", repr(field(default=1)))


class TestCheckMro(unittest.TestCase):
    """Covers: _validate_self() runs every check() found along the MRO (base class first),
    not just the most-derived override."""

    def test_every_level_runs_base_first_without_super_call(self):
        calls = []

        class Base(BlueprintCfg):
            a: int = 1

            def check(self):
                calls.append("Base")

        class Mid(Base):
            b: int = 2

            def check(self):
                calls.append("Mid")  # deliberately does not call super().check()

        class Leaf(Mid):
            c: int = 3

        Leaf()
        self.assertEqual(calls, ["Base", "Mid"])

    def test_base_check_failure_blocks_construction_before_subclass_check_runs(self):
        calls = []

        class Base(BlueprintCfg):
            a: int = 1

            def check(self):
                calls.append("Base")
                if self.a < 0:
                    raise ValueError("a must be >= 0")

        class Leaf(Base):
            def check(self):
                calls.append("Leaf")

        with self.assertRaises(ValueError):
            Leaf(a=-1)
        self.assertEqual(calls, ["Base"])  # Leaf's check() never ran

    def test_each_ancestors_check_enforced_independently(self):
        class Base(BlueprintCfg):
            a: int = 1

            def check(self):
                if self.a < 0:
                    raise ValueError("a must be >= 0")

        class Leaf(Base):
            b: int = 1

            def check(self):
                if self.b < 0:
                    raise ValueError("b must be >= 0")

        Leaf(a=1, b=1)  # both pass
        with self.assertRaises(ValueError):
            Leaf(a=-1, b=1)  # Base's check fails
        with self.assertRaises(ValueError):
            Leaf(a=1, b=-1)  # Leaf's check fails

    def test_runs_again_on_mutable_copy_exit(self):
        calls = []

        class Base(BlueprintCfg):
            a: int = 1

            def check(self):
                calls.append("Base")

        class Leaf(Base):
            def check(self):
                calls.append("Leaf")

        cfg = Leaf()
        with cfg.mutable_copy():
            pass
        self.assertEqual(calls[-2:], ["Base", "Leaf"])


class TestMutableCopyExceptionHandling(unittest.TestCase):
    """Covers: an unrelated exception raised inside a mutable_copy() block while the clone
    is left in a temporarily-invalid cross-field state."""

    def test_original_exception_propagates_with_original_type(self):
        cfg = CustomCheckCfg(min_val=10, max_val=20)
        with self.assertWarns(UserWarning):
            with self.assertRaises(RuntimeError):
                with cfg.mutable_copy() as y:
                    y.min_val = 25  # leaves y invalid: 25 > 20
                    raise RuntimeError("boom")
        self.assertEqual(cfg.min_val, 10)  # original untouched

    def test_no_warning_when_block_completes_normally(self):
        cfg = CustomCheckCfg(min_val=10, max_val=20)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning here fails the test
            with self.assertRaises(ValueError):
                with cfg.mutable_copy() as y:
                    y.min_val = 25  # invalid at exit, no earlier exception involved
        self.assertEqual(cfg.min_val, 10)


class TestDangerouslyAllMutable(unittest.TestCase):
    """Covers: the `dangerously_all_mutable()` global escape hatch."""

    def test_mutates_original_in_place_not_a_copy(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.dangerously_all_mutable():
            x.value = 2
        self.assertEqual(x.value, 2)
        with self.assertRaises(AttributeError):
            x.value = 3  # prohibited again after the block exits

    def test_sequence_fields_mutable_inside_block(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with blueprint.dangerously_all_mutable():
            p.tag_list.append("y")
        self.assertEqual(p.tag_list, ["x", "y"])
        with self.assertRaises(AttributeError):
            p.tag_list.append("z")

    def test_field_level_type_checks_still_apply(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.dangerously_all_mutable():
            with self.assertRaises(TypeError):
                x.value = "not an int"

    def test_nestable(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.dangerously_all_mutable():
            with blueprint.dangerously_all_mutable():
                x.value = 2
            x.value = 3  # still inside the outer block -- mutation still allowed
        self.assertEqual(x.value, 3)
        with self.assertRaises(AttributeError):
            x.value = 4


class TestDebugProhibitMutability(unittest.TestCase):
    """Covers: the `debug_prohibit_mutability()` global escape hatch."""

    def test_mutable_copy_raises_inside_block_and_works_again_after(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.debug_prohibit_mutability():
            with self.assertRaises(RuntimeError):
                with x.mutable_copy():
                    pass
        with x.mutable_copy() as y:
            y.value = 2
        self.assertEqual(y.value, 2)

    def test_nestable(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.debug_prohibit_mutability():
            with blueprint.debug_prohibit_mutability():
                with self.assertRaises(RuntimeError):
                    with x.mutable_copy():
                        pass
            # still inside the outer block
            with self.assertRaises(RuntimeError):
                with x.mutable_copy():
                    pass
        with x.mutable_copy() as y:
            y.value = 2
        self.assertEqual(y.value, 2)

    def test_independent_from_dangerously_all_mutable(self):
        x = ChildCfg(name="A", value=1)
        with blueprint.debug_prohibit_mutability():
            with blueprint.dangerously_all_mutable():
                x.value = 2  # allowed: direct assignment, not via mutable_copy()
                with self.assertRaises(RuntimeError):
                    with x.mutable_copy():
                        pass
        self.assertEqual(x.value, 2)


class TestGenerics(unittest.TestCase):
    """Covers: `BlueprintCfg` subclasses that are also `Generic[T]`, with collection fields
    resolved against the new Sequence/Mapping spellings."""

    def test_unresolved_typevar_behaves_like_any(self):
        T = TypeVar("T")

        class PlainGenericBase(BlueprintCfg, Generic[T]):
            x: Sequence[T]

        class PlainDerived(PlainGenericBase):
            pass

        cfg = PlainDerived(x=[1, 2, 3])
        self.assertEqual(cfg.x, [1, 2, 3])
        PlainDerived(x=["a", "b"])  # unresolved T accepts any item type, like Any

    def test_typevar_resolved_via_subscripted_base(self):
        T = TypeVar("T")

        class PlainGenericBase(BlueprintCfg, Generic[T]):
            x: Sequence[T]

        class PlainDerivedAsInt(PlainGenericBase[int]):
            pass

        self.assertEqual(PlainDerivedAsInt.__blueprint_fields__["x"].type, Sequence[int])
        PlainDerivedAsInt(x=[1, 2, 3])
        with self.assertRaises(TypeError):
            PlainDerivedAsInt(x=["1", "2"])

    def test_multi_level_generic_inheritance_composes(self):
        T = TypeVar("T")
        T2 = TypeVar("T2")

        class Base(BlueprintCfg, Generic[T]):
            x: Sequence[T]

        class Mid(Base[T2], Generic[T2]):
            pass

        class Concrete(Mid[int]):
            pass

        self.assertEqual(Concrete.__blueprint_fields__["x"].type, Sequence[int])
        Concrete(x=[1, 2, 3])
        with self.assertRaises(TypeError):
            Concrete(x=["nope"])

    def test_typevar_bound_restricts_unresolved_field(self):
        class Animal(BlueprintCfg):
            name: str

        class Rock:
            pass

        T = TypeVar("T", bound=Animal)

        class Pen(BlueprintCfg, Generic[T]):
            occupant: T

        class AnyPen(Pen):
            pass

        AnyPen(occupant=Animal(name="Generic Animal"))
        with self.assertRaises(InvalidBlueprintError):
            AnyPen(occupant=Rock())

    def test_generic_mapping_field_resolved(self):
        K = TypeVar("K")
        V = TypeVar("V")

        class MappingBase(BlueprintCfg, Generic[K, V]):
            data: Mapping[K, V]

        class StrToIntMapping(MappingBase[str, int]):
            pass

        self.assertEqual(StrToIntMapping.__blueprint_fields__["data"].type, Mapping[str, int])
        StrToIntMapping(data={"a": 1})
        with self.assertRaises(TypeError):
            StrToIntMapping(data={"a": "not an int"})

    def test_typevar_constraints_restrict_unresolved_field(self):
        T = TypeVar("T", int, str)

        class Cell(BlueprintCfg, Generic[T]):
            value: T

        class AnyCell(Cell):
            pass

        AnyCell(value=1)
        AnyCell(value="a")
        with self.assertRaises(InvalidBlueprintError):
            AnyCell(value=1.5)

    def test_multiple_subclasses_of_same_generic_base_resolve_independently(self):
        T = TypeVar("T")

        class Box(BlueprintCfg, Generic[T]):
            item: T

        class IntBox(Box[int]):
            pass

        class StrBox(Box[str]):
            pass

        IntBox(item=1)
        StrBox(item="a")
        with self.assertRaises(InvalidBlueprintError):
            IntBox(item="a")
        with self.assertRaises(InvalidBlueprintError):
            StrBox(item=1)

    def test_generic_field_nested_in_union_resolved(self):
        T = TypeVar("T")

        class OptionalBox(BlueprintCfg, Generic[T]):
            item: T | None

        class OptionalIntBox(OptionalBox[int]):
            pass

        OptionalIntBox(item=1)
        OptionalIntBox(item=None)
        with self.assertRaises(InvalidBlueprintError):
            OptionalIntBox(item="a")


class TestMiscellaneous(unittest.TestCase):
    """Light coverage for behavior the collection-type migration doesn't touch, kept here so
    it isn't lost entirely: numeric bounds and multiple inheritance with a non-BlueprintCfg
    mixin."""

    def test_numeric_bounds_via_field_and_annotated(self):
        class BoundedCfg(BlueprintCfg):
            port: int = field(default=8080, gt=0, lt=65536)
            count: Annotated[int, field(ge=0)]

        BoundedCfg(port=8000, count=0)
        with self.assertRaises(InvalidBlueprintError):
            BoundedCfg(port=0, count=0)
        with self.assertRaises(InvalidBlueprintError):
            BoundedCfg(port=8000, count=-1)

    def test_numeric_bounds_are_inherited(self):
        class BaseCfg(BlueprintCfg):
            level: int = field(default=1, ge=1, le=10)

        class SubCfg(BaseCfg):
            pass

        with self.assertRaises(InvalidBlueprintError):
            SubCfg(level=11)
        self.assertEqual(SubCfg(level=5).level, 5)

    def test_multiple_inheritance_with_non_blueprint_mixin(self):
        class GreetingMixin:
            def greet(self):
                return f"Hello, {self.name}!"

        class GreeterCfg(GreetingMixin, BlueprintCfg):
            name: str

        cfg = GreeterCfg(name="Ada")
        self.assertEqual(cfg.greet(), "Hello, Ada!")

    def test_multiple_inheritance_with_non_blueprint_mixin_reverse_order(self):
        # Same as above, but with BlueprintCfg listed before the mixin -- exercises
        # __init_subclass__'s field-collection loop with a different __mro__ ordering.
        class GreetingMixin:
            def greet(self):
                return f"Hello, {self.name}!"

        class GreeterCfg(BlueprintCfg, GreetingMixin):
            name: str

        cfg = GreeterCfg(name="Ada")
        self.assertEqual(cfg.greet(), "Hello, Ada!")


class TestExampleScript(unittest.TestCase):
    """Covers: `examples/example.py` stays in sync with the library and runs cleanly."""

    def test_example_runs_without_errors(self):
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        example_path = repo_root / "examples" / "example.py"
        self.assertTrue(example_path.exists(), f"expected {example_path} to exist")

        result = subprocess.run(
            [sys.executable, str(example_path)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"examples/example.py exited with {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
