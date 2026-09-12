# JSON Files

This page covers the disk side of Syncwave: where the files are, how writes happen, how to interact with the files safely from your own code, and what happens when things go wrong on the file system.

## One File per Store

Each store is persisted to a single JSON file at `<root_path>/<name>.json`. The store name doubles as the file name, so it must be a valid one (no path separators, no reserved names). Syncwave validates this when the store is created.

The root directory is set when creating the instance and defaults to `syncstores/` relative to the current working directory:

```python
syncwave = Syncwave()  # ./syncstores/
syncwave = Syncwave(root_path="~/data")  # user expansion
syncwave = Syncwave(root_path="$APP_DIR/data")  # environment variables
```

The path is normalized (made absolute, `~` and environment variables expanded) and the directory is created if needed. The resolved path is available as `syncwave.root_path`.

## How Writes Happen

Two properties of Syncwave's writes shape how you should interact with the files.

**Writes are debounced.** Rapid successive changes are batched, and the file is written a fraction of a second after the last one. Ten `append` calls in a row produce one write, not ten. The trade-off: at any given instant, the file may lag slightly behind the in-memory state.

**Writes are atomic.** Syncwave writes to a temporary file first, then atomically replaces the target. A reader can never observe a half-written file, even if the program crashes at the wrong moment. It sees either the previous content or the new content.

The files themselves are standard JSON, indented with two spaces. They are meant to be opened and edited.

## Reading and Writing Safely from Code

!!! danger "To Change"

    Maybe add in there a comment about why read_store_json and write_store_json should rarely be used: Syncwave guarantees that the data in JSON is the same as the in-memory data, just not *right* away. So why would you need to read/write the file directly in your python program? If you're doing so there's a good chance that you're doing something wrong; just read the value in the appropriate python object instead. These method mostly exist for demo purposes I wrote them when I wanted to write an example showing that the content of the file was actually what I claimed it was, and that writing to the file directly reflected in the python object, but then it actually didn't work (because of debounce/batching/etc.) so I had to introduce a bunch of timers everywhere and it was ugly. Anyway, so aside from the demo, and maybe debugging (although that is also suspect because you're to actually just reading/writing the file raw), these methods should probably not be used.

The debouncing above creates a small race window if your _program_ interacts with the files directly. Reading the file right after a change may return stale content; writing to the file races against a pending Syncwave write. The instance provides two methods that close these windows:

```python
numbers = syncwave.create_store(SyncList[int], name="numbers")
numbers.extend([1, 2, 3])

path = syncwave.root_path / "numbers.json"
print(path.read_text())  # may still show "[]"
print(syncwave.read_store_json("numbers"))  # always shows "[1, 2, 3]"

syncwave.write_store_json("numbers", "[9, 8, 7]")
print(numbers)  # immediately shows [9, 8, 7]
```

[read_store_json](../api/syncwave/#syncwave.Syncwave.read_store_json) returns the up-to-date content, including changes not yet flushed to disk. [write_store_json](../api/syncwave/#syncwave.Syncwave.write_store_json) writes the text and applies it to the store immediately, skipping the file-watching delay.

!!! danger "Old"

    These are for programmatic access. A human editing the file in an editor doesn't need any of
    this; that path is handled by the file watching described next.

That said, you should rarely need these methods. Syncwave already guarantees that the file and the in-memory data converge, so if your program wants the current data, the store's value _is_ the current data. Read the Python object instead of the file. If these methods show up in regular application code, there's a good chance the file is being used as a communication channel where the store should be. Their legitimate uses are demos and debugging; this documentation uses them to show file contents without sprinkling `sleep` calls everywhere.

A human editing the file in an editor doesn't need any of this either; that path is handled by the file watching described next.

## External Changes

While your program runs, Syncwave watches each store's file. When something else modifies it, the new content is read, validated, and applied to the store, usually within a fraction of a second. Like Syncwave's own writes, incoming changes are debounced, so a burst of rapid edits is processed once.

If the new content is invalid (malformed JSON, valid JSON that doesn't match the store's type, or a missing field whose default turns out to be invalid), the change is rejected and the file is reverted to the last valid state, as described in [Types and Validation](./types_and_validation/).

## Deletions Are Repaired

!!! danger "Old"

    Deleting a file out from under a running program is not a valid way to delete a store. Syncwave

treats it as an accident and repairs it: the file is recreated from the in-memory value.

Deleting a file out from under a running program is not a valid way to delete a store. Syncwave treats it as an accident and repairs it: the file is recreated from the in-memory value.

!!! note "Planned"

    This behavior is likely to become configurable in a future release: restore the file (the current behavior), or treat the deletion as a request to delete the store.

```python
syncwave.create_store(list[int], name="tough")
syncwave["tough"] = [7]

# someone deletes syncstores/tough.json...
# ...and moments later it's back, containing [7]
```

The same goes for the root directory itself: if the whole `syncstores/` directory is deleted while the program runs, Syncwave recreates it along with the files of every store.

The intended way to delete a store is through the instance, which removes both the store and its file:

```python
del syncwave["tough"]
```

After your program exits, the files are just files. Keep them, version them, or delete them; the next run picks up whatever is there.
