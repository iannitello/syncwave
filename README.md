# Syncwave

Make your code reactive; Turn plain JSONs into a live data store, two-way synced with Python objects.

> [!WARNING]
>
> Syncwave is under active development. Until version **1.0**, any minor release (`0.x`) may introduce breaking changes. Pin the exact version in production.
>
> Version **1.0** will be released when the library is stable, feature-complete, and tested.

## What Is Syncwave

Syncwave keeps in-memory Python objects and JSON files synchronized, in both directions, while your program runs:

- Change your data from Python — a list `append`, a dict assignment, a model field update — and the JSON file is updated.
- Edit the JSON file — in a text editor, from another process, with a script — and your Python objects are updated in place.
- Every change, from either side, is validated with [Pydantic](https://docs.pydantic.dev/latest/) against the types you declared. Invalid data is rejected, and an invalid file edit is reverted to the last valid state.

Think of it as persistence with almost no effort: no database, no ORM, no queries, no serialization code. Your data lives in human-readable JSON files you can open and edit at any time, and in ordinary-feeling Python objects you can pass around. If you know Python type hints and Pydantic models, you already know everything you need.

## Installation

Install from [PyPI](https://pypi.org/project/syncwave/).

```shell
# pip
pip install syncwave

# uv
uv add syncwave
```

Requires Python 3.10+.

## Quick Start

Bind a Pydantic model to a JSON file with `@syncwave.register`:

```python
from pydantic import BaseModel

from syncwave import Syncwave

syncwave = Syncwave()


# Creates `syncstores/customers.json` automatically.
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

# Keep a reference and mutate — changes are written to disk automatically.
john = customers[1]
john.age = 31  # syncstores/customers.json is updated

print(customers)  # {1: key=1 name='John Doe' age=31, 2: key=2 name='Jane Doe' age=25}

# Delete an entry — removed from memory and from the file.
del customers[1]

# External edits propagate back while the program runs.
input("Go edit syncstores/customers.json, then press Enter... ")
print(customers)  # reflects your changes
```

Stores are not limited to models. Any type Pydantic can handle works, and the reactive collections (`SyncDict`, `SyncList`, `SyncSet`) can be nested freely:

```python
from syncwave import SyncList, SyncSet, Syncwave

syncwave = Syncwave()

tags = syncwave.create_store(SyncSet[str], name="tags")
tags.add("python")  # synced to syncstores/tags.json

matrix = syncwave.create_store(SyncList[SyncList[int]], name="matrix")
matrix.append([1, 2])
matrix[0].append(3)  # reactivity reaches all the way down
```

## How It Works

The core idea: the domain of Python objects is vast, but the domain of JSON is small — and **JSON Schema** is the bridge between the two. If a type can be described by a JSON Schema, it can round-trip between Python and a JSON file. Syncwave delegates that entire concern to Pydantic, which means anything Pydantic can validate and serialize can be a store.

On top of that foundation, Syncwave provides **reactive types**: `SyncDict`, `SyncList`, and `SyncSet` mirror `dict`, `list`, and `set`, and your own Pydantic models can be made reactive too. They behave like the originals, but every mutation is validated and persisted, and external file changes are applied to the same objects in place — so references you hold stay valid and current. Writes to disk are debounced and atomic; the files are always complete, valid, human-readable JSON.

## Documentation

The full documentation lives at [syncwave.dev](https://syncwave.dev/):

- [Syncwave](https://syncwave.dev/usage/syncwave/) — the entry point: instances, stores, two-way sync, validation.
- [Sync Collections](https://syncwave.dev/usage/sync_collections/) — `SyncDict`, `SyncList`, `SyncSet`, and nesting.
- [Sync Models](https://syncwave.dev/usage/sync_models/) — making your Pydantic models reactive with `@syncwave.register`.
- [Reactivity](https://syncwave.dev/usage/reactivity/) — the mental model: references, in-place updates, lifecycles.
- [Types and Validation](https://syncwave.dev/usage/types_and_validation/) — what can be a store, defaults, keys, coercion.
- [JSON Files](https://syncwave.dev/usage/json_files/) — the disk side: atomic writes, safe access, external edits.
- [API Reference](https://syncwave.dev/api/syncwave/) — the complete API.

## Trade-offs

Syncwave is not a database, on purpose. Each store is fully held in memory and rewritten to its file as a whole; there are no partial updates, no transactions, and no multi-process coordination. That makes it a great fit for configuration, small-to-medium application state, prototypes, and tools where transparency matters — and a poor fit for large datasets or high-frequency concurrent writers. When you outgrow it, you'll know.

## License

Syncwave is licensed under the MIT License.
