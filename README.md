# blueprint

Typed, immutable-by-default config objects for Python.

`blueprint` gives you dataclass-like config classes with real type checking
and a deliberate mutation model: instances are frozen after construction,
and the only way to change one is through an explicit `mutable_copy()`
block, which hands you an independent, deep, mutable copy and re-validates
it when the block exits.

## Install

This project isn't published to PyPI yet. Install it from a local checkout:

```bash
pip install -e ".[dev]"
```

Requires Python 3.12 or newer.

## Quick example

```python
from blueprint import BlueprintCfg, field

class ServerCfg(BlueprintCfg):
    host: str = "localhost"
    port: int = 8080
    tags: list[str] = field(default_factory=list)

    def check(self):
        if not (0 < self.port < 65536):
            raise ValueError("port out of range")

cfg = ServerCfg(host="0.0.0.0", port=8000)

# Instances are frozen -- this raises AttributeError:
# cfg.port = 9000

# Changes go through mutable_copy(), which yields a deep, independent copy
# and validates it (including check()) when the block exits:
with cfg.mutable_copy() as new_cfg:
    new_cfg.port = 9000
    new_cfg.tags.append("staging")

assert cfg.port == 8000        # original untouched
assert new_cfg.port == 9000
```

Fields support plain types, `Optional`/`Union`, `Literal`, `Enum`, nested
`BlueprintCfg` classes, and `list[T]` / `dict[K, V]` / `tuple[...]`
collections, all recursively type-checked on construction and on every
mutation. `blueprint.format()` pretty-prints a config (and its nested
configs/collections), wrapping long lines for readability.

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff format .
ruff check .
```

## Known limitations

One thing worth knowing before relying on this in production:

- If a `mutable_copy()` block raises an unrelated exception while the
  config is in a temporarily-invalid cross-field state, the `check()`
  failure raised while unwinding the block will replace that original
  exception (it's kept as `__context__`, but the exception type you see
  changes).

See the inline comments in `src/blueprint/__init__.py` for more detail.

