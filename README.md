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

## Nested configs and collections

Mutability cascades: unlocking a config with `mutable_copy()` also unlocks
every nested `BlueprintCfg`, list, and dict field reachable from it, so you
don't need a separate `mutable_copy()` per nested object.

```python
import blueprint
from blueprint import BlueprintCfg, field

class RetryCfg(BlueprintCfg):
    max_attempts: int = 3
    backoff: float = 1.5

class ServiceCfg(BlueprintCfg):
    name: str
    retry: RetryCfg = field(default_factory=RetryCfg)
    endpoints: list[str] = field(default_factory=list)

svc = ServiceCfg(name="api", endpoints=["https://a.example"])

with svc.mutable_copy() as svc2:
    svc2.retry.max_attempts = 5        # nested config, no extra mutable_copy() needed
    svc2.endpoints.append("https://b.example")

print(blueprint.format(svc2))
# ServiceCfg(
#   name='api',
#   retry=RetryCfg(max_attempts=5, backoff=1.5),
#   endpoints=['https://a.example', 'https://b.example'],
# )
```

## Errors

Field type-check failures raise `InvalidBlueprintError` (a `TypeError`
subclass, so existing `except TypeError` handling keeps working). Its
`errors` attribute lists every field that failed, not just the first one:

```python
from blueprint import InvalidBlueprintError

try:
    ServerCfg(host=123, port="oops")
except InvalidBlueprintError as e:
    print(e.errors)  # one message per bad field
```

`check()` failures (like the `port out of range` example above) are raised
as whatever exception your `check()` method raises -- they aren't wrapped.

If a `mutable_copy()` block raises an unrelated exception while the config
is left in a temporarily-invalid cross-field state, that state is no longer
re-raised as a replacement exception: a `UserWarning` is emitted instead,
and the original exception keeps propagating with its original type. See
the inline comments around `mutable_copy()` in `src/blueprint/_blueprint.py`
for more detail.


## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff format .
ruff check .
```
