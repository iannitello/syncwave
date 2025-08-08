# Syncwave

Make your code reactive; Turn plain JSONs into a live data store, two-way synced with Python objects.

> [!WARNING]
> Syncwave is under active development. Until version **1.0**, any minor release (`0.x`) may introduce breaking API changes. Pin the exact version to use in production.
>
> Version **1.0** will be released when the library is stable, feature-complete, and tested.

## Getting Started

Install from [PyPI](https://pypi.org):

```shell
# pip
pip install syncwave

# uv
uv add syncwave
```

Bind a Pydantic model to a JSON file with `@syncwave.register`. Syncwave automatically synchronizes the file with your in-memory objects.

```python
from pydantic import BaseModel

from syncwave import Syncwave

# 1. Initialise Syncwave with a directory to hold the JSON files.
syncwave = Syncwave(data_dir="data")


# 2. Define and register a model
# This also creates a JSON file at `data/customers.json` if it doesn't exist.
# Otherwise, the data is loaded into the `syncwave` instance.
@syncwave.register(name="customers", key="id")
class Customer(BaseModel):
    id: int
    name: str
    age: int


# syncwave acts as a read-only dict with:
#     keys   -> collection name (e.g. "customers")
#     values -> corresponding SyncStore
# A SyncStore also acts as a read-only dict:
#     keys   -> model's key field (e.g. `id`)
#     values -> live Pydantic model instances


# 3. Create and mutate instances like you would with normal Pydantic objects.
john = Customer(id=1, name="John Doe", age=30)  # Automatically inserted in JSON
john.age = 31  # Automatically updated in JSON


# 4. Delete instances with `SyncStore.delete(key)`.
syncwave["customers"].delete(1)  # Removed from syncwave and JSON

# 5. External edits propagate automatically.
# First, let's add a new customer to populate the JSON file.
Customer(id=2, name="Jane Doe", age=25)

input("Now edit 'data/customers.json' and press Enter…")
print(syncwave)
```

## License

Syncwave is licensed under the MIT License.
