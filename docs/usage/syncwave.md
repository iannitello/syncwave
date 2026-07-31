# Syncwave

??? abstract "API Documentation"

    [`syncwave.Syncwave`][syncwave.Syncwave]

The `Syncwave` class is the entry point of the library. You create an instance and use it for everything else. This page covers the basics: creating stores, changing their data from Python and from the JSON file, and how validation works.

!!! success "First, make sure Syncwave is installed"

    See [Installation](../#installation) if you haven't done that yet.

Create a file `main.py` with:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()
```

The instance behaves like a Python `dict` (it's actually a [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html)), where each key maps to a **store**. A store is simply a value that is persisted to its own JSON file and kept in sync with it.

By default, the JSON files live in a directory named `syncstores/` next to where you run your program. The directory is created automatically if it doesn't exist. You can choose another location with `Syncwave(root_path="path/to/dir")`.

If you like database analogies: the `syncstores/` directory is your database, each JSON file is a table, and the `syncwave` instance is your connection. Except there is no server, no driver, and no ORM; Syncwave plays all of those roles at once.

## Create a Store

You can interact with `syncwave` using the familiar `dict` [operations](https://docs.python.org/3/library/stdtypes.html#typesmapping). The one exception is inserting a new key, i.e. creating a store:

```python title="main.py" hl_lines="6"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave["numbers"] = [1, 2, 3]
```

This raises an error:

```console
KeyError: "Store 'numbers' does not exist. Use `syncwave.create_store(...)`, or `@syncwave.register(...)` first."
```

You cannot just insert a value because in Syncwave, a store is validated against a **type**. `syncwave.create_store(...)` is where you pass that type, along with other parameters to configure the store[^1].

[^1]: Not implemented yet. Configuration parameters will be introduced in subsequent versions of the library.

So let's create your first store the right way:

```python title="main.py" hl_lines="6"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
```

Two things are happening in this line:

- `list[int]` declares the type of the store: a list of integers.
- `name="numbers"` gives the store its key. It determines how you access it (`syncwave["numbers"]`) and the name of its JSON file (`syncstores/numbers.json`).

Run the program:

```bash
python main.py
```

Then look inside the `syncstores/` directory. Syncwave created the file for you:

```json title="syncstores/numbers.json"
[]
```

An empty list of integers is just an empty JSON array. If the file had already existed with data in it, Syncwave would have validated the content and loaded it into the store instead.

The store's initial value is returned by `create_store`. These two lines:

```python
syncwave.create_store(list[int], name="numbers")
numbers = syncwave["numbers"]
```

can be simplified to:

```python
numbers = syncwave.create_store(list[int], name="numbers")
```

However, the examples so far ignored the return value because keeping a reference in these cases was dangerous. You will see why in [Change the Data from Python](#change-the-data-from-python). The value returned by `create_store` becomes useful with [reactive types](#why-reactivity).

### The `default` Parameter

While some types have obvious defaults, that's not always the case. An empty JSON array is a valid `list[int]`, but nothing obvious exists for `int`. For those stores, provide the initial value yourself:

```python
syncwave.create_store(int, name="counter", default=0)
```

The way `default` works might not be what you expect, so here are the exact rules. When a store is created, Syncwave picks the initial value in this order:

1. If the file already contains data, that data is validated and loaded. `default` is ignored.
2. Otherwise, Syncwave tries to infer an empty value that satisfies the type (it tries `{}`, `[]`, `""`, and `None`, in that order). If one fits, it is used and `default` is ignored, even a valid one: a `list[int]` store always starts as `[]`, and passing `default=[1, 2, 3]` changes nothing.
3. Only when nothing can be inferred does `default` come into play. If you didn't provide one, the creation fails with a `ValueError`.

In short, `default` only matters for types with no obvious empty value, like the `int` above. [Types and Validation](./types_and_validation/) covers initial values in more detail.

??? note "Additional details"

    A key aspect of Syncwave is creating a mapping between Python and JSON. For instance, a `list` naturally maps to a JSON array `[]`, a `dict` to a JSON object `{}`, and `None` to a JSON `null`. However, what about an empty JSON file? Python doesn't have something like `undefined` that could represent the absence of a value.

    What value should then be in `syncwave["empty_file"]`? `None` already represents a file containing `null`, and besides, `None` wouldn't even be a valid value for a store declared as `list[int]`.

    For that reason, as soon as you create a store it must be filled with something, and that thing must be a valid value with respect to the store's type.

## Change the Data from Python

A store is changed by assigning a new value to it, exactly like setting a key in a `dict`:

```python title="main.py" hl_lines="7 8"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]
print(syncwave["numbers"])  # [1, 2, 3]
```

Run the program again. Then open the JSON file:

```json title="syncstores/numbers.json"
[1, 2, 3]
```

There is no save call and no query. You assigned a value, and the file was updated.

!!! danger "Important: Watch for in-place mutations and stale references"

    The value stored at `syncwave["numbers"]` is a regular Python list. It is not aware of Syncwave, so an in-place change like `syncwave["numbers"].append(4)` happens in memory only: Syncwave cannot detect it, and the file is not updated.

    Be careful with taking references as well: `numbers = syncwave["numbers"]` or `numbers = syncwave.create_store(...)`. The variable `numbers` will not stay synchronized with either the JSON file or the value at `syncwave["numbers"]`.

    Always assign a whole new value to the store, and read the value in `syncwave` as shown above.

    **Fortunately, Syncwave introduces [reactive types](#why-reactivity) that lift these restrictions.** You only have to mind that when dealing with ordinary Python types.

!!! tip "Writes are debounced"

    Syncwave batches rapid successive changes and writes them to disk a fraction of a second later. If you programmatically want to read the file content at an exact moment, use [read_store_json](../api/syncwave/#syncwave.Syncwave.read_store_json), which always returns the up-to-date content. Everything about how Syncwave interacts with the disk is covered in [JSON Files](./json_files/).

## Change the Data from the File

Synchronization also works in the other direction, which is arguably the more interesting half. As long as your program is running, Syncwave watches the JSON file and applies external changes to the store.

To see it in action, make the program wait so you have time to edit the file:

```python title="main.py" hl_lines="9 10"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]

input("Edit syncstores/numbers.json, save it, then press Enter... ")
print(syncwave["numbers"])
```

Run the program. While it waits, open `syncstores/numbers.json` in your editor and change it to something else like:

```json title="syncstores/numbers.json"
[1, 2, 3, 100]
```

Save the file. Go back to the terminal and press ++enter++:

```console
$ python main.py
Edit syncstores/numbers.json, save it, then press Enter...
[1, 2, 3, 100]
```

The store picked up the change. It doesn't matter who edits the file, whether it's you in a text editor or another program entirely. Syncwave keeps both sides in sync.

Notice that the code reads `syncwave["numbers"]` again instead of keeping the list in a variable. An external change replaces the store's value with a new list, so a variable saved earlier would still point to the old one. With plain types (i.e. non-[reactive](#why-reactivity) types), always go through the `Syncwave` instance.

## Validation Both Ways

You declared the store as `list[int]`, and Syncwave holds you to it. Try to assign something that doesn't match:

```python
syncwave["numbers"] = [1, 2, "hello"]
```

```console
pydantic_core._pydantic_core.ValidationError: 1 validation error for list[int]
2
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='hello', input_type=str]
```

Validation is powered by [Pydantic](https://docs.pydantic.dev/latest/), so you get the exact same behavior and error messages you may already know. [Types and Validation](./types_and_validation/) covers store types in depth.

The file is protected too. If you (or anything else) write invalid data into `syncstores/numbers.json`, for example:

```json title="syncstores/numbers.json"
[1, 2, "hello"]
```

Syncwave rejects the change and reverts the file to its last valid state. The store never holds data that doesn't match its type, no matter where the change comes from.

### Union Types and More

Store types go well beyond simple containers. Any type Pydantic can validate and serialize is accepted, for example:

- Unions: `int | str`, `list[int] | int`
- Literals: `Literal["light", "dark"]`
- Enums: any `enum.Enum` subclass
- Constrained types: `Annotated[int, Field(ge=0)]`
- Any composition of the above: `dict[str, list[int] | int]`

Some of these have no empty value Syncwave could infer, so they are also good examples of stores that need a `default`:

```python
from enum import Enum

class Theme(Enum):
    LIGHT = "light"
    DARK = "dark"

syncwave.create_store(Theme, name="theme", default=Theme.LIGHT)
```

See [Types and Validation](./types_and_validation/) for more details.

### Skipping Validation

The store type can also be as loose as you want. `typing.Any` (or equivalently `object`) accepts any JSON-serializable data, which effectively skips validation:

```python
from typing import Any

syncwave.create_store(Any, name="anything")
syncwave["anything"] = {"anything": [1, "two", None]}
```

You lose the guarantees that come with a real type, but the two-way sync works exactly the same. This is handy for prototyping, or for data whose shape you genuinely don't control.

## Manage Stores

Since `Syncwave` is a `MutableMapping`, the standard `dict` interface works on the instance:

```python
len(syncwave)           # number of stores
list(syncwave)          # store names
"numbers" in syncwave   # membership test

del syncwave["numbers"] # delete a store
```

Be careful with that last one: deleting a store also deletes its JSON file from disk and **all data will be lost!**

## Why Reactivity

Everything on this page followed the same pattern: you read a store's current value through the `syncwave` instance, and you change it by assigning a whole new value through the instance. Both rules exist for the same reason: Syncwave has no way to detect in-place changes to plain Python objects. It can't know that someone called `append` on a regular list (short of comparing the whole content over and over), so the only operations it can react to are the ones that go through the instance.

That word, _react_, is the heart of the library. An object is **reactive** when it stays connected to the store data: changes made through it are detected, validated, and written to the JSON file, and changes coming from the file are applied to it. The `Syncwave` instance is itself reactive, which is why the assignment pattern explained on this page works. Its entries are whole stores, though. To get the same behavior for values _inside_ a store, Syncwave provides its own reactive objects.

Here is the earlier example again, rewritten with a `SyncList`, the reactive counterpart of `list`:

```python title="main.py" hl_lines="1 6 8"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()


numbers = syncwave.create_store(SyncList[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]
numbers.append(4)

input("Edit syncstores/numbers.json, save it, then press Enter... ")
print(numbers)
```

The store type is now `SyncList[int]`, and this time the value returned by `create_store` is kept in the `numbers` variable. Doing that was dangerous with a plain list, but it's perfectly fine with a reactive object. You can now see why it's convenient that `create_store` hands you a reference that you can use to edit the JSON file and that gets updated when the file changes.

If you ever need to check, all reactive objects are instances of the [`Reactive`](../api/reactive/) class: `isinstance(numbers, Reactive)` returns `True`.

Run the program. While it waits at the prompt, look at the file:

```json title="syncstores/numbers.json"
[1, 2, 3, 4]
```

Two things happened here. Assigning `[1, 2, 3]` through the instance did not disconnect `numbers`: Syncwave updated the object in place. And the `append` on `numbers`, an in-place change, was detected and written to the file. No assignment needed.

Now edit the file, add a number at the end, save it, and press ++enter++:

```console
$ python main.py
Edit syncstores/numbers.json, save it, then press Enter...
[1, 2, 3, 4, 100]
```

The external change landed in `numbers` too. A reactive reference stays connected in both directions, and this holds for values nested arbitrarily deep inside a store.

!!! info "Terminology"

    Throughout the documentation, the word _reactive_ is used interchangeably with the prefix _sync-_. For example, saying "a reactive list" refers to a `SyncList`.

Read [Sync Collections](./sync_collections/) and [Sync Models](./sync_models/) to learn how to use all the reactive types. Then, [Reactivity](./reactivity/) explains the system as a whole, including what happens to the references you keep.
