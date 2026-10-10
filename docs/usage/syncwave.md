# Syncwave

??? abstract "API Reference"

    [`syncwave.Syncwave`](../api/syncwave/)

This page covers the basics: creating stores, changing their data from Python and from the JSON file, and how validation protects both sides. It closes with a first look at **reactive types**.

## The `syncwave` Instance

!!! success "First, make sure Syncwave is installed"

    See [Installation](../#installation) if you haven't done that yet.

The `Syncwave` class is the entry point of the library. You create an instance and use it for everything else.

Create a file `main.py` with:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()
```

The `syncwave` instance behaves like a Python `dict`, where each key maps to a **store**. A store is simply a value that is persisted to its own JSON file and kept in sync with it.

By default, the JSON files live in a directory named `syncstores/` next to where you run your program. The directory is created automatically if it doesn't exist. You can choose another location with `Syncwave(root_path="path/to/dir")`.

If you like database analogies: the `syncstores/` directory is your database, each JSON file is a table, and the `syncwave` instance is your connection. Except you don't need a server, a driver, or an ORM; Syncwave plays all of those roles at once.

## Create a Store

You interact with `syncwave` using the familiar `dict` [operations](https://docs.python.org/3/library/stdtypes.html#typesmapping). The one exception is inserting a new key, i.e. creating a store:

```python title="main.py" hl_lines="5"
from syncwave import Syncwave

syncwave = Syncwave()

syncwave["numbers"] = [1, 2, 3]
```

This raises an error:

```console
KeyError: "Store 'numbers' does not exist. Use `syncwave.create_store(...)`, or `@syncwave.store(...)` first."
```

You cannot insert a value under a key that doesn't exist because Syncwave needs additional information, most importantly a **type** the data must conform to. The correct way to create a store is to use [`syncwave.create_store(...)`](../api/syncwave/#syncwave.Syncwave.create_store): that's where you pass that type, along with other optional parameters to configure the store[^1].

[^1]: Configuration parameters are not implemented yet. They will be introduced in subsequent versions of the library.

Create your first store like this:

```python title="main.py" hl_lines="5 6"
from syncwave import Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(list[int], name="numbers")
print(numbers)
```

Three things to notice:

- `list[int]` declares the type of the store: a list of integers.
- `name="numbers"` gives the store its key. It determines how you access it (`syncwave["numbers"]`) and the name of its JSON file (`syncstores/numbers.json`).
- `create_store` returns the store's initial value, which is assigned to `numbers`.

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

### The `default` Parameter

The method `create_store` has a parameter `default` that you can use to pass an initial value, used when the file doesn't exist (or is empty):

```python
syncwave.create_store(list[int], name="numbers", default=[1, 2, 3])
```

However, since the file `syncstores/numbers.json` already contains data, `default` is ignored; the file always wins.

For some store types, passing a `default` value is mandatory. That's the case when no "empty" value (`{}`, `[]`, `""`, or `None`) fits the type. For example, if the store is a simple `int`, you must specify the initial value:

```python
syncwave.create_store(int, name="counter", default=0)
```

[Types and Validation](./types_and_validation/) covers default values in more detail.

??? note "Why a store can never be empty"

    A key aspect of Syncwave is creating a mapping between Python and JSON. A value in Python must have a corresponding JSON value, and vice-versa. For instance:

    | Python | JSON   |
    | ------ | ------ |
    | `dict` | `{}`   |
    | `list` | `[]`   |
    | `str`  | `""`   |
    | `None` | `null` |

    However, what about an empty JSON file? Python doesn't have something like `undefined` that could represent the absence of a value. What value should then be in `syncwave["empty_file"]`? `None` already represents a file containing `null`, and besides, `None` wouldn't even be a valid value for a store declared as `list[int]`.

    For that reason, as soon as you create a store it must be filled with something, and that thing must be a valid value with respect to the store's type.

### Managing Stores

The only `dict` operations rejected are those that would insert a key that doesn't exist yet, whether explicitly, like `syncwave["new_key"] = [1, 2, 3]`, or implicitly, like calling `syncwave.update(...)` with new keys. Everything else from the `dict` interface works as expected.

```python
len(syncwave)  # number of stores
list(syncwave)  # store names
"numbers" in syncwave  # membership test

del syncwave["numbers"]  # deletes the store and its JSON file
```

Be careful with that last one, and with operations such as `pop` and `clear`: deleting a store also deletes its JSON file from disk and **all data will be lost!**[^2]

[^2]:
    Whether the file gets deleted or not when deleting a store will become configurable in
    future versions of the library.

## Change the Data from Python

A store is changed by assigning a new value to it, exactly like setting a key in a `dict`:

```python title="main.py" hl_lines="6"
from syncwave import Syncwave

syncwave = Syncwave()

syncwave.create_store(list[int], name="numbers")
syncwave["numbers"] = [7, 8, 9]
print(syncwave["numbers"])  # [7, 8, 9]
```

Run the program, then open the JSON file:

```json title="syncstores/numbers.json"
[7, 8, 9]
```

The `[1, 2, 3]` you wrote earlier is gone, and the file now holds the new data. There was no save call and no query. You assigned a value, and the file was updated.

Note that we update the store through the `syncwave` instance, like `syncwave["numbers"] = [7, 8, 9]`, and not with an in-place mutation, like `syncwave["numbers"].append(4)`. This matters, and you'll see why in the next section.

## Copies on Read and Write

!!! info "TL;DR"

    Syncwave copies values before handing them over when you _read_ from a store, and copies values on the way in, when you _write_ to a store.

    In practice, that means:

      1. Read values from `syncwave["numbers"]`, not from a variable like `numbers` which is just a snapshot.
      2. Write values to `syncwave["numbers"]`, e.g. `syncwave["numbers"] = [1, 2, 3]`. In-place mutations like `numbers.append(4)` won't reach the store.

    These rules only apply to normal types: **[reactive types](#why-reactivity)** lift both restrictions.

Usually, defining a variable creates a reference, not a copy:

```python
initial_list = [1, 2, 3]
ref = initial_list
ref.append(4)
print(initial_list)  # [1, 2, 3, 4]
```

But that's not how it works in Syncwave:

```python
syncwave["numbers"] = [1, 2, 3]
numbers = syncwave["numbers"]
numbers.append(4)
print(syncwave["numbers"])  # still [1, 2, 3]
```

That's because every time you read the store, Syncwave hands you a copy, not a reference to its internal object. Whether you read the store like `numbers = syncwave["numbers"]`, or `numbers = syncwave.create_store(list[int], name="numbers")` like in the first example, the variable `numbers` holds a snapshot of the store.

In either case, it's a plain Python list:

```python
print(type(numbers))  # <class 'list'>
```

This means there's no way for Syncwave to know when `numbers` is mutated: a normal `list` has no hook Syncwave could use to update the JSON file when it changes. The `Syncwave` class, on the other hand, is built for that: when an operation like `syncwave["numbers"] = [1, 2, 3]` happens, Syncwave is well aware, and has the opportunity to **react**.

??? note "`append` directly?"

    You may be tempted to try something like this, without the intermediate variable:

    ```python
    syncwave["numbers"] = [1, 2, 3]
    syncwave["numbers"].append(4)
    print(syncwave["numbers"])  # [1, 2, 3]
    ```

    But that doesn't change anything; the line `syncwave["numbers"].append(4)` still reads from the store before calling `append` on the returned value, which again is a copy.

Copies happen on the write side as well. Consider this:

```python
numbers = [1, 2, 3]
syncwave["numbers"] = numbers
numbers.append(4)
print(syncwave["numbers"])  # still [1, 2, 3]
```

We _first_ create `numbers`, and after it's assigned we `append` a value to it. However, this mutation doesn't reach the store either because Syncwave copies values on their way in as well.

Why is all that copying needed?

Because Syncwave must _own_ its data to be able to keep it synchronized with the JSON files. If outside code could keep a reference to an internal value, an in-place change Syncwave can't detect would silently break the synchronization. Copying at both boundaries (read and write) prevents that.

Admittedly, that's annoying. It would be much nicer if `numbers` was not just a snapshot, but followed the store's content, and if you could mutate it in place to update the store.

!!! tip "That's what reactivity offers"

    Think of it as a spreadsheet where the cell `A1` holds some data. Without reactivity, creating the `numbers` variable is like copy-pasting that data into `B1`. It would be much better to use the formula `=A1`, so that `B1` follows `A1` forever.

    To achieve that, you need [reactive types](#why-reactivity). With them, a variable like `numbers` not only tracks the store, but can also be used to mutate it.

Until we get there, we will refrain from keeping variables such as `numbers`, and always go through the `syncwave` instance instead to read and write.

## Change the Data from the File

Synchronization also works in the other direction, which is arguably the more interesting half. As long as your program is running, Syncwave watches the JSON file and applies external changes to the store.

To see it in action, make the program wait so you have time to edit the file:

```python title="main.py" hl_lines="6 8 9"
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
    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing
```

Validation is powered by [Pydantic](https://docs.pydantic.dev/latest/), so you get the exact same behavior and error messages you may already know. [Types and Validation](./types_and_validation/) covers store types in depth.

The file is protected too. If you (or anything else) write invalid data into `syncstores/numbers.json` while the program runs, Syncwave rejects the change and reverts the file to its last valid state. To see it, run the program from the previous section again. This time, while it waits, put invalid data in the file, like the string `"hello"`:

```json title="syncstores/numbers.json"
[1, 2, "hello"]
```

Save it and keep an eye on the file: it quickly snaps back to `[1, 2, 3]`. Press ++enter++ and the program prints `[1, 2, 3]` as well; the bad data never reached the store.

Finally, if the file contains invalid data while the program is not running, you will get a `ValueError` the next time it runs, when `create_store` tries to load the file.

The store never holds data that doesn't match its type, no matter where the change comes from.

### Richer Types

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

### Skipping Validation

The store type can also be as loose as you want. `typing.Any` accepts any JSON-serializable data, which effectively skips validation:

```python
from typing import Any

syncwave.create_store(Any, name="anything")
syncwave["anything"] = {"anything": [1, "two", None]}
```

You lose the guarantees that come with a real type, but the two-way sync works exactly the same. This is handy for prototyping, or for data whose shape you genuinely don't control.

## Why Reactivity

Everything on this page followed the same pattern: you read a store's current value through the `syncwave` instance, and you change it by assigning a whole new value through the instance. That's because Syncwave has no way to detect in-place changes to plain Python objects. It can't know that someone called `append` on a regular list (short of comparing the whole content over and over), so the only operations it can react to are the ones that go through the instance.

That word, _react_, is the heart of the library. An object is **reactive** when it stays connected to the store data: changes made through it are detected, validated, and written to the JSON file, and changes coming from the file are applied to it. The `syncwave` instance is itself reactive, which is why the assignment pattern works. Its entries are whole stores, though. To get the same behavior for values _inside_ a store, Syncwave provides its own reactive objects.

Here is the earlier example again, rewritten with a `SyncList`, the reactive counterpart of `list`:

```python title="main.py" hl_lines="1 5"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(SyncList[int], name="numbers")
```

The store type is now `SyncList[int]`. The value returned by `create_store` is kept in the `numbers` variable again, but this time it's not just a snapshot of the store, it's a reactive object:

```python
print(type(numbers))  # <class 'syncwave.sync_collection.SyncList'>
```

`SyncList` is a subclass of the [`Reactive`](../api/reactive/) class. You can use it for checks such as `isinstance(numbers, Reactive)`, which returns `True`.

Now, `numbers` will stay synchronized with the store when it changes:

```python
syncwave["numbers"] = []
print(numbers)  # []

syncwave["numbers"] = [1, 2, 3]
print(numbers)  # [1, 2, 3]
```

Notice the assignment didn't disconnect `numbers`. Instead of replacing its internal object with a new one, Syncwave updated it in place, so references like `numbers` stay connected.

The same goes for writing to the store. In-place mutations reach the store, whether you do them on `syncwave["numbers"]` or on `numbers`; no assignment needed:

```python
syncwave["numbers"].append(4)
print(numbers)  # [1, 2, 3, 4]

numbers.append(5)
print(syncwave["numbers"])  # [1, 2, 3, 4, 5]
```

As always, the corresponding JSON file follows. At that point, `syncstores/numbers.json` would be:

```json title="syncstores/numbers.json"
[1, 2, 3, 4, 5]
```

Of course, the other direction works too: a change originating from the file propagates into `numbers`. Change the example to:

```python title="main.py" hl_lines="6 8 9"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(SyncList[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]

input("Edit syncstores/numbers.json, save it, then press Enter... ")
print(numbers)
```

Note that we print `numbers` and not `syncwave["numbers"]`.

Run the program. While it waits at the prompt, edit the file, save it, and press ++enter++:

```console
$ python main.py
Edit syncstores/numbers.json, save it, then press Enter...
[1, 2, 3, 100]
```

A reactive object stays connected in both directions, and this holds for values nested arbitrarily deep inside a store.

You can now see why it's convenient that `create_store` returns the initial value right when you create the store. Its usefulness was limited with plain types, but with a reactive type it hands you a live handle on the store: you can mutate it to change the data, and it always shows the current state. This is not special to `create_store`; a regular read like `numbers = syncwave["numbers"]` gives you the same object.

!!! info "Terminology"

    Throughout the documentation, the word _reactive_ is used interchangeably with the prefix _sync-_. For example, saying "a reactive list" refers to a `SyncList`.

Read [Collections](./collections/) and [Models](./models/) to learn how to use all the reactive types. Then, [Reactivity](./reactivity/) explains the system as a whole.

For the complete API of the `Syncwave` class, see the [API Reference](../api/syncwave/).
