# Models

??? abstract "API Reference"

    [`syncwave.SyncModel`](../api/sync_model/)

This page introduces reactive **models**. You'll see how to define one, how models combine with the reactive collections, and how the `store` decorator creates a store in a single step. The last sections cover `SyncRoot` for single values, and `@sync_dataclass` if you prefer the dataclass syntax.

## Introduction to Models

!!! info "Skip"

    Feel free to [skip](#the-syncmodel-class) this introduction if you're already familiar with Pydantic.

The previous page, [collections](./collections/), ended on a problem. A store containing settings as `SyncDict[str, int]` works while every setting is an `int`, but as soon as you want to represent a `theme`, no dict type describes the data anymore. What the data needs is a fixed set of named fields, each with its own type.

??? note "`TypedDict` or `NamedTuple`?"

    Reactivity aside, you may be tempted to represent the app's settings using a `TypedDict` or a `NamedTuple`, but they aren't really what we need:

    - **`TypedDict`** is just a `dict` at runtime, meaning you can still assign whatever you want (`settings["typo"] = 5`), and types aren't enforced (`Settings({"volume": "loud!"})`). It is mostly useful for type checkers (`pyright`, `mypy`, `ty`, etc.) and IDE tools.
    - **`NamedTuple`** supports dot notation, so reading `settings.volume` works while `settings.typo` would get rejected. However, it is immutable: `settings.volume = 5` raises an exception. Types aren't enforced either: `Settings(volume="loud!")` works just fine.

The standard library's [`dataclasses`](https://docs.python.org/3/library/dataclasses.html) module is _almost_ what we need:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class Settings:
    volume: int
    brightness: int
    theme: Literal["light", "dark", "system"]
```

It's mutable, it rejects typos, it works well with tooling, but there's no runtime check: `Settings(volume="loud!", brightness=5, theme="system")` isn't rejected.

This is where [Pydantic](https://pydantic.dev/docs/validation/latest/get-started/) comes into play. It offers everything we need (and a lot more):

```python
from typing import Literal

from pydantic import BaseModel


class Settings(BaseModel):
    volume: int
    brightness: int
    theme: Literal["light", "dark", "system"]


settings = Settings(volume=8, brightness=5, theme="dark")
```

You define your model class by subclassing Pydantic's [`BaseModel`](https://pydantic.dev/docs/validation/latest/concepts/models/). `Settings` is a regular class you instantiate with keyword arguments. The `settings` instance is mutable, rejects typos, works well with tooling, _and_ data is validated at runtime.

Now our model always has exactly these three fields with these types, and anything else is an error:

```python
Settings(volume=8, brightness=5, theme="dark")  # valid
Settings(volume=8, brightness=5, theme="dakr")  # error
```

```console
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
theme
  Input should be 'light', 'dark' or 'system' [type=literal_error, input_value='dakr', input_type=str]
```

If you really want to stick with dataclasses, Pydantic also provides its own [dataclass](https://pydantic.dev/docs/validation/latest/concepts/dataclasses/), with added runtime validation. Just import it with `from pydantic.dataclasses import dataclass`, and create your dataclass like with the standard library's version.

One detail to know: Pydantic validates the data when an instance is created, but not when you assign a field afterward (unless you configure the model with `validate_assignment=True`). `settings.volume = "loud!"` goes through on a regular model. You'll see below that reactive models always validate assignments.

Now, the only missing part is **reactivity**. Just like a `SyncDict` is a `dict` that stays synchronized to its store, we need a version of `BaseModel` that does the same. That way, changing our app's settings gets persisted to the JSON file without even thinking about it, and changes made to the file update the `settings` instance.

## The `SyncModel` Class

To make a model reactive, simply subclass `SyncModel` instead of `BaseModel`; everything else is the same. Then use the class as the type of a store:

```python title="main.py" hl_lines="3 8 14"
from typing import Literal

from syncwave import SyncModel, Syncwave

syncwave = Syncwave()


class Settings(SyncModel):
    volume: int = 8
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"


settings = syncwave.create_store(Settings, name="settings")

settings.volume = 7
settings.theme = "dark"
```

Run the program, then open the file:

```json title="syncstores/settings.json"
{
  "volume": 7,
  "brightness": 5,
  "theme": "dark"
}
```

Each assignment is validated and written to the file, and operations like `settings.volume = "loud!"` or `settings.typo = 7` are rejected.

The other direction works as usual:

```python title="main.py" hl_lines="16 17"
from typing import Literal

from syncwave import SyncModel, Syncwave

syncwave = Syncwave()


class Settings(SyncModel):
    volume: int = 8
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"


settings = syncwave.create_store(Settings, name="settings")

input("Edit syncstores/settings.json, save it, then press Enter... ")
print(settings)
```

Run the program. While it waits at the prompt, change `syncstores/settings.json` to something else:

```json title="syncstores/settings.json"
{
  "volume": 8,
  "brightness": 5,
  "theme": "light"
}
```

Save the file and press ++enter++:

```console
$ python main.py
Edit syncstores/settings.json, save it, then press Enter...
volume=8 brightness=5 theme='light'
```

A reactive model stays connected in both directions, like every reactive object.

`SyncModel` is a subclass of `BaseModel`, so everything from Pydantic still works: `Field(...)`, validators, `model_config`, `model_dump()`, and so on. Instances also pass the usual checks:

```python
isinstance(settings, Settings)  # True
isinstance(settings, SyncModel)  # True
isinstance(settings, BaseModel)  # True
isinstance(settings, Reactive)  # True
```

Subclassing a reactive model gives you another reactive model.

### Default Values

Notice how every field of `Settings` has a default, which is why the store could start without passing the `default` parameter to `create_store`. If a field doesn't have a default, i.e. if you can't create an instance by simply calling `Settings()`, then you have to pass a default value explicitly, either as an instance or as a valid `dict`:

```python hl_lines="2 10"
class Settings(SyncModel):
    volume: int
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"


settings = syncwave.create_store(
    Settings,
    name="settings",
    default={"volume": 8},  # or Settings(volume=8)
)
```

As always, `default` is ignored if the file already contains data.

### Instance Lifecycle

Reactive models, like all[^1] reactive objects, can be inert, live, or dead.

[^1]: Excluding the `syncwave` instance itself, which is always **live**.

An instance you create yourself is **inert**:

```python
settings = Settings(volume=8)
print(settings.sync_state)  # SyncState.INERT
```

It behaves exactly like a regular Pydantic instance, so assignments aren't validated (unless you configured the model with `validate_assignment=True`), and it isn't connected to any store.

An instance you get from a store is **live**:

```python
settings = syncwave["settings"]
print(settings.sync_state)  # SyncState.LIVE
```

You can assign a whole new value to the store and Syncwave updates the live instance in place:

```python
print(settings)  # volume=8 brightness=5 theme='light'
syncwave["settings"] = {"volume": 1}
print(settings)  # volume=1 brightness=5 theme='system'
```

The `dict` was validated into a full `Settings`, so the fields it didn't mention went back to their defaults.

The same thing happens if you insert an instance:

```python
inert_settings = Settings(volume=2)
syncwave["settings"] = inert_settings

print(settings)  # volume=2 brightness=5 theme='system'
print(inert_settings.sync_state)  # SyncState.INERT
```

Note that the instance you assigned was inert and it remained inert. Only its values were used to update the existing live instance in place.

A live instance becomes **dead** when it's removed from the store. From then on, reading or assigning a field raises a `DeadReferenceError`:

```python
from syncwave import DeadReferenceError

del syncwave["settings"]
print(settings.sync_state)  # SyncState.DEAD

try:
    settings.volume = 8
except DeadReferenceError:
    print("DeadReferenceError")
```

## Reactive Fields

Model fields can be reactive types themselves:

```python title="main.py" hl_lines="12 16"
from typing import Literal

from syncwave import SyncList, SyncModel, Syncwave

syncwave = Syncwave()


class Settings(SyncModel):
    volume: int = 8
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"
    recent_files: SyncList[str] = SyncList()


settings = syncwave.create_store(Settings, name="settings")
settings.recent_files.append("notes.txt")
```

```json title="syncstores/settings.json"
{
  "volume": 8,
  "brightness": 5,
  "theme": "light",
  "recent_files": ["notes.txt"]
}
```

Everything from [Collections](./collections/) applies to the `recent_files` field. It's like any other `SyncList`, except it lives inside `Settings`.

??? note "Using `= []` instead of `= SyncList()` as a default value"

    In the example, the default value for `recent_files` is `SyncList()`, an inert `SyncList`. Using a normal list `[]` would work, but there are a few things you should know if you do:

    - By default, Pydantic doesn't validate the default values, so when you create an instance with `settings = Settings()`, the default list `[]` enters the model and is never validated. That means it's never transformed into a `SyncList`. For example, `type(settings.recent_files)` would just return `<class 'list'>`. This isn't a problem because this situation only happens with inert objects (a live `Settings` will have a live `SyncList` at `recent_files`): an inert `SyncList` behaves just like a normal `list`, so everything you could do with `recent_files` would be the same whether it's a `list` or a `SyncList`. Alternatively, you could use `Field(default=[], validate_default=True)`, but that's not prettier than simply using `SyncList()`.
    - Since you declare `recent_files` as a `SyncList` and you use `[]` as a default value, type checkers will see this as an error. It works in practice, but it's a small lie we tell type checkers.
    - You may also have a linter error since using a mutable value as a class attribute is a bad thing to do. This isn't seen as a problem for `BaseModel` because Pydantic deep-copies the mutable default, and linters know this, so they exempt `BaseModel` from that warning. Since `SyncModel` is a subclass of `BaseModel`, the same behavior is inherited, but linters don't know that (yet).

Fields can hold other reactive models too. Let's nest a window inside the settings:

```python
class Window(SyncModel):
    width: int = 1280
    height: int = 720


class Settings(SyncModel):
    volume: int = 8
    window: Window = Window()


settings = syncwave.create_store(Settings, name="settings")
settings.window.width = 1920
```

`settings.window.width = 1920` is validated and synced like any other change, two levels down.

As with other reactive types, the chain can't be broken. Here's what happens if you use a `SyncList` inside a regular `BaseModel`:

```python hl_lines="4"
from pydantic import BaseModel


class Broken(BaseModel):  # a `BaseModel`, not a `SyncModel`
    recent_files: SyncList[str] = []


syncwave.create_store(Broken, name="broken")
```

```console
TypeError: `SyncList` cannot be used here: Field `recent_files` in `Broken`: not in a reactive model (breaks the reactive chain).
```

## Models in Collections

The composition works in the other direction too. The most common pattern is probably a `SyncModel` inside a `SyncDict` (this is pretty much why the library was created in the first place!):

```python title="main.py" hl_lines="6 11"
from syncwave import SyncDict, SyncModel, Syncwave

syncwave = Syncwave()


class Customer(SyncModel):
    name: str
    age: int


customers = syncwave.create_store(SyncDict[int, Customer], name="customers")

customers[1] = Customer(name="Alice", age=25)
customers[2] = {"name": "Bob", "age": 30}  # a dict is validated into a Customer

customers[2].age = 31  # validated, written to the file
```

```json title="syncstores/customers.json"
{
  "1": {
    "name": "Alice",
    "age": 25
  },
  "2": {
    "name": "Bob",
    "age": 31
  }
}
```

(The keys are always strings in a JSON file, but in Python they are integers. See [Key Types](./collections/#key-types).)

Notice that `Customer` doesn't need defaults for its fields here. The store is a `SyncDict`, so it starts as an empty dict, and no `Customer` has to exist yet.

References work the way you'd expect from the collections: `alice = customers[1]` stays connected to whatever lives under key `1`, in both directions, for as long as the entry exists. Once you `del customers[1]`, `alice` is dead (😞). `SyncList[Customer]` works the same way, with the [position-based identity](./collections/#position-based-identity) of lists.

The only collection that can't hold reactive models is `SyncSet`, because its items must be hashable and reactive models are mutable (see [Hashable Items](./collections/#hashable-items)).

## The `store` Decorator

Defining a reactive model and creating a store of its instances right after is so common that there's a decorator for it. [`@syncwave.store`](../api/syncwave/#syncwave.Syncwave.store) creates the store as soon as the class is defined:

```python title="main.py" hl_lines="6 12"
from syncwave import SyncList, SyncModel, Syncwave

syncwave = Syncwave()


@syncwave.store(name="customers")
class Customer(SyncModel):
    name: str
    age: int


customers: SyncList[Customer] = syncwave["customers"]
customers.append(Customer(name="Alice", age=25))

alice = customers[0]
alice.age = 26  # validated, written to the file
```

You need to delete the previous `syncstores/customers.json` for this example to run: the store was a `SyncDict[int, Customer]`, and now it's a `SyncList[Customer]`. The [next section](#collection-wrapping) explains why.

The result is:

```json title="syncstores/customers.json"
[
  {
    "name": "Alice",
    "age": 26
  }
]
```

This is equivalent to creating the store like this:

```python
class Customer(SyncModel):
    name: str
    age: int


customers = syncwave.create_store(SyncList[Customer], name="customers")
```

The decorator returns your class unchanged, so you keep using `Customer` as usual (instantiation, type hints, `isinstance` checks). Since the store is created inside the decorator, you get it afterward by reading from `syncwave` like `customers: SyncList[Customer] = syncwave["customers"]`. The type hint `SyncList[Customer]` isn't mandatory, but it helps your editor and type checker, which have no way of knowing the type of `customers` otherwise.

The decorated class must be reactive. Decorating a regular `BaseModel` raises an error:

```console
TypeError: Use a `SyncModel` instead of a `BaseModel`.
```

You can also decorate a [`SyncRoot`](#single-values-with-syncroot) or a [reactive dataclass](#reactive-dataclasses), which you will meet below.

### Collection Wrapping

The store above is a _list_ of customers even though nothing declared that explicitly. You can decide which collection should be used to wrap `Customer` by using the `collection` parameter. Its default value `"auto"` picks a wrapping based on your model:

| Condition              | Store type                      | Access pattern     |
| ---------------------- | ------------------------------- | ------------------ |
| Has a `key` field      | `SyncDict[<key type>, <model>]` | `store[key]`       |
| No `key` field         | `SyncList[<model>]`             | `store[index]`     |
| Subclass of `SyncRoot` | `<model>` (no wrapping)         | `syncwave["name"]` |

So giving your model a field named `key` is enough to get a dictionary-shaped store:

```python
@syncwave.store(name="customers")
class Customer(SyncModel):
    key: str
    name: str
    age: int


customers = syncwave["customers"]
customers["c1"] = {"key": "c1", "name": "Alice", "age": 25}
```

Note that the dictionary key and the model's `key` field are independent: the field only influences which wrapping `"auto"` picks, and the key type of the `SyncDict`. For example, defining a model with `key: datetime.datetime` makes `"auto"` pick `SyncDict[datetime.datetime, Customer]` for the store. The same rules described in [Key Types](./collections/#key-types) apply, even if the type comes from the annotation on `key`.

You can also pick the collection explicitly:

- `collection=SyncList` forces a list, even if the model has a `key` field.
- `collection=SyncDict` forces a dictionary with `str` keys. For another key type, pass it as the only type argument, e.g. `collection=SyncDict[int]`.
- `collection=None` disables wrapping: the store holds a single instance, like the `settings` store [earlier](#the-syncmodel-class).

`SyncSet` is not an option, for the reason given in [Models in Collections](#models-in-collections).

### The `default` Parameter

`store` accepts a `default`, just like `create_store`. Without wrapping, you need it when some fields have no default. Since the class doesn't exist yet when the decorator's arguments are evaluated, pass plain data (not an instance):

```python
@syncwave.store(name="config", collection=None, default={"debug": True})
class Config(SyncModel):
    debug: bool
```

Even when you don't _need_ a default, you can pass a value to seed data:

```python
data = {
    "c1": {"key": "c1", "name": "Alice", "age": 25},
    "c2": {"key": "c2", "name": "Bob", "age": 30},
}


@syncwave.store(name="customers", default=data)
class Customer(SyncModel):
    key: str
    name: str
    age: int
```

Don't forget that the value passed to `default` is ignored if the file already contains data.

## Single Values with `SyncRoot`

So far, stores usually held a container (e.g. a `SyncList`) or a model class with fields (e.g. `Settings`). However, a store containing a single value is also valid.

For example, you could have:

```python
locale = syncwave.create_store(str, name="locale", default="en-US")
```

(Without `default`, the store would start as an empty string `""`.)

Here, `locale` is not a reactive value; it's a normal string. This has all the limitations we know: you can't use `locale` to update the store, and `locale` is a snapshot of the store, so changing the file or the value at `syncwave["locale"]` won't update it.

You might wish for a reactive handle on such a store, i.e. have `locale` be an object you could mutate to change the store, and print to see the current one (something like `ref()` in Vue.js). This can be achieved using [`SyncRoot`](../api/sync_model/#syncwave.SyncRoot), the reactive version of Pydantic's [`RootModel`](https://pydantic.dev/docs/validation/latest/concepts/models/#rootmodel-and-custom-root-types):

```python title="main.py" hl_lines="6 9"
from syncwave import SyncRoot, Syncwave

syncwave = Syncwave()


class Locale(SyncRoot[str]): ...


locale = syncwave.create_store(Locale, name="locale", default="en-US")
```

You subclass `SyncRoot` and give it a type, which produces a model with a single field named `root`. It comes with the same validation and serialization as other models.

You can achieve the same result with the `store` decorator:

```python hl_lines="1 5"
@syncwave.store(name="locale", default="en-US")
class Locale(SyncRoot[str]): ...


locale: Locale = syncwave["locale"]
```

When using the decorator with a `SyncRoot`, the `"auto"` mode doesn't wrap it in a collection, since the whole point is to hold a single value.

To be even more concise, you don't have to define `Locale` at all. You can simply write:

```python
locale = syncwave.create_store(SyncRoot[str], name="locale", default="en-US")
```

Whichever you prefer, the result is the same.

The JSON file holds the bare value, not an object around it:

```json title="syncstores/locale.json"
"en-US"
```

So `locale` is a live handle on a single string, following the file in both directions like every reactive object. To update the store you update the `root` field:

```python
locale.root = "fr-CA"
```

```json title="syncstores/locale.json"
"fr-CA"
```

Assigning through the instance still works too; `syncwave["locale"] = "fr-CA"` updates `locale` in place.

??? note "`SyncRoot` is a subclass of `SyncModel`"

    Just like Pydantic's `RootModel` is a subclass of `BaseModel`, `SyncRoot` is a subclass of `SyncModel`. That means that everything about `SyncModel` applies to `SyncRoot` as well. For example, all of these are `True`:

    ```python
    issubclass(Locale, SyncModel)  # True
    issubclass(Locale, BaseModel)  # True
    issubclass(Locale, Reactive)  # True
    ```

## Reactive Dataclasses

If you prefer the dataclass syntax, Syncwave provides [`@sync_dataclass`](../api/sync_model/#syncwave.sync_dataclass). It applies Pydantic's dataclass decorator, so the class is validated exactly like a [Pydantic dataclass](https://pydantic.dev/docs/validation/latest/concepts/dataclasses/), and makes it reactive on top:

```python title="main.py" hl_lines="6"
from syncwave import SyncList, Syncwave, sync_dataclass

syncwave = Syncwave()


@sync_dataclass
class Point:
    x: int = 0
    y: int = 0


points = syncwave.create_store(SyncList[Point], name="points")
points.append(Point(x=1, y=2))
points[0].x = 3  # validated, written to the file
```

A class decorated with `@sync_dataclass` behaves like a `SyncModel` regarding reactivity: it has the same three states, assignments are validated, it can have reactive fields, `store` accepts it, etc.

!!! warning "Mutable defaults"

    Dataclasses don't accept mutable defaults. A field like `tags: list[str] = []` raises an error when the class is defined, and so do `SyncList()`[^2] and any other unhashable default. `BaseModel` and `SyncModel` don't have this restriction because Pydantic copies mutable defaults for each instance.

    To use a mutable default on dataclasses you need to use a factory:

    ```python
    from dataclasses import field


    @sync_dataclass
    class MyDataclass:
        normal_list: list[str] = field(default_factory=list)
        sync_list: SyncList[str] = field(default_factory=SyncList)
    ```

[^2]: Before Python 3.11, `SyncList()` doesn't raise an error, but still don't use it 
there.

The class decorated with `@sync_dataclass` keeps its own class hierarchy, so it doesn't inherit from `Reactive`. For example, `issubclass(Point, Reactive)` and `isinstance(Point(), Reactive)` both return `False`. To check whether a dataclass is reactive, Syncwave provides the function [`is_sync_dataclass`](../api/sync_model/#syncwave.is_sync_dataclass).

This follows the naming of the other kinds of dataclasses:

| Kind     | Decorator                         | Check                   |
| -------- | --------------------------------- | ----------------------- |
| Stdlib   | `@dataclasses.dataclass`          | `is_dataclass`          |
| Pydantic | `@pydantic.dataclasses.dataclass` | `is_pydantic_dataclass` |
| Syncwave | `@syncwave.sync_dataclass`        | `is_sync_dataclass`     |

A reactive dataclass is also a Pydantic dataclass, and a Pydantic dataclass is also a stdlib dataclass, so all three checks return `True` for `Point`.

## What's Next

With models covered, you now know all the reactive types. A lot of information about the reactive system is scattered across the previous three pages. [Reactivity](./reactivity/) presents everything in one place.

For the complete API of reactive models, see the [API Reference](../api/sync_model/).
