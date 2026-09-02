"""Tests for `BlueprintCfg.__get_pydantic_core_schema__` -- lets a BlueprintCfg
subclass be used as a field type in a pydantic BaseModel / pydantic dataclass.
Skipped entirely if pydantic isn't installed (it's a dev-only dependency, not a
runtime dependency of blueprint).
"""

# This file deliberately passes wrong types to pydantic models to exercise runtime
# validation -- pytest covers its correctness, not a static type checker.
# mypy: ignore-errors
# pyrefly: ignore-errors

import unittest
from typing import Annotated, Literal

import pydantic

from blueprint import BlueprintCfg, field


class RetryCfg(BlueprintCfg):
    max_attempts: int = 3


class ServerCfg(BlueprintCfg):
    host: str = "localhost"
    port: int = 8080
    retry: RetryCfg = field(default_factory=RetryCfg)


class TestPydanticIntegration(unittest.TestCase):
    def test_field_in_basemodel_accepts_instance(self):
        class Settings(pydantic.BaseModel):
            model_config = pydantic.ConfigDict(arbitrary_types_allowed=False)
            server: ServerCfg

        cfg = ServerCfg(host="0.0.0.0", port=9000)
        settings = Settings(server=cfg)
        self.assertIs(settings.server, cfg)  # passed through, not rebuilt

    def test_field_in_basemodel_rejects_dict(self):
        # blueprint deliberately has no from_dict() (see as_dict() docs) -- a plain dict
        # is not accepted in place of an actual ServerCfg(...) instance.
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        with self.assertRaises(pydantic.ValidationError):
            Settings(server={"host": "0.0.0.0", "port": 9000})

    def test_field_in_basemodel_rejects_wrong_type(self):
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        with self.assertRaises(pydantic.ValidationError):
            Settings(server=RetryCfg())  # wrong BlueprintCfg subclass

    def test_model_dump_python_keeps_instance(self):
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        cfg = ServerCfg(host="0.0.0.0", port=9000)
        settings = Settings(server=cfg)
        dumped = settings.model_dump(mode="python")
        self.assertIs(dumped["server"], cfg)

    def test_model_dump_json_uses_as_dict(self):
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        cfg = ServerCfg(host="0.0.0.0", port=9000)
        settings = Settings(server=cfg)
        dumped = settings.model_dump(mode="json")
        self.assertEqual(dumped["server"], cfg.as_dict())
        self.assertEqual(
            dumped["server"],
            {"host": "0.0.0.0", "port": 9000, "retry": {"max_attempts": 3}},
        )

        # model_dump_json() round-trips through the same serializer
        import json

        self.assertEqual(json.loads(settings.model_dump_json())["server"], cfg.as_dict())

    def test_field_in_pydantic_dataclass(self):
        @pydantic.dataclasses.dataclass
        class Settings:
            server: ServerCfg

        cfg = ServerCfg(host="0.0.0.0", port=9000)
        settings = Settings(server=cfg)
        self.assertIs(settings.server, cfg)

        with self.assertRaises(pydantic.ValidationError):
            Settings(server={"host": "0.0.0.0"})

    def test_json_schema_generation_does_not_crash(self):
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        # We don't assert on the exact shape (there's no dict-based input schema, on
        # purpose) -- just that generating it doesn't raise.
        schema = Settings.model_json_schema()
        self.assertIn("properties", schema)

    def test_nested_blueprint_field_still_frozen(self):
        # Using BlueprintCfg inside a pydantic model doesn't loosen blueprint's own
        # immutability guarantees.
        class Settings(pydantic.BaseModel):
            server: ServerCfg

        settings = Settings(server=ServerCfg())
        with self.assertRaises(AttributeError):
            settings.server.port = 1234

    def test_default_factory_recognized_behind_annotated_type_alias(self):
        # regression test: annotated Union wasn't handled properly.
        class TransportA(BlueprintCfg):
            kind: Literal["a"] = "a"


        class TransportB(BlueprintCfg):
            kind: Literal["b"] = "b"

        TransportCfg = Annotated[TransportA | TransportB, "any comment"]

        class ConnectionCfg(BlueprintCfg):
            transport: TransportCfg = field(default_factory=TransportA)


        cfg = ConnectionCfg()  # must not raise "missing required keyword-only argument"
        self.assertIsInstance(cfg.transport, TransportA)

        # Still overridable like any other field
        cfg2 = ConnectionCfg(transport=TransportB())
        self.assertIsInstance(cfg2.transport, TransportB)

        # Each instance gets its own factory-produced value (no shared mutable default)
        self.assertIsNot(cfg.transport, ConnectionCfg().transport)

        # Also works when ConnectionCfg itself is used from within a pydantic model
        class Settings(pydantic.BaseModel):
            conn: ConnectionCfg = pydantic.Field(default_factory=ConnectionCfg)

        settings = Settings()
        self.assertIsInstance(settings.conn.transport, TransportA)

    def test_subclass_instance_accepted(self):
        class AdminServerCfg(ServerCfg):
            is_admin: bool = True

        class Settings(pydantic.BaseModel):
            server: ServerCfg

        admin_cfg = AdminServerCfg()
        settings = Settings(server=admin_cfg)
        self.assertIs(settings.server, admin_cfg)


if __name__ == "__main__":
    unittest.main()
