import copy
import enum
import pickle
import unittest
import warnings
from datetime import datetime, timedelta
from typing import Annotated, Literal

from blueprint import BlueprintCfg, ConfigDict, ConfigList, InvalidBlueprintError, field


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class ChildCfg(BlueprintCfg):
    name: str
    value: int = 10


class ParentCfg(BlueprintCfg):
    child: ChildCfg
    tag_list: list[str] = field(default_factory=list)


class CustomCheckCfg(BlueprintCfg):
    min_val: int
    max_val: int

    def check(self):
        if self.min_val > self.max_val:
            raise ValueError("min_val cannot be greater than max_val")


class TestBlueprintCfg(unittest.TestCase):
    def test_kwargs_only(self):
        # BlueprintCfg subclasses must be instantiated with keyword arguments only
        with self.assertRaises(TypeError):
            # positional arguments should fail
            ChildCfg("Alice", 20)  # type: ignore

        # Keyword arguments should work
        cfg = ChildCfg(name="Alice", value=20)
        self.assertEqual(cfg.name, "Alice")
        self.assertEqual(cfg.value, 20)

    def test_missing_required_args(self):
        # Missing 'name' which has no default should fail
        with self.assertRaises(TypeError):
            ChildCfg(value=5)

    def test_default_values_and_factories(self):
        # Test default value
        cfg1 = ChildCfg(name="Test")
        self.assertEqual(cfg1.value, 10)

        # Test default factory
        p1 = ParentCfg(child=ChildCfg(name="Child1"))
        p2 = ParentCfg(child=ChildCfg(name="Child2"))

        self.assertEqual(p1.tag_list, [])

        with p1.mutable_copy() as p1m:
            p1m.tag_list.append("tag1")

        # Verify lists are independent (no shared mutable state)
        self.assertEqual(p1.tag_list, [])  # p1 itself is never touched
        self.assertEqual(p1m.tag_list, ["tag1"])
        self.assertEqual(p2.tag_list, [])

    def test_strict_type_checking(self):
        # Int field should reject bool
        with self.assertRaises(TypeError):
            ChildCfg(name="Test", value=True)

        # Int field should reject str
        with self.assertRaises(TypeError):
            ChildCfg(name="Test", value="10")

        # Str field should reject int
        with self.assertRaises(TypeError):
            ChildCfg(name=123, value=10)

    def test_basic_types(self):
        class AllBasicCfg(BlueprintCfg):
            b: bytes
            s: str
            i: int
            bo: bool
            dt: datetime
            td: timedelta
            n: None

        now = datetime.now()
        delta = timedelta(days=1)
        cfg = AllBasicCfg(b=b"hello", s="world", i=42, bo=True, dt=now, td=delta, n=None)
        self.assertEqual(cfg.b, b"hello")
        self.assertEqual(cfg.s, "world")
        self.assertEqual(cfg.i, 42)
        self.assertEqual(cfg.bo, True)
        self.assertEqual(cfg.dt, now)
        self.assertEqual(cfg.td, delta)
        self.assertIsNone(cfg.n)

        # Assignment is prohibited outside a mutable_copy() block
        with self.assertRaises(AttributeError):
            cfg.bo = 1

        # ...and field types are still checked immediately, even inside one
        with cfg.mutable_copy() as y:
            with self.assertRaises(TypeError):
                y.bo = 1  # Int is not bool

    def test_union_types(self):
        class UnionCfg(BlueprintCfg):
            opt: int | None
            multi: int | str

        cfg = UnionCfg(opt=None, multi="hello")
        self.assertIsNone(cfg.opt)
        self.assertEqual(cfg.multi, "hello")

        with cfg.mutable_copy() as y:
            y.opt = 10
            self.assertEqual(y.opt, 10)

            y.multi = 42
            self.assertEqual(y.multi, 42)

            with self.assertRaises(TypeError):
                y.opt = "not-an-int-or-none"

        # original untouched
        self.assertIsNone(cfg.opt)
        self.assertEqual(cfg.multi, "hello")

    def test_literal_types(self):
        class LiteralCfg(BlueprintCfg):
            mode: Literal["read", "write"]
            num: Literal[1, 2]

        cfg = LiteralCfg(mode="read", num=1)
        self.assertEqual(cfg.mode, "read")
        self.assertEqual(cfg.num, 1)

        with cfg.mutable_copy() as y:
            y.mode = "write"
            self.assertEqual(y.mode, "write")

            with self.assertRaises(TypeError):
                y.mode = "delete"  # Not in Literal

            with self.assertRaises(TypeError):
                y.num = True  # True == 1, but type is bool, should be rejected

        self.assertEqual(cfg.mode, "read")  # original untouched

    def test_enum_types(self):
        class EnumCfg(BlueprintCfg):
            color: Color

        cfg = EnumCfg(color=Color.RED)
        self.assertEqual(cfg.color, Color.RED)

        with cfg.mutable_copy() as y:
            y.color = Color.BLUE
            self.assertEqual(y.color, Color.BLUE)

            with self.assertRaises(TypeError):
                y.color = 1  # Raw int should be rejected

        self.assertEqual(cfg.color, Color.RED)  # original untouched

    def test_nested_config_field_requires_instance_not_dict(self):
        # A BlueprintCfg-typed field must already be an instance of the right class -- dict
        # inputs are rejected, both at construction and at assignment.
        with self.assertRaises(TypeError):
            ParentCfg(child={"name": "SubChild", "value": 30}, tag_list=["a", "b"])

        p = ParentCfg(child=ChildCfg(name="SubChild", value=30), tag_list=["a", "b"])
        self.assertEqual(p.child.name, "SubChild")

        with p.mutable_copy() as y:
            with self.assertRaises(TypeError):
                y.child = {"name": "NewChild", "value": 40}
            y.child = ChildCfg(name="NewChild", value=40)
        self.assertIsInstance(y.child, ChildCfg)
        self.assertEqual(y.child.name, "NewChild")
        self.assertEqual(y.child.value, 40)
        self.assertEqual(p.child.name, "SubChild")  # original untouched

    def test_collection_types_and_mutation(self):
        class CollectionsCfg(BlueprintCfg):
            ints: list[int]
            str_int_dict: dict[str, int]
            pair: tuple[int, str]
            arbitrary_tuple: tuple[str, ...]

        cfg = CollectionsCfg(
            ints=[1, 2, 3],
            str_int_dict={"a": 1, "b": 2},
            pair=(10, "ten"),
            arbitrary_tuple=("x", "y", "z"),
        )

        with cfg.mutable_copy() as y:
            # Verify mutation of lists works, item types are always checked
            y.ints.append(4)
            self.assertEqual(y.ints, [1, 2, 3, 4])

            with self.assertRaises(TypeError):
                y.ints.append("not-an-int")

            with self.assertRaises(TypeError):
                y.ints[0] = "not-an-int"

            # Verify dict mutation
            y.str_int_dict["c"] = 3
            with self.assertRaises(TypeError):
                y.str_int_dict["d"] = "not-an-int"  # value type wrong
            with self.assertRaises(TypeError):
                y.str_int_dict[123] = 4  # key type wrong

            # Verify tuple values
            with self.assertRaises(TypeError):
                y.pair = (10, 20)  # Second element must be str

            # Arbitrary tuple checking
            y.arbitrary_tuple = ("a",)
            with self.assertRaises(TypeError):
                y.arbitrary_tuple = ("a", 2)

        # none of the above touched the original
        self.assertEqual(cfg.ints, [1, 2, 3])
        self.assertEqual(cfg.str_int_dict, {"a": 1, "b": 2})
        self.assertEqual(cfg.pair, (10, "ten"))
        self.assertEqual(cfg.arbitrary_tuple, ("x", "y", "z"))

        # outside the block, y itself is locked again
        with self.assertRaises(AttributeError):
            y.ints.append(5)

    def test_bare_collection_annotations_are_still_wrapped(self):
        # Bare `list`/`dict`/`tuple` (no subscript) used to bypass the ConfigList/ConfigDict
        # proxy entirely -- get_origin() returns None for them (unlike typing.List/Dict/Tuple
        # or a subscripted list[...]/dict[...]), so they fell through _convert_value()
        # unconverted and allowed direct mutation outside mutable_copy(). They should behave
        # just like their Any-typed parameterized form: list[Any], dict[Any, Any], tuple.
        class BareCollectionsCfg(BlueprintCfg):
            items: list
            mapping: dict
            pair: tuple

        cfg = BareCollectionsCfg(items=[1, "two", 3.0], mapping={"a": 1}, pair=(1, "x"))
        self.assertIsInstance(cfg.items, ConfigList)
        self.assertIsInstance(cfg.mapping, ConfigDict)

        with self.assertRaises(AttributeError):
            cfg.items.append(4)
        with self.assertRaises(AttributeError):
            cfg.mapping["b"] = 2

        with cfg.mutable_copy() as y:
            y.items.append(4)
            y.mapping["b"] = 2
        self.assertEqual(y.items, [1, "two", 3.0, 4])
        self.assertEqual(y.mapping, {"a": 1, "b": 2})
        # original untouched
        self.assertEqual(cfg.items, [1, "two", 3.0])
        self.assertEqual(cfg.mapping, {"a": 1})

    def test_nested_configs_in_collections(self):
        class ConfigListCfg(BlueprintCfg):
            children: list[ChildCfg]

        cfg = ConfigListCfg(children=[ChildCfg(name="C1"), ChildCfg(name="C2", value=50)])
        self.assertIsInstance(cfg.children[0], ChildCfg)
        self.assertEqual(cfg.children[0].name, "C1")
        self.assertEqual(cfg.children[1].value, 50)

        with cfg.mutable_copy() as y:
            # Appending cascades mutability onto the new item too -- the list itself is
            # y's own field
            y.children.append(ChildCfg(name="C3"))
            self.assertIsInstance(y.children[2], ChildCfg)
            self.assertEqual(y.children[2].name, "C3")

        self.assertEqual(len(cfg.children), 2)  # original untouched

        # Reaching into an existing list item and mutating it directly now works too --
        # mutability cascades to items inside mutable list/dict fields -- and a
        # resulting check() failure still only surfaces at the enclosing block's exit.
        class CustomParent(BlueprintCfg):
            children: list[ChildCfg]

            def check(self):
                if any(c.value < 0 for c in self.children):
                    raise ValueError("Child value cannot be negative")

        cp = CustomParent(children=[ChildCfg(name="C1", value=5)])
        with self.assertRaises(ValueError):
            with cp.mutable_copy() as y2:
                y2.children[0].value = -1  # direct nested mutation, now allowed
        self.assertEqual(cp.children[0].value, 5)  # original untouched

    def test_deletion_prevention(self):
        cfg = ChildCfg(name="Test")
        with self.assertRaises(AttributeError):
            del cfg.name

        # A declared field can never be deleted, even from a mutable_copy()
        with cfg.mutable_copy() as y:
            with self.assertRaises(AttributeError):
                del y.name

    def test_custom_check_hook(self):
        # Valid range
        cfg = CustomCheckCfg(min_val=10, max_val=20)
        self.assertEqual(cfg.min_val, 10)

        # Invalid range at init should fail
        with self.assertRaises(ValueError):
            CustomCheckCfg(min_val=30, max_val=20)

        # Assignment is prohibited outside mutable_copy(), regardless of validity
        with self.assertRaises(AttributeError):
            cfg.min_val = 25

        # Leaving an invalid range when a mutable_copy() block exits still raises --
        # check() runs exactly once there, on the final state
        with self.assertRaises(ValueError):
            with cfg.mutable_copy() as y:
                y.min_val = 25  # min_val (25) > max_val (20)
        self.assertEqual(cfg.min_val, 10)  # original untouched

    def test_dataclass_ergonomics(self):
        c1 = ChildCfg(name="A", value=1)
        c2 = ChildCfg(name="A", value=1)
        c3 = ChildCfg(name="B", value=1)

        self.assertEqual(c1, c2)
        self.assertNotEqual(c1, c3)
        self.assertEqual(repr(c1), "ChildCfg(name='A', value=1)")

    def test_inheritance_and_overriding(self):
        class Base(BlueprintCfg):
            x: int = 1
            y: str

        class Sub(Base):
            x = 2  # override default without type re-annotation
            y: str = "sub"  # override and provide default

        s = Sub()
        self.assertEqual(s.x, 2)
        self.assertEqual(s.y, "sub")

        # Assignment outside mutable_copy() is prohibited
        with self.assertRaises(AttributeError):
            s.x = "string"

        # Type checking for inherited/overridden fields still runs immediately inside one
        with s.mutable_copy() as y:
            with self.assertRaises(TypeError):
                y.x = "string"
            y.x = 3
        self.assertEqual(y.x, 3)
        self.assertEqual(s.x, 2)  # original untouched

    def test_multiple_inheritance_with_non_blueprint_mixin(self):
        class GreetingMixin:
            def greet(self):
                return f"Hello, {self.name}!"

        class GreeterCfg(GreetingMixin, BlueprintCfg):
            name: str

        cfg = GreeterCfg(name="Ada")
        self.assertEqual(cfg.name, "Ada")
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
        self.assertEqual(cfg.name, "Ada")
        self.assertEqual(cfg.greet(), "Hello, Ada!")

    def test_metadata_and_hover_descriptions(self):
        class AnnotatedMetaCfg(BlueprintCfg):
            port: Annotated[int, "The port number"] = 8080
            host: str = field(default="localhost", description="The host address")

        fields = AnnotatedMetaCfg.__blueprint_fields__
        self.assertEqual(fields["port"].description, "The port number")
        self.assertEqual(fields["host"].description, "The host address")

    def test_deepcopy_produces_independent_copy(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        d = copy.deepcopy(p)

        self.assertEqual(d, p)
        self.assertIsNot(d, p)
        self.assertIsInstance(d.child, ChildCfg)
        self.assertIsNot(d.child, p.child)
        self.assertIsNot(d.tag_list, p.tag_list)

        # Mutating the deep copy -- via mutable_copy(), same as any other instance --
        # never touches the original, proving the two share no nested state.
        with d.mutable_copy() as dm:
            dm.child.value = 99
            dm.tag_list.append("z")
        self.assertEqual(p.child.value, 1)
        self.assertEqual(p.tag_list, ["x", "y"])
        # d itself is untouched too -- dm is its own clone
        self.assertEqual(d.child.value, 1)
        self.assertEqual(d.tag_list, ["x", "y"])
        self.assertEqual(dm.child.value, 99)
        self.assertEqual(dm.tag_list, ["x", "y", "z"])

    def test_pickle_roundtrip_produces_independent_copy(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        p2 = pickle.loads(pickle.dumps(p))

        self.assertEqual(p2, p)
        self.assertIsNot(p2, p)
        self.assertIsInstance(p2.child, ChildCfg)
        self.assertIsNot(p2.child, p.child)
        self.assertIsNot(p2.tag_list, p.tag_list)

        # Round-tripped instance behaves like any other -- still immutable by default,
        # and mutating a mutable_copy() of it never touches the original.
        with self.assertRaises(AttributeError):
            p2.tag_list.append("z")
        with p2.mutable_copy() as p2m:
            p2m.tag_list.append("z")
        self.assertEqual(p.tag_list, ["x", "y"])
        self.assertEqual(p2.tag_list, ["x", "y"])
        self.assertEqual(p2m.tag_list, ["x", "y", "z"])


class TestEquality(unittest.TestCase):
    """Covers: `==` behavior for BlueprintCfg instances across deepcopy() and mutable_copy()."""

    def test_deepcopy_is_equal_to_original(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        d = copy.deepcopy(p)

        self.assertIsNot(d, p)
        self.assertEqual(d, p)

    def test_mutable_copy_without_changes_is_equal_to_original(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        with p.mutable_copy() as y:
            pass  # no changes made inside the block

        self.assertIsNot(y, p)
        self.assertEqual(y, p)

    def test_mutable_copy_with_changes_is_not_equal_to_original(self):
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])

        # A change to a plain field
        with p.mutable_copy() as y:
            y.tag_list.append("z")
        self.assertNotEqual(y, p)

        # A change to a nested BlueprintCfg field
        with p.mutable_copy() as y2:
            y2.child.value = 99
        self.assertNotEqual(y2, p)


class TestAssignmentProhibition(unittest.TestCase):
    """Covers: assignment (of any kind) is prohibited outside of a mutable_copy() block."""

    def test_field_assignment_outside_mutable_copy_raises(self):
        cfg = ChildCfg(name="A", value=1)
        with self.assertRaises(AttributeError):
            cfg.name = "B"
        with self.assertRaises(AttributeError):
            cfg.value = 2
        self.assertEqual(cfg.name, "A")
        self.assertEqual(cfg.value, 1)

    def test_list_field_mutation_outside_mutable_copy_raises(self):
        p = ParentCfg(child=ChildCfg(name="A"), tag_list=["x"])
        with self.assertRaises(AttributeError):
            p.tag_list.append("y")
        with self.assertRaises(AttributeError):
            p.tag_list[0] = "z"
        with self.assertRaises(AttributeError):
            del p.tag_list[0]
        self.assertEqual(p.tag_list, ["x"])

    def test_dict_field_mutation_outside_mutable_copy_raises(self):
        class RegistryCfg(BlueprintCfg):
            items: dict[str, int]

        reg = RegistryCfg(items={"a": 1})
        with self.assertRaises(AttributeError):
            reg.items["b"] = 2
        with self.assertRaises(AttributeError):
            del reg.items["a"]
        with self.assertRaises(AttributeError):
            reg.items.update({"c": 3})
        self.assertEqual(reg.items, {"a": 1})

    def test_nested_child_assignment_allowed_when_parent_mutable(self):
        p = ParentCfg(child=ChildCfg(name="A"), tag_list=["x"])
        with p.mutable_copy() as y:
            # mutability cascades: y.child is mutable too, for the life of the block
            y.child.name = "B"
            self.assertEqual(y.child.name, "B")
        self.assertEqual(p.child.name, "A")  # original untouched
        # after the block, the (now-detached) child is locked again too
        with self.assertRaises(AttributeError):
            y.child.name = "C"

    def test_construction_itself_is_unaffected(self):
        # Construction (via __init__) doesn't go through __setattr__ -- it always works.
        cfg = ChildCfg(name="A", value=1)
        self.assertEqual(cfg.name, "A")


class TestMutableCopy(unittest.TestCase):
    def test_basic_copy_is_independent(self):
        x = ChildCfg(name="A", value=1)
        with x.mutable_copy() as y:
            y.name = "B"
            y.value = 2
        self.assertEqual(x.name, "A")
        self.assertEqual(x.value, 1)
        self.assertEqual(y.name, "B")
        self.assertEqual(y.value, 2)
        self.assertIsNot(x, y)

    def test_no_mutation_of_original_even_on_error(self):
        x = ChildCfg(name="A", value=1)
        with self.assertRaises(TypeError):
            with x.mutable_copy() as y:
                y.value = "not-an-int"
        self.assertEqual(x.name, "A")
        self.assertEqual(x.value, 1)

    def test_invalid_type_raises_immediately_not_deferred(self):
        x = ChildCfg(name="A", value=1)
        with x.mutable_copy() as y:
            with self.assertRaises(TypeError):
                y.value = "nope"
            # the failed assignment didn't happen, and y is still usable/mutable
            self.assertEqual(y.value, 1)
            y.value = 2
        self.assertEqual(y.value, 2)
        self.assertEqual(x.value, 1)  # original untouched throughout

    def test_temporarily_inconsistent_cross_field_state_allowed(self):
        # min_val > max_val would normally be rejected immediately via check(),
        # but should be allowed to pass through an intermediate state inside the block,
        # since check() only runs once, when the block exits.
        x = CustomCheckCfg(min_val=1, max_val=10)
        with x.mutable_copy() as y:
            y.min_val = 100  # temporarily invalid: 100 > 10
            y.max_val = 200  # now valid again: 100 <= 200
        self.assertEqual(y.min_val, 100)
        self.assertEqual(y.max_val, 200)
        # original untouched
        self.assertEqual(x.min_val, 1)
        self.assertEqual(x.max_val, 10)

    def test_final_invalid_cross_field_state_raises(self):
        x = CustomCheckCfg(min_val=1, max_val=10)
        with self.assertRaises(ValueError):
            with x.mutable_copy() as y:
                y.min_val = 100  # left invalid (100 > 10) at exit

    def test_assignment_outside_mutable_copy_raises_attribute_error(self):
        x = CustomCheckCfg(min_val=1, max_val=10)
        with self.assertRaises(AttributeError):
            x.min_val = 100  # assignment is prohibited outside mutable_copy()
        self.assertEqual(x.min_val, 1)  # unchanged

    def test_is_blueprint_mutable_invisible_and_false_by_default(self):
        x = ChildCfg(name="A", value=1)
        self.assertNotIn("_is_blueprint_mutable", x.__blueprint_fields__)
        self.assertFalse(x.__dict__.get("_is_blueprint_mutable", False))
        self.assertFalse(x._is_blueprint_mutable)
        # not exposed via repr/eq machinery
        self.assertNotIn("_is_blueprint_mutable", repr(x))

    def test_mutable_flag_false_after_exit(self):
        x = ChildCfg(name="A", value=1)
        with x.mutable_copy() as y:
            pass
        self.assertFalse(y._is_blueprint_mutable)
        # y behaves like any other non-mutable instance again: assignment is rejected
        with self.assertRaises(AttributeError):
            y.value = 2

    def test_nested_config_direct_mutation_works_via_cascade(self):
        x = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with x.mutable_copy() as y:
            # mutability cascades to y.child -- no separate mutable_copy() needed
            y.child.name = "mutated"
            y.child.value = 99
        self.assertEqual(y.child.name, "mutated")
        self.assertEqual(y.child.value, 99)
        self.assertEqual(x.child.name, "A")
        self.assertEqual(x.child.value, 1)

    def test_nested_config_bubble_up_validation_after_exit(self):
        class DependentParentCfg(BlueprintCfg):
            min_limit: int
            child: ChildCfg

            def check(self):
                if self.child.value < self.min_limit:
                    raise ValueError("Child value cannot be less than parent min_limit")

        dp = DependentParentCfg(min_limit=15, child=ChildCfg(name="C", value=20))
        with self.assertRaises(ValueError):
            with dp.mutable_copy() as y:
                # cascaded direct edit; check() is deferred to y's own exit
                y.child.value = 5
        # original untouched
        self.assertEqual(dp.child.value, 20)

    def test_list_field_type_checked_always_and_locked_after_exit(self):
        x = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with x.mutable_copy() as y:
            y.tag_list = ["a", "b", "c"]
            with self.assertRaises(TypeError):
                y.tag_list.append(123)  # field-level checks always run, even mid-edit
            self.assertEqual(y.tag_list, ["a", "b", "c"])  # unaffected by the failure
        self.assertEqual(y.tag_list, ["a", "b", "c"])
        # after exit, y itself is locked again -- even a *valid* mutation is rejected
        with self.assertRaises(AttributeError):
            y.tag_list.append("d")

    def test_lists_are_independent_between_original_and_copy(self):
        x = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with x.mutable_copy() as y:
            y.tag_list.append("y")
        self.assertEqual(x.tag_list, ["x"])
        self.assertEqual(y.tag_list, ["x", "y"])

    def test_nested_list_of_configs_direct_mutation_and_append(self):
        class ConfigListCfg(BlueprintCfg):
            children: list[ChildCfg]

        cfg = ConfigListCfg(children=[ChildCfg(name="C1", value=1), ChildCfg(name="C2", value=2)])
        with cfg.mutable_copy() as y:
            y.children[0].value = 999  # direct mutation of a list item, cascaded
            # newly appended item too...
            # ...is itself mutable within the same block
            y.children.append(ChildCfg(name="C3", value=3))
            y.children[2].value = 30
        self.assertEqual(y.children[0].value, 999)
        self.assertEqual(len(y.children), 3)
        self.assertEqual(y.children[2].name, "C3")
        self.assertEqual(y.children[2].value, 30)
        # original untouched
        self.assertEqual(cfg.children[0].value, 1)
        self.assertEqual(len(cfg.children), 2)

    def test_dict_field_of_nested_configs(self):
        class RegistryCfg(BlueprintCfg):
            items: dict[str, ChildCfg]

        reg = RegistryCfg(items={"a": ChildCfg(name="A", value=1)})
        with reg.mutable_copy() as y:
            y.items["a"].value = 42  # direct mutation of a dict value, cascaded

            y.items["b"] = ChildCfg(name="B", value=2)
            # Like ConfigList.append(), assigning into a ConfigDict cascades mutability
            # onto the value, so a freshly-attached one is directly editable too.
            y.items["b"].value = 20

            # Assigning a reference to an EXISTING, external instance is deep-copied on
            # the way in, so it's independently mutable here without ever touching the
            # original object it was copied from.
            y.items["c"] = reg.items["a"]
            y.items["c"].value = 43

        self.assertEqual(y.items["a"].value, 42)
        self.assertIsInstance(y.items["b"], ChildCfg)
        self.assertEqual(y.items["b"].name, "B")
        self.assertEqual(y.items["b"].value, 20)
        self.assertIsInstance(y.items["c"], ChildCfg)
        self.assertEqual(y.items["c"].value, 43)
        # original untouched -- neither by the "a" mutation nor by the "c" alias
        self.assertEqual(reg.items["a"].value, 1)
        self.assertEqual(len(reg.items), 1)

    def test_deep_nested_cascade_and_deferred_check(self):
        class GrandchildCfg(BlueprintCfg):
            score: int

            def check(self):
                if self.score < 0:
                    raise ValueError("score cannot be negative")

        class MiddleCfg(BlueprintCfg):
            grandchild: GrandchildCfg

        class TopCfg(BlueprintCfg):
            middle: MiddleCfg

        top = TopCfg(middle=MiddleCfg(grandchild=GrandchildCfg(score=5)))

        # Mutability cascades through every level: no separate mutable_copy() needed
        # for the grandchild, even though it's three levels deep.
        with top.mutable_copy() as y:
            y.middle.grandchild.score = -1  # temporarily invalid; check() is deferred
            y.middle.grandchild.score = 7  # fixed before the block exits
        self.assertEqual(y.middle.grandchild.score, 7)
        self.assertEqual(top.middle.grandchild.score, 5)  # original untouched

        # Leaving an invalid final state at a block's own exit still raises there, and
        # nodes finalize bottom-up so the deepest node's own check() runs first
        with self.assertRaises(ValueError):
            with top.mutable_copy() as y2:
                y2.middle.grandchild.score = -5  # left invalid at exit
        self.assertEqual(top.middle.grandchild.score, 5)  # original untouched

    def test_observable_list_has_own_is_blueprint_mutable_flag(self):
        p = ParentCfg(child=ChildCfg(name="A"), tag_list=["x"])
        self.assertFalse(p.tag_list._is_blueprint_mutable)
        with p.mutable_copy() as y:
            self.assertTrue(y.tag_list._is_blueprint_mutable)
        self.assertFalse(y.tag_list._is_blueprint_mutable)
        with self.assertRaises(AttributeError):
            y.tag_list.append("y")

    def test_observable_dict_has_own_is_blueprint_mutable_flag(self):
        class RegistryCfg(BlueprintCfg):
            items: dict[str, int]

        reg = RegistryCfg(items={"a": 1})
        self.assertFalse(reg.items._is_blueprint_mutable)
        with reg.mutable_copy() as y:
            self.assertTrue(y.items._is_blueprint_mutable)
            y.items["b"] = 2
        self.assertFalse(y.items._is_blueprint_mutable)
        with self.assertRaises(AttributeError):
            y.items["c"] = 3

    def test_freshly_attached_nested_config_is_blueprint_mutable_for_rest_of_block(
        self,
    ):
        x = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x"])
        with x.mutable_copy() as y:
            y.child = ChildCfg(name="B", value=2)  # freshly attached instance
            y.child.name = "C"  # immediately editable within the same block
        self.assertEqual(y.child.name, "C")
        self.assertEqual(x.child.name, "A")  # original untouched
        with self.assertRaises(AttributeError):
            y.child.name = "D"  # locked again after the block

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

    def test_can_still_read_fields_normally_inside_block(self):
        x = ChildCfg(name="A", value=1)
        with x.mutable_copy() as y:
            self.assertEqual(y.name, "A")
            y.name = "B"
            self.assertEqual(y.name, "B")


class TestImmutableByDefault(unittest.TestCase):
    """Covers: every container type the package ships -- ConfigList, ConfigDict,
    and BlueprintCfg -- is immutable unless explicitly unlocked via mutable_copy()."""

    def test_class_level_flag_defaults_to_immutable(self):
        # The flag that gates every mutation defaults to False on the class itself,
        # so any freshly created instance starts out immutable without extra effort.
        for cls in (ConfigList, ConfigDict, BlueprintCfg):
            with self.subTest(cls=cls.__name__):
                self.assertFalse(cls._is_blueprint_mutable)

    def test_fresh_instances_are_immutable(self):
        instances = {
            "ConfigList": ConfigList([1, 2], int),
            "ConfigDict": ConfigDict({"a": 1}, str, int),
            "BlueprintCfg": ChildCfg(name="A", value=1),
        }
        for label, obj in instances.items():
            with self.subTest(cls=label):
                self.assertFalse(obj._is_blueprint_mutable)

    def test_mutation_raises_by_default(self):
        lst = ConfigList([1, 2], int)
        with self.assertRaises(AttributeError):
            lst.append(3)
        with self.assertRaises(AttributeError):
            lst[0] = 9
        with self.assertRaises(AttributeError):
            del lst[0]
        self.assertEqual(lst, [1, 2])  # untouched

        dct = ConfigDict({"a": 1}, str, int)
        with self.assertRaises(AttributeError):
            dct["b"] = 2
        with self.assertRaises(AttributeError):
            del dct["a"]
        with self.assertRaises(AttributeError):
            dct.update({"c": 3})
        self.assertEqual(dct, {"a": 1})  # untouched

        cfg = ChildCfg(name="A", value=1)
        with self.assertRaises(AttributeError):
            cfg.value = 2
        self.assertEqual(cfg.value, 1)  # untouched

    def test_nested_containers_of_a_fresh_cfg_are_also_immutable(self):
        # Immutability isn't just top-level: list/dict/cfg fields nested inside a
        # freshly constructed BlueprintCfg all start out locked too.
        p = ParentCfg(child=ChildCfg(name="A", value=1), tag_list=["x", "y"])
        self.assertFalse(p._is_blueprint_mutable)
        self.assertFalse(p.child._is_blueprint_mutable)
        self.assertFalse(p.tag_list._is_blueprint_mutable)


class TestInvalidBlueprintError(unittest.TestCase):
    """Covers: InvalidBlueprintError, raised by _validate_self() for field type mismatches."""

    def test_is_a_type_error_subclass(self):
        # Existing `except TypeError` / assertRaises(TypeError) callers keep working
        # unchanged since InvalidBlueprintError subclasses TypeError.
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
        self.assertIn("'a'", ctx.exception.errors[0])
        self.assertIn("'b'", ctx.exception.errors[1])


class TestMutableCopyExceptionHandling(unittest.TestCase):
    """Covers: an unrelated exception raised inside a mutable_copy() block while the
    clone is left in a temporarily-invalid cross-field state."""

    def test_original_exception_propagates_with_original_type(self):
        cfg = CustomCheckCfg(min_val=10, max_val=20)

        with self.assertWarns(UserWarning):
            with self.assertRaises(RuntimeError):
                with cfg.mutable_copy() as y:
                    y.min_val = 25  # leaves y invalid: min_val (25) > max_val (20)
                    raise RuntimeError("boom")

        self.assertEqual(cfg.min_val, 10)  # original untouched

    def test_no_warning_when_block_completes_normally(self):
        # No earlier exception here -- the ValueError from check() at block-exit is
        # the real error, so it should be raised directly, without a warning first.
        cfg = CustomCheckCfg(min_val=10, max_val=20)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning here fails the test
            with self.assertRaises(ValueError):
                with cfg.mutable_copy() as y:
                    y.min_val = 25  # leaves y invalid, block itself doesn't raise
        self.assertEqual(cfg.min_val, 10)


if __name__ == "__main__":
    unittest.main()
