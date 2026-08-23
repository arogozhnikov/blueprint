"""
Runnable tour of blueprint's features.

"""

import blueprint
from blueprint import BlueprintCfg, InvalidBlueprintError, field

print("""
# ---------------------------------------------------------------------------
# 1. Defining a config: type-annotated fields, defaults, a custom check()
# ---------------------------------------------------------------------------
""")


class RetryCfg(BlueprintCfg):
    max_attempts: int = 3
    backoff: float = 1.5


class ServerCfg(BlueprintCfg):
    host: str = "localhost"
    port: int = 8080
    tags: list[str] = field(default_factory=list)
    retry: RetryCfg = field(default_factory=RetryCfg)
    as_admin: bool = False

    def check(self):
        # check() runs after every construction and after every mutable_copy() block --
        # use it for validation that spans more than one field.
        if not (0 < self.port < 65536):
            raise ValueError("port out of range")
        if not self.as_admin and self.port < 1000:
            raise ValueError("you need admin privileges")


cfg = ServerCfg(host="0.0.0.0", port=8000, tags=["prod"])
print(blueprint.format(cfg))
assert cfg.host == "0.0.0.0"
assert cfg.retry.max_attempts == 3  # default_factory ran automatically


print("""
# ---------------------------------------------------------------------------
# 2. Instances are frozen; mutable_copy() is the only way to change one
# ---------------------------------------------------------------------------
""")

try:
    cfg.port = 9000
    raise AssertionError("should not get here")
except AttributeError as e:
    print(f"direct assignment blocked: {e}")

with cfg.mutable_copy() as cfg2:
    cfg2.port = 9000
    cfg2.tags.append("staging")  # nested list fields are mutable too, inside the block
    cfg2.retry.max_attempts = 5  # ...and so are nested configs, no separate mutable_copy()

print(f"cfg  (untouched): {blueprint.format(cfg)}")
print(f"cfg2 (new copy):  {blueprint.format(cfg2)}")
assert cfg.port == 8000 and cfg.tags == ["prod"]  # original never touched
assert cfg2.port == 9000 and cfg2.tags == ["prod", "staging"]


print("""
# ---------------------------------------------------------------------------
# 3. Everything assigned into a config is deep-copied on the way in
# ---------------------------------------------------------------------------
""")


source_tags = ["a", "b"]
tagged = ServerCfg(tags=source_tags)
source_tags.append("c")  # mutate the list we passed in, *after* constructing `tagged`
print(f"source_tags now: {source_tags}")
print(f"tagged.tags:     {tagged.tags}")
assert tagged.tags == ["a", "b"]  # tagged's copy never saw the later append

# The same applies to nested BlueprintCfg instances and to values assigned mid mutable_copy():
retry = RetryCfg(max_attempts=1)
with ServerCfg(retry=retry).mutable_copy() as cfg3:
    cfg3.retry.max_attempts = 99  # mutating the copy...
assert retry.max_attempts == 1  # ...never touches the object `retry` still points at


print("""
# ---------------------------------------------------------------------------
# 4. Type-check failures raise InvalidBlueprintError with a readable message
# ---------------------------------------------------------------------------
""")


try:
    ServerCfg(host=123, port="oops")
    raise AssertionError("should not get here")
except InvalidBlueprintError as e:
    print(f"caught InvalidBlueprintError: {e}")
    print(f"e.errors: {e.errors}")
    assert len(e.errors) == 2  # one message per invalid field

# check() failures are raised as whatever exception check() itself raises, unwrapped:
try:
    ServerCfg(port=99999)
    raise AssertionError("should not get here")
except ValueError as e:
    print(f"caught ValueError from check(): {e}")


print("""
# ---------------------------------------------------------------------------
# 5. blueprint.dangerously_all_mutable() -- a global, unscoped escape hatch
# ---------------------------------------------------------------------------
""")


quick = ServerCfg(host="127.0.0.1")
with blueprint.dangerously_all_mutable():
    # Direct assignment on the ORIGINAL object, no copy involved -- useful for incremental
    # construction or quick experiments, but every other reference to `quick` sees this too.
    quick.port = 8081
assert quick.port == 8081
try:
    quick.port = 8082
    raise AssertionError("should not get here")
except AttributeError:
    print("outside the block, quick is frozen again, as normal")


print("""
# ---------------------------------------------------------------------------------
# 6. blueprint.debug_prohibit_mutability() -- assert a region never mutates configs
# ---------------------------------------------------------------------------------
""")


with blueprint.debug_prohibit_mutability():
    try:
        with cfg.mutable_copy():
            pass
        raise AssertionError("should not get here")
    except RuntimeError as e:
        print(f"mutable_copy() blocked: {e}")

# Back to normal outside the block:
with cfg.mutable_copy() as cfg4:
    cfg4.port = 7000
assert cfg4.port == 7000


print("\nAll example sections completed successfully.")
