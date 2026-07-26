# Syncwave

The `Syncwave` class is the entry point of the library: every store you create, read, or change goes through it. This page presents the class and its instance — how stores are created, how data flows between Python and the JSON files, and how validation protects both sides.

!!! note "Before you start"

    Make sure Syncwave is installed. See [Installation](../#installation) if you haven't done that yet.

## Create a `Syncwave` Instance

Create a file `main.py` with:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()
```

The `Syncwave` instance is the entry point of the library. If you like database analogies, think of it as your connection: everything goes through it.

The instance behaves like a Python `dict`, where each key maps to a **store**. A store is simply a value that is persisted to its own JSON file and kept in sync with it.

By default, the JSON files live in a `syncstores/` directory next to where you run your program. The directory is created automatically if it doesn't exist. You can choose another location with `Syncwave(root_path="path/to/dir")`.

## Create a Store

Now create your first store:

```python title="main.py" hl_lines="6"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
```

Two things are happening in this single line:

- `list[int]` declares the **type** of the store: a list of integers. Syncwave uses this type to validate everything that goes in, from either direction.
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

Some types have no natural empty value. An empty JSON array is a valid `list[int]`, but nothing obvious exists for `int`. For those stores, provide the initial value yourself:

```python
syncwave.create_store(int, name="counter", default=0)
```

The `default` is only used when the file doesn't exist or is empty. If the file already contains data, the file wins.

## Change the Data from Python

A store holding a plain type like `list[int]` is changed by assigning a new value to it, exactly like setting a key in a `dict`:

```python title="main.py" hl_lines="7 8"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]
print(syncwave["numbers"])  # [1, 2, 3]
```

Run the program again. Then open the JSON file:

```json title="syncstores/numbers.json"
[
  1,
  2,
  3
]
```

No save call, no commit, no query. You assigned a value, and the file followed.

!!! warning "Change the whole store, not the list in place"

    `syncwave["numbers"]` is a regular Python list. It is not aware of Syncwave, so an in-place change like `syncwave["numbers"].append(4)` happens in memory only: Syncwave cannot detect it, and the file is not updated. With plain types, always assign a whole new value to the store, as shown above. Reactive types remove this restriction, and you will meet them at the end of this page.

!!! tip "Writes are debounced"

    Syncwave batches rapid successive changes and writes them to disk a fraction of a second later. If your program needs to read the file content at an exact moment, use [read_store_json](../api/syncwave/#syncwave.Syncwave.read_store_json), which always returns the up-to-date content. Everything about how Syncwave interacts with the disk is covered in [JSON Files](./json_files/).

## Change the Data from the File

Synchronization works in the other direction too, and this is where Syncwave really starts to feel different. As long as your program is running, Syncwave watches the JSON file and applies external changes to the store.

To see it in action, make the program wait so you have time to edit the file:

```python title="main.py" hl_lines="9 10"
from syncwave import Syncwave

syncwave = Syncwave()


syncwave.create_store(list[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]

input("Edit syncstores/numbers.json, save it, then press Enter... ")
print(syncwave["numbers"])
```

Run the program. While it waits, open `syncstores/numbers.json` in your editor and change it to:

```json title="syncstores/numbers.json"
[
  1,
  2,
  3,
  100
]
```

Save the file. Go back to the terminal and press ++enter++:

```console
$ python main.py
Edit syncstores/numbers.json, save it, then press Enter...
[1, 2, 3, 100]
```

The store picked up the change. It doesn't matter who edits the file: you in a text editor, another program, a script. Syncwave keeps both sides in sync.

Notice that the code reads `syncwave["numbers"]` again instead of keeping the list in a variable. An external change replaces the store's value with a new list, so a variable saved earlier would still point to the old one. With plain types, always go through the instance.

## Validation, Both Ways

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

## Manage Stores

Since `Syncwave` is a `MutableMapping`, the standard `dict` interface works on the instance:

```python
len(syncwave)          # number of stores
list(syncwave)         # store names
"numbers" in syncwave  # membership test

del syncwave["numbers"]
```

Be careful with that last one: deleting a store also deletes its JSON file from disk.

## Why Reactive Types

Everything on this page followed the same pattern: to change a store, you assign a whole new value through the instance. That is not a stylistic choice, it is a consequence of how Python works. A plain `list` has no way to tell Syncwave that someone called `append` on it, so the only change Syncwave can observe is the assignment itself.

Reactive types are Syncwave's answer. `SyncList`, `SyncDict`, and `SyncSet` are counterparts of `list`, `dict`, and `set` (and your Pydantic models can become reactive too) that stay connected to their store. They behave like the originals, but every in-place change is detected, validated, and written to the file:

```python
numbers = syncwave.create_store(SyncList[int], name="numbers")
numbers.append(1)  # synced to syncstores/numbers.json, no reassignment needed
```

The connection also works in the other direction: when the file changes, reactive objects are updated in place rather than replaced, so references you hold — even to values nested deep inside a store — remain valid and current.

Continue with [Sync Collections](./sync_collections/) to meet the reactive collections, and [Reactivity](./reactivity/) for how the reactive system works as a whole. For the complete API of the class presented here, see the [API Reference](../api/syncwave/).
