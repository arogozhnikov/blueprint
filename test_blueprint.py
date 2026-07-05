import unittest
import enum
from datetime import datetime, timedelta
from typing import Union, Literal, Annotated, List, Dict, Tuple
from blueprint import BlueprintCfg, field, MISSING


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
            ChildCfg("Alice", 20)  # positional arguments should fail

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
        p1.tag_list.append("tag1")
        
        # Verify lists are independent (no shared mutable state)
        self.assertEqual(p1.tag_list, ["tag1"])
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
        cfg = AllBasicCfg(
            b=b"hello",
            s="world",
            i=42,
            bo=True,
            dt=now,
            td=delta,
            n=None
        )
        self.assertEqual(cfg.b, b"hello")
        self.assertEqual(cfg.s, "world")
        self.assertEqual(cfg.i, 42)
        self.assertEqual(cfg.bo, True)
        self.assertEqual(cfg.dt, now)
        self.assertEqual(cfg.td, delta)
        self.assertIsNone(cfg.n)

        # Test assignment validation
        with self.assertRaises(TypeError):
            cfg.bo = 1  # Int is not bool

    def test_union_types(self):
        class UnionCfg(BlueprintCfg):
            opt: Union[int, None]
            multi: int | str

        cfg = UnionCfg(opt=None, multi="hello")
        self.assertIsNone(cfg.opt)
        self.assertEqual(cfg.multi, "hello")

        cfg.opt = 10
        self.assertEqual(cfg.opt, 10)

        cfg.multi = 42
        self.assertEqual(cfg.multi, 42)

        with self.assertRaises(TypeError):
            cfg.opt = "not-an-int-or-none"

    def test_literal_types(self):
        class LiteralCfg(BlueprintCfg):
            mode: Literal["read", "write"]
            num: Literal[1, 2]

        cfg = LiteralCfg(mode="read", num=1)
        self.assertEqual(cfg.mode, "read")
        self.assertEqual(cfg.num, 1)

        cfg.mode = "write"
        self.assertEqual(cfg.mode, "write")

        with self.assertRaises(TypeError):
            cfg.mode = "delete"  # Not in Literal

        with self.assertRaises(TypeError):
            cfg.num = True  # True == 1, but type is bool, should be rejected

    def test_enum_types(self):
        class EnumCfg(BlueprintCfg):
            color: Color

        cfg = EnumCfg(color=Color.RED)
        self.assertEqual(cfg.color, Color.RED)

        cfg.color = Color.BLUE
        self.assertEqual(cfg.color, Color.BLUE)

        with self.assertRaises(TypeError):
            cfg.color = 1  # Raw int should be rejected

    def test_nested_configs_and_dict_conversion(self):
        # Initializing with a dict for nested config should convert it to instance
        p = ParentCfg(
            child={"name": "SubChild", "value": 30},
            tag_list=["a", "b"]
        )
        self.assertIsInstance(p.child, ChildCfg)
        self.assertEqual(p.child.name, "SubChild")
        self.assertEqual(p.child.value, 30)

        # Assigning a dict to nested field should convert it
        p.child = {"name": "NewChild", "value": 40}
        self.assertIsInstance(p.child, ChildCfg)
        self.assertEqual(p.child.name, "NewChild")
        self.assertEqual(p.child.value, 40)

        # Modifying a field of the child should propagate change notification and validate
        # Let's test with a cross-validation parent
        class DependentParentCfg(BlueprintCfg):
            min_limit: int
            child: ChildCfg

            def check(self):
                if self.child.value < self.min_limit:
                    raise ValueError("Child value cannot be less than parent min_limit")

        dp = DependentParentCfg(min_limit=15, child={"name": "C", "value": 20})
        # This is valid (20 >= 15)

        with self.assertRaises(ValueError):
            dp.child.value = 10  # This should trigger dp._validate_all() and fail cross check

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
            arbitrary_tuple=("x", "y", "z")
        )

        # Verify mutation of lists triggers validation
        cfg.ints.append(4)
        self.assertEqual(cfg.ints, [1, 2, 3, 4])

        with self.assertRaises(TypeError):
            cfg.ints.append("not-an-int")

        with self.assertRaises(TypeError):
            cfg.ints[0] = "not-an-int"

        # Verify dict mutation
        cfg.str_int_dict["c"] = 3
        with self.assertRaises(TypeError):
            cfg.str_int_dict["d"] = "not-an-int"  # value type wrong
        with self.assertRaises(TypeError):
            cfg.str_int_dict[123] = 4  # key type wrong

        # Verify tuple values
        with self.assertRaises(TypeError):
            cfg.pair = (10, 20)  # Second element must be str

        # Arbitrary tuple checking
        cfg.arbitrary_tuple = ("a",)
        with self.assertRaises(TypeError):
            cfg.arbitrary_tuple = ("a", 2)

    def test_nested_configs_in_collections(self):
        class ConfigListCfg(BlueprintCfg):
            children: list[ChildCfg]

        # Init with list of dicts should convert them
        cfg = ConfigListCfg(children=[{"name": "C1"}, {"name": "C2", "value": 50}])
        self.assertIsInstance(cfg.children[0], ChildCfg)
        self.assertEqual(cfg.children[0].name, "C1")
        self.assertEqual(cfg.children[1].value, 50)

        # Appending dict should convert it
        cfg.children.append({"name": "C3"})
        self.assertIsInstance(cfg.children[2], ChildCfg)
        self.assertEqual(cfg.children[2].name, "C3")

        # Modifying a child in the list should trigger parent validation
        class CustomParent(BlueprintCfg):
            children: list[ChildCfg]
            def check(self):
                if any(c.value < 0 for c in self.children):
                    raise ValueError("Child value cannot be negative")

        cp = CustomParent(children=[{"name": "C1", "value": 5}])
        with self.assertRaises(ValueError):
            cp.children[0].value = -1

    def test_deletion_prevention(self):
        cfg = ChildCfg(name="Test")
        with self.assertRaises(AttributeError):
            del cfg.name

    def test_custom_check_hook(self):
        # Valid range
        cfg = CustomCheckCfg(min_val=10, max_val=20)
        self.assertEqual(cfg.min_val, 10)

        # Invalid range at init should fail
        with self.assertRaises(ValueError):
            CustomCheckCfg(min_val=30, max_val=20)

        # Invalid range at update should fail
        with self.assertRaises(ValueError):
            cfg.min_val = 25  # min_val (25) > max_val (20)

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
            x = 2            # override default without type re-annotation
            y: str = "sub"   # override and provide default

        s = Sub()
        self.assertEqual(s.x, 2)
        self.assertEqual(s.y, "sub")

        # Verify type check still works for inherited and overridden x
        with self.assertRaises(TypeError):
            s.x = "string"

    def test_metadata_and_hover_descriptions(self):
        class AnnotatedMetaCfg(BlueprintCfg):
            port: Annotated[int, "The port number"] = 8080
            host: str = field(default="localhost", description="The host address")

        fields = AnnotatedMetaCfg.__blueprint_fields__
        self.assertEqual(fields["port"].description, "The port number")
        self.assertEqual(fields["host"].description, "The host address")


if __name__ == "__main__":
    unittest.main()
