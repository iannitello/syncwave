# JSON Files

Up to now, the JSON files stayed in the background: you changed a store, and its file followed. This page looks at that disk side of Syncwave in more detail.

As you've seen, each store lives in its own file, `<root_path>/<name>.json`, where `root_path` defaults to `syncstores/`. The files are standard JSON, and they're meant to be opened and edited.

## JSON Schema

The content of each file can be described by a [JSON Schema](https://json-schema.org), derived from the store's type. In short, JSON Schema is a specification for describing the shape of a JSON document, using JSON itself.

For example, take this Pydantic model:

```python
class Person(BaseModel):
    name: str
    nickname: str | None = None
    age: int
```

The JSON value `{"name": "Alice", "age": 25}` is valid, while `{"name": "Bob", "nickname": "Bobby"}` is not. How can we tell? By looking at the model's JSON Schema, which you can get with `Person.model_json_schema()`:

```json
{
  "properties": {
    "name": { "title": "Name", "type": "string" },
    "nickname": {
      "anyOf": [{ "type": "string" }, { "type": "null" }],
      "default": null,
      "title": "Nickname"
    },
    "age": { "title": "Age", "type": "integer" }
  },
  "required": ["name", "age"],
  "title": "Person",
  "type": "object"
}
```

`age` is listed under `required`, and Bob doesn't have one.

In practice, Syncwave delegates all of this to [Pydantic](https://docs.pydantic.dev/latest/). Any type Pydantic can validate and serialize is a valid store type: primitives, containers, `datetime`, `UUID`, `Enum`, `Literal`, your own models, unions, and any nesting of these. Reactive types like `SyncDict` know how to talk to Pydantic too, so they can be validated and serialized.

The domain of Python objects is vast, while a JSON file can only hold a few kinds of values: objects, arrays, strings, numbers, booleans, and `null`. For instance, the list `[1, 2, 3]` and the set `{1, 2, 3}` both serialize to the array `[1, 2, 3]`, and a `datetime.date` becomes a plain string in the file. What settles the ambiguity is the store's declared type, which tells Pydantic what to rebuild from the file. The JSON alone doesn't carry enough information.

Anyway, if a type can be described by a JSON Schema, it can round-trip between Python and a JSON file, and Syncwave can sync it.

A Python object with no JSON Schema representation (a thread, an open socket) cannot be a store. At some point JSON is simply not rich enough. In practice you'll rarely hit that limit: what JSON can't represent is usually behavior rather than data, and stores are about data.

## How Writes Happen

**Writes are atomic.** Syncwave writes to a temporary file first, then atomically replaces the target. A reader can never observe a half-written file, even if the program crashes at the wrong moment. It sees either the previous content or the new content.

**Writes are debounced.** Rapid successive changes are batched, and a background thread writes the file a fraction of a second after the last one. Ten `append` calls in a row produce one write, not ten.

### Programmatic Reads/Writes

Because of the debouncing, there's a small race window if you interact _programmatically_
directly with the files. The file can lag slightly behind the in-memory state, but it's only
noticeable if your program reads the file itself, right after changing a store:

```python
numbers = syncwave.create_store(SyncList[int], name="numbers")
numbers.append(1)
path = syncwave.root_path / "numbers.json"
print(path.read_text())  # may still show []
```

That's not a problem in practice. Syncwave guarantees that everything stays consistent: the store's value _is_ the current data, and the file reflects that a moment
later. If your program needs the current data, it should read the store, not the file.

The same goes in the other direction: to change the data from your program, change the store. Writing to the file from the program that owns the store races with Syncwave's own writes.

## External Changes

While your program runs, Syncwave watches each store's file. When something else modifies it, the new content is read, validated, and applied to the store. If the new content is invalid (malformed JSON, or valid JSON that doesn't match the store's type), the change is rejected and the file is reverted to the last valid state.

### Deletions Are Repaired

Deleting a file while the program runs is treated as an accident: Syncwave recreates it from the in-memory value.

The intended way to delete a store is to remove its key from the `syncwave` instance, with `del`, `pop`, `clear`, or any other operation that removes keys. That deletes both the store and its file.

## Thread Safety

Syncwave is multi-threaded by nature. One thread watches the files, and others write your changes to disk and apply file edits to the stores. You don't have to do anything about it: stores are thread-safe.

Each store has its own lock, and every change to the store goes through it, whether it comes from your Python code or from the JSON file. An operation like `numbers[0] = 4`, `settings.volume = 7`, or `syncwave["numbers"] = [1, 2, 3]` never sees the store half-updated, so you can use your stores from as many threads as you want[^1].

Creating and deleting stores, on the other hand, isn't thread-safe. A store is a bit like a table in a database: you don't usually create and drop tables on the fly, they're part of your schema. It's the data inside them that changes all the time. Create your stores once, usually at startup.

[^1]:
    `Syncwave`, `SyncDict`, `SyncList`, and `SyncSet` are built on `collections.abc` classes (e.g. `MutableMapping`). Their basic operations are atomic, but mixin methods like `clear` are a series of basic operations (`__delitem__` in the case of `clear`), and the lock is released between each one.

    This is a temporary limitation of the current implementation, and it will be fixed soon.

Inert objects aren't connected to any store, so they're just as thread-safe as their regular counterparts (`list`, `dict`, `BaseModel`, etc.).

Syncwave assumes there's a single `Syncwave` instance per directory. Nothing enforces it, but two instances sharing the same files, in one program or in two, aren't supported.

## Conclusion

That's the end of the usage guide.

When your program isn't running, the files are just JSON files. You can edit them, or do whatever you want, and the next time the program runs it reads them. If the content doesn't match the store's type, `create_store` raises a `ValueError` on that next run.

For the details of every class and method, see the [API Reference](../api/syncwave/).
