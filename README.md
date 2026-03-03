# Syncwave

Make your code reactive; Turn plain JSONs into a live data store, two-way synced with Python objects.

> [!WARNING]
>
> Syncwave is under active development. Until version **1.0**, any minor release (`0.x`) may introduce breaking changes. Pin the exact version in production.
>
> Version **1.0** will be released when the library is stable, feature-complete, and tested.

## Installation

Install from [PyPI](https://pypi.org/project/syncwave/).

```shell
# pip
pip install syncwave

# uv
uv add syncwave
```

Requires Python 3.9+.

## Quick Start

Bind in-memory data to a JSON file with `@syncwave.register`. _Syncwave_ automatically synchronizes the file with your in-memory objects.

```python
from pydantic import BaseModel

from syncwave import Syncwave

syncwave = Syncwave()


# Register a model — creates `syncstores/customers.json` automatically.
# If the file already exists, its data is loaded into the store.
@syncwave.register(name="customers")
class Customer(BaseModel):
    key: int
    name: str
    age: int


customers = syncwave["customers"]

# Add entries — dicts and model instances both work.
customers[1] = {"key": 1, "name": "John Doe", "age": 30}
customers[2] = Customer(key=2, name="Jane Doe", age=25)

# Grab a reference and mutate — changes are written to disk automatically.
john = customers[1]
john.age = 31  # syncstores/customers.json is updated

# The store is a dict-like object that can be printed.
print(customers)  # {1: key=1 name='John Doe' age=31, 2: key=2 name='Jane Doe' age=25}
print(syncwave)  # {'customers': ...}

# Delete an entry.
del customers[1]  # removed from memory and from the file

# External edits propagate back.
input("Go edit syncstores/customers.json, then press Enter... ")
print(customers)  # reflects the external changes
```

## The Reactive System

See [`docs/reactivity.md`](docs/reactivity.md).

## API Reference

### `Syncwave`

The entry point. A `MutableMapping[str, Any]` where each key is a store name and each value is the store's data (which may or may not be reactive). Each store maps to a single JSON file.

```python
from syncwave import Syncwave

syncwave = Syncwave(root_path="data")
```

- `root_path` (optional): Directory for JSON files. Defaults to `./syncstores`.
- `syncwave.root_path`: Read-only property returning the resolved path.

**Methods:**

- `syncwave.create_store(tp, *, name, default)` — Creates a store for a given type. The type can be anything supported by Pydantic, including reactive types. Creates or loads the associated JSON file. A `default` is required when the file is empty and the type has no natural empty form (e.g., `int`).
- `syncwave.make_reactive(cls, *, cls_name)` — Turns a Pydantic model into a `SyncModel` class.
- `@syncwave.register(*, name, collection)` — Class decorator that makes a model reactive and creates a store for it in one step. See [Collection Wrapping](#collection-wrapping) below.

Standard `MutableMapping` operations are supported: `syncwave[name]`, `syncwave[name] = value`, `del syncwave[name]`, `len(syncwave)`, iteration, etc.

### `SyncDict`, `SyncList`, `SyncSet`

Reactive counterparts of `dict`, `list`, and `set`. They behave like their standard equivalents (`MutableMapping`, `MutableSequence`, `MutableSet`) but every mutation is validated by Pydantic, persisted to disk, and propagated from disk.

They are parameterized like their standard counterparts:

```python
from syncwave import SyncDict, SyncList, SyncSet

syncwave.create_store(SyncDict[str, int], name="settings")
syncwave.create_store(SyncList[str], name="tags")
syncwave.create_store(SyncSet[int], name="ids")
```

Reactive collections can be nested: `SyncDict[str, SyncList[int]]`, `SyncList[SyncDict[str, str]]`, etc. `SyncSet` cannot hold reactive items (since reactive objects are mutable and therefore not hashable).

`SyncCollection` is the abstract base for all three — useful for `isinstance` checks.

### `SyncModel`

A user-defined Pydantic model that Syncwave has made reactive. Attribute access and assignment are intercepted to keep the data in sync.

Created via `syncwave.make_reactive` or `@syncwave.register`. The supported model types are:

1. subclasses of `pydantic.BaseModel`,
2. subclasses of `pydantic.RootModel`,
3. classes decorated with `@pydantic.dataclasses.dataclass`.

`@syncwave.register` returns the **original class**, not the `SyncModel`. You can keep using it normally — Syncwave handles the reactive wrapping internally.

### `Reactive`

Abstract base class that all reactive types inherit from. Provides the `sync_live` property, which is `True` while the object is connected to a store and `False` after it has been killed (e.g., by deleting the store or replacing the entry). Operations on a dead reference raise `DeadReferenceError`.

```python
from syncwave import Reactive

ref = syncwave["customers"][1]
isinstance(ref, Reactive)  # True
ref.sync_live               # True

del syncwave["customers"][1]
ref.sync_live               # False
```

### `is_sync_model_supported`

Type guard that returns `True` if a class can be made reactive (i.e., it is a `BaseModel`, `RootModel`, or Pydantic dataclass, and is not already a `SyncModel`).

```python
from syncwave import is_sync_model_supported

is_sync_model_supported(Customer)  # True
is_sync_model_supported(int)       # False
```

## Collection Wrapping

When using `@syncwave.register`, the `collection` parameter controls how the model is stored:

| Model shape             | Wrapping                      | Access pattern              |
| ----------------------- | ----------------------------- | --------------------------- |
| Has a `key` field       | `SyncDict[<key type>, Model]` | `store[key]`                |
| No `key` field          | `SyncList[Model]`             | `store[index]`              |
| Subclass of `RootModel` | No wrapping                   | `syncwave["name"]` directly |

This is the default `collection="auto"` behavior. You can override it:

```python
from syncwave import SyncList


# Force a list even though the model has a `key` field.
@syncwave.register(name="items", collection=SyncList)
class Item(BaseModel):
    key: int  # ignored
    name: str


# No wrapping — the store holds a single model instance.
@syncwave.register(name="config", collection=None)
class Config(BaseModel):
    theme: str
    debug: bool
```

## License

Syncwave is licensed under the MIT License.
