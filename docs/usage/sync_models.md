# Sync Models

Collections organize your data; models give it a schema. Syncwave can make your own Pydantic classes reactive: instances coming from a store stay connected to it, every field assignment is validated and written to the JSON file, and external changes to the file update the instances in place.

## Register a Model

The most direct way is the `@syncwave.register` decorator:

```python title="main.py"
from pydantic import BaseModel
from syncwave import Syncwave

syncwave = Syncwave()


@syncwave.register(name="customers")
class Customer(BaseModel):
    name: str
    age: int


customers = syncwave["customers"]
customers.append(Customer(name="Alice", age=30))
customers.append({"name": "Bob", "age": 25})  # dicts are validated too

alice = customers[0]
alice.age = 31  # validated, written to the file

print(customers)  # [name='Alice' age=31, name='Bob' age=25]
```

```json title="syncstores/customers.json"
[
  {
    "name": "Alice",
    "age": 31
  },
  {
    "name": "Bob",
    "age": 25
  }
]
```

A few things are worth spelling out:

- The decorator returns your class **unchanged**. You keep using `Customer` everywhere — type hints, instantiation, `isinstance` checks — as if Syncwave weren't involved.
- Internally, Syncwave derives a reactive class from yours (here it would be named `SyncCustomer`). The instances you get back from the store are instances of that derived class, so `isinstance(alice, Customer)` is still `True`.
- By default the store is a list of models, hence the `append` calls. The next section shows how to control this.

## Collection Wrapping

A single model instance is rarely a whole store: usually you want a collection of them. The `collection` parameter of `register` controls the wrapping, and its default value `"auto"` picks based on the shape of your model:

| Model shape             | Store type                    | Access pattern     |
| ----------------------- | ----------------------------- | ------------------ |
| Has a `key` field       | `SyncDict[<key type>, Model]` | `store[key]`       |
| No `key` field          | `SyncList[Model]`             | `store[index]`     |
| Subclass of `RootModel` | No wrapping                   | `syncwave["name"]` |

So giving your model a field named `key` is enough to get a dictionary-shaped store:

```python
@syncwave.register(name="users")
class User(BaseModel):
    key: str
    email: str


users = syncwave["users"]
users["u1"] = {"key": "u1", "email": "u1@example.com"}
print(users)  # {'u1': key='u1' email='u1@example.com'}
```

Note that the dictionary key and the model's `key` field are independent: the field only influences which wrapping `"auto"` picks, it is up to you to keep them consistent.

You can also override the auto behavior explicitly:

- `collection=SyncList` or `collection=SyncDict` forces the wrapping (a `key` field, if any, is ignored). `SyncDict` accepts one type argument for the key here, e.g. `collection=SyncDict[int]`.
- `collection=None` disables wrapping: the store holds a single instance. A natural fit for configuration objects:

```python
@syncwave.register(name="config", collection=None)
class Config(BaseModel):
    theme: str = "light"
    debug: bool = False


config = syncwave["config"]
config.theme = "dark"  # syncstores/config.json is updated
```

`SyncSet` is not an option: sets cannot contain mutable items, and reactive models are mutable.

## Supported Classes

Syncwave can make three kinds of classes reactive:

1. subclasses of `pydantic.BaseModel`,
2. subclasses of `pydantic.RootModel`,
3. classes decorated with `@pydantic.dataclasses.dataclass`.

Standard-library dataclasses and plain Python classes are not supported, because Syncwave needs the validation machinery that Pydantic attaches to a class. The error tells you the fix:

```python
from dataclasses import dataclass


@dataclass
class Point:
    x: int


syncwave.make_reactive(Point)
```

```console
TypeError: Standard `dataclasses.dataclass` are not supported, use `pydantic.dataclasses.dataclass` instead.
```

You can test a class programmatically with [is_sync_model_supported](../api/sync_model/#syncwave.is_sync_model_supported).

## The Explicit Path

`@syncwave.register` is a convenience that does two things in one step. When you need more control — a different store layout, the model in several stores, a custom class name — use the two underlying operations directly:

```python
from pydantic import BaseModel
from syncwave import SyncList, Syncwave


class Product(BaseModel):
    name: str
    price: float


SyncProduct = syncwave.make_reactive(Product)
products = syncwave.create_store(SyncList[SyncProduct], name="products")
products.append(Product(name="pen", price=1.5))
```

[make_reactive](../api/syncwave/#syncwave.Syncwave.make_reactive) returns a new class that inherits from both `SyncModel` and your class; the original is not modified. The returned class is a type like any other: use it in `SyncList[...]`, `SyncDict[str, ...]`, as a model field, nested at any depth.

A class can only be made reactive once per `Syncwave` instance. Registering `Product` a second time raises an error, which prevents two stores from silently competing over the same class.

## Reactive Fields

Model fields can themselves be reactive types, and reactivity keeps propagating down:

```python
from syncwave import SyncList, Syncwave


@syncwave.register(name="projects")
class Project(BaseModel):
    name: str
    tags: SyncList[str]


projects = syncwave["projects"]
projects.append({"name": "syncwave", "tags": ["python"]})
projects[0].tags.append("json")  # synced, three levels deep
```

Plain container fields like `list[str]` work too, but with the usual caveat: assigning to the field (`project.tags = [...]`) is detected and synced, while in-place changes to the plain list are invisible. Use a reactive field type when you want to mutate in place.

## Restrictions

Reactive models trade a little flexibility for the sync guarantee:

- **Frozen models cannot be made reactive.** A frozen model can never change, so there is nothing to sync; attempting it raises a `TypeError`. Use frozen models as regular values inside a store instead. Individual frozen fields are rejected for the same reason.
- **Tracked fields cannot be deleted.** `del alice.age` raises an `AttributeError` telling you to set the field to `None` instead (if the type allows it), because a missing field cannot be represented consistently in the JSON.

Like every reactive object, model instances have a `sync_live` property and raise `DeadReferenceError` once they are removed from their store. How and when that happens is the subject of [Reactivity](./reactivity/). For the complete API, see the [API Reference](../api/sync_model/).
