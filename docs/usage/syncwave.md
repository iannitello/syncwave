# Syncwave

??? abstract "API Reference"

    [`syncwave.Syncwave`](../api/syncwave)

The `Syncwave` class is the entry point of the library. You create an instance and use it for everything else. This page covers the basics: creating stores, changing their data from Python and from the JSON file, and how validation works.

!!! success "First, make sure Syncwave is installed"

    See [Installation](../#installation) if you haven't done that yet.

Create a file `main.py` with:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()
```

The instance behaves like a Python `dict`, where each key maps to a **store**. A store is simply a value that is persisted to its own JSON file and kept in sync with it.

By default, the JSON files live in a directory named `syncstores/` next to where you run your program. The directory is created automatically if it doesn't exist. You can choose another location with `Syncwave(root_path="path/to/dir")`.

If you like database analogies: the `syncstores/` directory is your database, each JSON file is a table, and the `syncwave` instance is your connection. Except you don't need a server, a driver, or an ORM; Syncwave plays all of those roles at once.

## Create a Store

You interact with `syncwave` using the familiar `dict` [operations](https://docs.python.org/3/library/stdtypes.html#typesmapping). The one exception is inserting a new key, i.e. creating a store:

```python title="main.py" hl_lines="6"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave["numbers"] = [1, 2, 3]
```

This raises an error:

```console
KeyError: "Store 'numbers' does not exist. Use `syncwave.create_store(...)`, or `@syncwave.register(...)` first."
```

You cannot directly insert a value (yet) because Syncwave needs additional information, most importantly a **type** the data must conform to. The correct way to create a store is to use [`syncwave.create_store(...)`](../api/syncwave/#syncwave.Syncwave.create_store): that's where you pass that type, along with other parameters to configure the store[^1].

[^1]: Configuration parameters are not implemented yet. They will be introduced in subsequent versions of the library.

The only operations refused are those that would insert a key that doesn't exist yet, whether explicitly like the assignment above, or implicitly, like calling `syncwave.update(...)` with a new key. Everything else from the `dict` interface works as expected.

So let's create your first store the right way:

```python title="main.py" hl_lines="6 7"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
print(syncwave["numbers"])
```

Two things are happening in the `create_store` call:

- `list[int]` declares the type of the store: a list of integers.
- `name="numbers"` gives the store its key. It determines how you access it (`syncwave["numbers"]`) and the name of its JSON file (`syncstores/numbers.json`).

Run the program:

```console
$ python main.py
[]
```

Then look inside the `syncstores/` directory. Syncwave created the file for you:

```json title="syncstores/numbers.json"
[]
```

Since the file didn't exist, the store started with a default value: an empty list, which in JSON is just an empty array. If the file had already existed with data in it, Syncwave would have validated the content and loaded it into the store instead.

Let's try that. Put some values in `syncstores/numbers.json` and save the file:

```json title="syncstores/numbers.json"
[1, 2, 3]
```

Run the program again:

```console
$ python main.py
[1, 2, 3]
```

Content successfully loaded!

### `create_store` Return Value and Variables

The method `create_store` returns the store's initial value, whether that's the default or the content loaded from the file.

The previous example can be rewritten as:

```python title="main.py" hl_lines="6 7"
from syncwave import Syncwave

syncwave = Syncwave()


numbers = syncwave.create_store(list[int], name="numbers")
print(numbers)
```

However, nothing ties the `numbers` variable to the store; it's just a plain Python list:

```python
print(type(numbers))  # <class 'list'>
```

That means that the value held in `syncwave["numbers"]` (and in the corresponding JSON file) may change over time, but `numbers` won't follow.

This is because on every read operation, Syncwave hands you a copy of the store's value, not a reference. And that's not just the case for the return value of `create_store`; the same is true if you directly create a variable for `syncwave["numbers"]`:

```python
numbers = syncwave["numbers"]
```

A fresh read will always be up to date, but a variable like `numbers` holds a snapshot of the value, not the value itself:

```python
numbers == syncwave["numbers"]  # True
numbers is syncwave["numbers"]  # False
```

Think of it as a spreadsheet where cell `A1` holds some data. Creating the `numbers` variable is like copy-pasting that data into `B1`, but it's not very useful since it's static data. It would be much better to use the formula `=A1` so that `B1` follows `A1` forever. To achieve that, you need [reactive types](#why-reactivity): with them, a variable like `numbers` tracks the store, and it becomes apparent why it's useful to keep the return value of `create_store` in a variable.

Until we get to reactive types, the examples will refrain from keeping such variables and always go through the `syncwave` instance instead.

### The `default` Parameter

While some types have obvious defaults, that's not always the case. An empty JSON array is a valid `list[int]`, but nothing obvious exists for a simple `int`. For those stores, provide the initial value yourself:

```python
syncwave.create_store(int, name="counter", default=0)
```

The way `default` works might not be what you expect, so here are the exact rules. When a store is created, Syncwave picks the initial value in this order:

1. If the file already contains data, that data is validated and loaded. `default` is ignored.
2. Otherwise, Syncwave tries to infer an empty value that satisfies the type (it tries `{}`, `[]`, `""`, and `None`, in that order). If one fits, it is used and `default` is ignored, even a valid one. A `list[int]` store always starts as `[]`, and passing `default=[1, 2, 3]` changes nothing.
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


syncwave.create_store(list[int], name="numbers")  # return value discarded
syncwave["numbers"] = [7, 8, 9]
print(syncwave["numbers"])  # [7, 8, 9]
```

Run the program, then open the JSON file:

```json title="syncstores/numbers.json"
[7, 8, 9]
```

There is no save call and no query. You assigned a value, and the file was updated.

!!! warning "In-place changes don't reach the store"

    Remember that reading a store hands you a copy, so an in-place change like `syncwave["numbers"].append(10)` only modifies that copy. The same goes if you kept a variable: `numbers.append(10)` changes your list, not the store. In both cases, the store and the JSON file are untouched.

    With plain (non-reactive) types, change a store by assigning a whole new value, and read the value fresh through the `syncwave` instance when you need it. [Reactive types](#why-reactivity) lift both restrictions.

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

The file is protected too. If you (or anything else) write invalid data into `syncstores/numbers.json` while the program runs, Syncwave rejects the change and reverts the file to its last valid state. To see it, run the program from the previous section again. This time, while it waits, put invalid data in the file, like the string `"hello"`:

```json title="syncstores/numbers.json"
[1, 2, "hello"]
```

Save it and keep an eye on the file: it quickly snaps back to `[1, 2, 3]`. Press ++enter++ and the program prints `[1, 2, 3]` as well; the bad data never reached the store.

Finally, if the file contains invalid data while the program is not running, you will get a `ValueError` the next time it runs, when `create_store` tries to load the file.

The store never holds data that doesn't match its type, no matter where the change comes from.

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

Everything on this page followed the same pattern: you read a store's current value through the `syncwave` instance, and you change it by assigning a whole new value through the instance. That's because Syncwave has no way to detect in-place changes to plain Python objects. It can't know that someone called `append` on a regular list (short of comparing the whole content over and over), so the only operations it can react to are the ones that go through the instance.[^2]

[^2]: This is also why reads hand you copies. If Syncwave gave you its own internal object, an in-place change would silently knock the store out of sync with the file; a copy keeps the store safe.

That word, _react_, is the heart of the library. An object is **reactive** when it stays connected to the store data: changes made through it are detected, validated, and written to the JSON file, and changes coming from the file are applied to it. The `syncwave` instance is itself reactive, which is why the assignment pattern works. Its entries are whole stores, though. To get the same behavior for values _inside_ a store, Syncwave provides its own reactive objects.

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

The store type is now `SyncList[int]`, and the value returned by `create_store` is kept in the `numbers` variable again. With a plain list, `numbers` would just be a snapshot, but a reactive object stays connected. You can now see why it's convenient that `create_store` hands you a reference that you can use to edit the JSON file and that gets updated when the file changes.

If you ever need to check, all reactive objects are instances of the [`Reactive`](../api/reactive/) class: `isinstance(numbers, Reactive)` returns `True`.

Run the program. While it waits at the prompt, look at the file:

```json title="syncstores/numbers.json"
[1, 2, 3, 4]
```

Two things to note here. Assigning `[1, 2, 3]` through the instance did not disconnect `numbers`: Syncwave updated the object in place. And the `append` on `numbers`, an in-place change, was detected and written to the file. No assignment needed.

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
