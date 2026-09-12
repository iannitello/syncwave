# Models

??? abstract "API Reference"

    [`syncwave.SyncModel`](../api/sync_model/)

<!-- !!! danger "Comment"

    Don't assume the reader is familiar with dataclasses or pydantic models. These should be introduced and the reader should feel like this is very useful to them. We of course shouldn't explain everything there is to know about models either since that would amount to writing the whole Pydantic docs. So we should invite the user to read more, and link the pydantic docs.

    The page overall should read like this:

    1. The concept of model is introduced (dataclass/pydantic model).
    2. How to create a model using `make_reactive`, and the details about that.
    3. What can be made reactive, and `is_sync_model_supported` as an analogy with `issubclass` `isinstance`
    4. How you can use reactive types in the fields (both SyncCollection types and other reactive models)
    5. Vice-versa, how you can use models as the elements of a reactive collection (e.g. the canonical example of the "customer" store which would be like `SyncDict[int, SyncCustomer]`)
    6. Show that everything "just works" with `pydantic.RootModel` as well, link to RootModel (https://pydantic.dev/docs/validation/latest/concepts/models/#rootmodel-and-custom-root-types) in Pydantic docs, introduce the idea of a potential `SyncValue`, and show that using `pydantic.RootModel` does exactly that already.
    7. After this was all slowly introduced, we can now show the `register` decorator as a kind of shorthand/Syntactic sugar. Should be presented as `make_reactive` + `create_store`. The collection wrapping should be well explained: how to manually decide which collection (if any) is used to wrap around the instances of the decorated model class, and the "auto" mode and how it chooses intelligently for the user. -->

This page introduces reactive **models**. You'll see how to create one with `make_reactive`, how models combine with the reactive collections, and how the `register` decorator sets everything up in one line.

## Introduction to Models

!!! info

    Feel free to [skip](#making-a-syncmodel) this introduction if you're already familiar with Pydantic.

The previous page, [collections](./collections/), ended on a problem. A store containing settings as `SyncDict[str, int]` works while every setting is an `int`, but as soon as a `theme` needs to fit in, no dict type describes the data anymore, and loosening everything to `Any` throws validation away. What the data needs is a fixed set of named fields, each with its own type.

??? note "`TypedDict` or `NamedTuple`?"

    Reactivity aside, you may be tempted to represent the app's settings using a `TypedDict` or a `NamedTuple`, but they aren't really what we need:

    - A `TypedDict` is just a `dict` at runtime, meaning you can still assign whatever you want `settings["typo"] = 5`, and types aren't enforced `Settings({"volume": "loud!"})`. It is mostly useful for type checkers (`ty`, `mypy`, `pyright`, etc.) and IDE tools.
    - A `NamedTuple` supports dot-notation, so reading `settings.volume` works while `settings.typo` would get rejected. However, it is immutable: `settings.volume = 5` raises an exception. Also, typing isn't enforced either, `Settings(volume="loud!")` works just fine. Use where you would use a regular `tuple`, but want enhanced readability and better tool support.

The standard library [`dataclasses`](https://docs.python.org/3/library/dataclasses.html) module is _almost_ what we need:

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

This is where Pydantic comes into play. It offers everything we need (and a lot more):

```python
from typing import Literal

from pydantic import BaseModel


class Settings(BaseModel):
    volume: int
    brightness: int
    themes: Literal["light", "dark", "system"]
```

`Settings` is a regular class you instantiate with keyword arguments. The `settings` instance is mutable, rejects typos, works well with tooling, _and_ data is validated at runtime!

It always has exactly these three fields with these types, and anything else is an error:

```python
Settings(volume=8, brightness=5, theme="dark")  # valid
Settings(volume=8, brightness=5, theme="dakr")  # error
```

```console
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
theme
  Input should be 'light', 'dark' or 'system' [type=literal_error, input_value='dakr', input_type=str]
```

You define your model class by subclassing Pydantic's `BaseModel`. If you really want to stick with dataclasses, Pydantic also provides its own dataclass, but with added runtime validation. Just import it `from pydantic.dataclasses import dataclass`, and create your dataclass like with the standard library's version.

Now, the only missing part is **reactivity**. Just like a `SyncDict` is a `dict` that stays synchronized to its store, we need a version of `Settings` that does the same. That way, changing our app's settings gets persisted to the JSON file without even thinking about it, and changes made to the file update the `settings` instance.

### Models Cheat Sheet

Here's a quick overview of how to work with models, it contains pretty much everything you need to use them in Syncwave.

!!! tip

    Models can do a lot more than what is shown here. The [Pydantic documentation](https://pydantic.dev/docs/validation/latest/get-started/) contains everything, and it's a good read even outside of Syncwave.

<table>
  <tr>
    <th>Operation</th>
    <th>Syntax</th>
  </tr>
</table>

## Making a `SyncModel`

The `syncwave` instance has a method called [`make_reactive`](../api/syncwave/#syncwave.Syncwave.make_reactive) that you use to create a new reactive version of your model: `SyncSettings = syncwave.make_reactive(Settings)`.

`SyncSettings` is a type like any other, so you can declare a store with it. Let's use it to manage the settings:

```python title="main.py" hl_lines="15 16"
from typing import Literal

from pydantic import BaseModel
from syncwave import Syncwave

syncwave = Syncwave()


class Settings(BaseModel):
    volume: int = 8
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"


SyncSettings = syncwave.make_reactive(Settings)
settings = syncwave.create_store(SyncSettings, name="settings")

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

Each assignment was validated and written to the file.

And unlike a `SyncDict[str, Any]`, the model holds its shape. Both these operations would be rejected:

```python
settings.volume = "loud!"
settings.typo = 7
```

The other direction works as usual: edit `syncstores/settings.json` while the program runs, and `settings` picks up the change. A reactive model stays connected in both directions, like every reactive object.

The returned class is a new class inheriting both from the original `Settings` and from `SyncModel`, a class provided by Syncwave.

Similarly to `SyncCollection`, the parent class of `SyncDict`/`SyncList`/`SyncSet`, you never really need to use the actual `SyncModel` except for static typing and for runtime checks:

```python
isinstance(numbers, SyncCollection)  # True
issubclass(SyncList, SyncCollection)  # True
issubclass(SyncCollection, Reactive)  # True
```

A few details about `make_reactive`:

- The original class is not modified. `SyncSettings` is a new class inheriting from both `SyncModel` and `Settings`, so instances from the store satisfy `isinstance(settings, Settings)` and `isinstance(settings, SyncModel)`.
- The new class is named `Sync` + the original name. The `cls_name` parameter overrides this.
- A class can only be made reactive once per `Syncwave` instance. A second call raises an error, which prevents two stores from silently competing over the same class.

Also, just like the collections, you never instantiate the reactive class yourself: `SyncSettings()` raises the same `TypeError` you saw in [Instance Creation](./collections/#instance-creation). Keep creating regular `Settings` instances (or plain dicts), and Syncwave converts them on the way in.

Notice how every field on the original class has a default, which is why the store could start without passing the `default` parameter to `create_store`. If a field didn't have a default value, i.e. if you couldn't create an instance by simply calling `Settings()` without parameters, then you'd have to pass a default value explicitly, either as an instance of `Settings`, or as a valid `dict`:

```python hl_lines="2 11"
class Settings(BaseModel):
    volume: int
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"


SyncSettings = syncwave.make_reactive(Settings)
settings = syncwave.create_store(
    SyncSettings,
    name="settings",
    default={"volume": 8},  # or Settings(volume=8)
)
```

### Supported Classes

`make_reactive` accepts three kinds of classes:

1. subclasses of `pydantic.BaseModel`,
2. subclasses of `pydantic.RootModel` (more on those [below](#single-values-with-rootmodel)),
3. classes decorated with `@pydantic.dataclasses.dataclass`.

If you prefer the dataclass syntax, [Pydantic's dataclasses](https://pydantic.dev/docs/validation/latest/concepts/dataclasses/) behave exactly like `BaseModel` classes on this page. Standard-library dataclasses and plain Python classes are not supported, because Syncwave needs the validation machinery Pydantic attaches to a class. The error tells you the fix:

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

To check a class programmatically, use [is_sync_model_supported](../api/sync_model/#syncwave.is_sync_model_supported). It plays the role `isinstance` and `issubclass` play elsewhere: a way to ask "can this class be made reactive?" before trying:

```python
from syncwave import is_sync_model_supported

is_sync_model_supported(Settings)  # True
is_sync_model_supported(dict)  # False
```

## Reactive Fields

Model fields can be reactive types themselves, and reactivity keeps flowing down:

```python title="main.py" hl_lines="13 19"
from typing import Literal

from pydantic import BaseModel
from syncwave import SyncList, Syncwave

syncwave = Syncwave()


class Settings(BaseModel):
    volume: int = 8
    brightness: int = 5
    theme: Literal["light", "dark", "system"] = "system"
    recent_files: SyncList[str] = []


SyncSettings = syncwave.make_reactive(Settings)
settings = syncwave.create_store(SyncSettings, name="settings")

settings.recent_files.append("notes.txt")
```

```json title="syncstores/settings.json"
{
  "volume": 8,
  "brightness": 5,
  "theme": "system",
  "recent_files": ["notes.txt"]
}
```

The list field has a default too, so the store still doesn't need a `default` parameter. `recent_files` holds a real `SyncList`, and reading the field hands you that live object, not a copy. Everything from [Collections](./collections/) applies to it: mutate it in place, keep a reference to it, and it stays synchronized.

The nesting rule is also the one you already know: the reactive chain must be unbroken. A reactive field only works inside a class that was itself made reactive, and Syncwave rejects the type upfront otherwise:

```python
class Broken(BaseModel):
    recent_files: SyncList[str]


syncwave.create_store(Broken, name="broken")  # never made reactive
```

```console
TypeError: `SyncList` cannot be used here: Field `recent_files` in `Broken` cannot be reactive because it is not contained in a `SyncModel` (breaks the reactive chain).
```

Fields can hold other reactive models too. Let's nest a window inside the settings:

```python
class Window(BaseModel):
    width: int = 1280
    height: int = 720


SyncWindow = syncwave.make_reactive(Window)


class Settings(BaseModel):
    volume: int = 8
    window: SyncWindow = Window()


SyncSettings = syncwave.make_reactive(Settings)
settings = syncwave.create_store(SyncSettings, name="settings")

settings.window.width = 1920
```

`settings.window.width = 1920` is validated and synced like any other change, two levels down.

And of course, plain field types (`str`, `list[str]`, a model never made reactive) are always valid. The chain simply ends at them, with the usual pattern: assign to the field to change it, and read it fresh through the model when you need the current value.

## Models in Collections

The composition works in the other direction too, and it's probably the most common way to use a reactive model: as the element type of a reactive collection. The classic shape is a store of records keyed by an id:

```python title="main.py" hl_lines="13 15 16"
from pydantic import BaseModel
from syncwave import SyncDict, Syncwave

syncwave = Syncwave()


class Customer(BaseModel):
    name: str
    age: int


SyncCustomer = syncwave.make_reactive(Customer)
customers = syncwave.create_store(SyncDict[int, SyncCustomer], name="customers")

customers[1] = Customer(name="Alice", age=30)
customers[2] = {"name": "Bob", "age": 25}  # a dict is validated into a Customer

customers[1].age = 31  # validated, written to the file
```

```json title="syncstores/customers.json"
{
  "1": {
    "name": "Alice",
    "age": 31
  },
  "2": {
    "name": "Bob",
    "age": 25
  }
}
```

(The integer keys land in the file as strings, as covered in [Key Types](./collections/#key-types).)

References work the way you'd expect from the collections: `alice = customers[1]` stays connected to whatever lives under key `1`, in both directions, for as long as the entry exists. `SyncList[SyncCustomer]` works the same way, and the nesting can go as deep as you need.

## Single Values with `RootModel`

Every store on this page and the previous one held a container or a class with fields. At the other extreme, [Syncwave](./syncwave/) showed stores holding a single plain value, like one `str`, with the usual plain-type pattern: change it by assigning through the instance, read it fresh through the instance.

You might wish for a reactive handle on such a store: an object you could keep in a variable, mutate to change the single value, and print to see the current one (if you know Vue.js, something like `ref()`). Syncwave doesn't ship a dedicated type for this, say a hypothetical `SyncValue`, because that handle already exists. Reactivity is a chain with the `syncwave` instance at its root, so for a single-value store the instance is the handle: `syncwave["locale"] = ...` writes the value, `syncwave["locale"]` reads it.

If you want the object anyway, Pydantic already has it. A [`RootModel`](https://pydantic.dev/docs/validation/latest/concepts/models/#rootmodel-and-custom-root-types) wraps a single value in a model with one field named `root`, and `make_reactive` supports it out of the box:

```python title="main.py" hl_lines="10 13"
from pydantic import RootModel
from syncwave import Syncwave

syncwave = Syncwave()


class Locale(RootModel[str]): ...


SyncLocale = syncwave.make_reactive(Locale)
locale = syncwave.create_store(SyncLocale, name="locale")

locale.root = "en-US"  # validated, synced
print(locale.root)  # en-US
```

No `default` was needed here: the store started as an empty string, the inferred empty value for a `str` root. And the JSON file holds the bare value, not an object around it:

```json title="syncstores/locale.json"
"en-US"
```

So `locale` is exactly that hypothetical `SyncValue`: a live handle on a single string, following the file in both directions like every reactive object. Assigning through the instance still works too; `syncwave["locale"] = "fr-FR"` updates the same object in place.

## The `register` Shorthand

Making a class reactive and storing its instances in a reactive collection is so common that there's a decorator for it. `@syncwave.register` combines `make_reactive` and `create_store` in one step:

```python title="main.py" hl_lines="7"
from pydantic import BaseModel
from syncwave import Syncwave

syncwave = Syncwave()


@syncwave.register(name="customers")
class Customer(BaseModel):
    name: str
    age: int


customers = syncwave["customers"]
customers.append(Customer(name="Alice", age=30))

alice = customers[0]
alice.age = 31  # validated, written to the file
```

This is equivalent to:

```python
SyncCustomer = syncwave.make_reactive(Customer)
customers = syncwave.create_store(SyncList[SyncCustomer], name="customers")
```

Unlike `make_reactive`, the decorator returns your class **unchanged**. You keep using `Customer` everywhere (type hints, instantiation, `isinstance` checks) as if Syncwave weren't involved; the reactive class is derived internally, and the instances you get back from the store are instances of both.

### Collection Wrapping

You may have noticed that the store above is a _list_ of customers even though nothing declared a collection. That's the decorator's `collection` parameter at work. Its default value `"auto"` picks a wrapping based on the shape of your model:

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
```

Note that the dictionary key and the model's `key` field are independent: the field only influences which wrapping `"auto"` picks, and it is up to you to keep the two consistent.

You can also override the auto behavior explicitly:

- `collection=SyncDict` or `collection=SyncList` forces the wrapping (a `key` field, if any, is ignored). `SyncDict` accepts one type argument for the key here, e.g. `collection=SyncDict[int]`; without it, the key type defaults to `str`.
- `collection=None` disables wrapping: the store holds a single instance, like the `settings` store [earlier](#making-a-syncmodel).

`SyncSet` is not an option: sets can only hold hashable items, and reactive models are mutable, as explained in [Hashable Items](./collections/#hashable-items).

## Restrictions

- **Frozen models cannot be made reactive.** A frozen model can never change, so there is nothing to sync; attempting it raises a `TypeError`. Use frozen models as regular (plain) values inside a store instead. Individual frozen fields are rejected for the same reason.
- **Tracked fields cannot be deleted.** `del alice.age` raises an `AttributeError` telling you to set the field to `None` instead (if the type allows it), because a missing field cannot be represented consistently in the JSON.
- **Defaults must be valid.** Pydantic doesn't validate a field's default, but Syncwave does as soon as the model is made reactive, so `brightness: int = "hello"` raises a `ValueError` right away, as does a default whose validators produce a value the field cannot serialize. Default factories are not called upfront, since that could have side effects; an invalid factory result is caught when its value would reach a file.
- **Validators and serializers must be pure and idempotent**, and fields with `exclude=True` cannot be used. Syncwave re-validates and re-serializes values many times, as explained in [Types and Validation](./types_and_validation/#validators-and-serializers-must-be-pure).

## What's Next

With models covered, you now know all the reactive types. What remains is the system they form: what exactly a reference points to over time, when it stops being valid, and how to tell (`sync_live`, `DeadReferenceError`). [Reactivity](./reactivity/) covers that lifecycle.

For the complete API of reactive models, see the [API Reference](../api/sync_model/).
