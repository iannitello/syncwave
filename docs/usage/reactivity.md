# Reactivity

This page explains the reactive system. The three previous pages, [Syncwave](./syncwave/), [Collections](./collections/), and [Models](./models/), already cover most of it, but this page puts everything in one place and adds some details.

## Core Concept

In Syncwave, reactivity is the property that keeps an object synchronized with a JSON file, in both directions.

Concretely, if an object is reactive, mutating it triggers a write to its associated file, and changes to the file update that object in place. This means that reactivity can only apply to _mutable containers_.

A single value, like an `int` or a `str`, can't be mutated. The string `"hello world"` can't change: its methods return new strings, for example `upper()` returns the new string `"HELLO WORLD"`, but they never mutate the original. Not all containers are mutable either; the most obvious example is a `tuple`.

Python's built-in mutable containers, like `list` or `dict`, aren't reactive, and there's no way to make them reactive: they have no hook Syncwave could use to detect a change. Syncwave has to offer its own types.

## The Reactive Types

Here is the list of all reactive types:

- The `Syncwave` class itself.
- The three `SyncCollection` types:
    - `SyncDict`, the reactive equivalent of a `dict`
    - `SyncList`, the reactive equivalent of a `list`
    - `SyncSet`, the reactive equivalent of a `set`
- User-defined models:
    - Subclasses of `SyncModel`, the reactive equivalent of Pydantic's `BaseModel`.
    - Subclasses of `SyncRoot`, the reactive equivalent of Pydantic's `RootModel`.
    - Classes decorated with `@sync_dataclass`, the reactive equivalent of Pydantic dataclasses.

All of them, except reactive dataclasses, are subclasses of the [`Reactive`](../api/reactive/) base class. You'll mostly use it for runtime checks. The three collections also share the virtual base class `SyncCollection`:

```python
isinstance(SyncList(), Reactive)  # True
issubclass(SyncList, SyncCollection)  # True
issubclass(SyncCollection, Reactive)  # True
```

A class decorated with `@sync_dataclass` keeps its own class hierarchy, so it doesn't inherit from `Reactive`. To check whether a class is a reactive dataclass, use [`is_sync_dataclass`](./models/#reactive-dataclasses) instead.

### More Types

The list of reactive types might seem short, but there isn't much room for more types. A reactive type has to be mutable, since its whole point is to be changed in place, and among the containers that can be represented in JSON, `dict`, `list`, and `set` are just about the only general-purpose mutable ones Python has.

The JSON side is already fully covered: the ones Syncwave offers already cover everything that can be described with [JSON Schema](./json_files/#json-schema). New types could offer different Python behaviors, but they would have to share a JSON representation with an existing one. That's already the case with `SyncList` and `SyncSet`: both become JSON arrays, and what differs is how you work with the data in Python.

You may also feel like there should be a reactive type to wrap single values, a hypothetical `SyncValue`, a bit like `ref()` in Vue.js. You're in luck: Pydantic already has `RootModel`, and its reactive counterpart, [`SyncRoot`](./models/#single-values-with-syncroot), offers exactly that. But even if it didn't, you could always use the `syncwave` instance as the parent of a single value: `syncwave["locale"] = "en-US"`. That brings us to the next subject: the reactive chain.

## The Reactive Chain

A store is always part of a reactive chain. Even if the store itself is not a reactive type, e.g. a single value like a `str`, or a non-reactive container like a `list`, it's still held by the `syncwave` instance, which _is_ reactive.

Reactive types compose: they can be nested inside each other, with two exceptions. `Syncwave` is always at the root and can't be used deeper, and a `SyncSet` can't hold reactive items (see [Hashable Items](./collections/#hashable-items)), so it always ends the chain. A `SyncDict` holding `SyncList`s holding reactive models is reactive at every level, and a change anywhere in the tree is synced.

The chain must be unbroken: a reactive type can't sit inside a non-reactive container, because the plain container would swallow the changes. Syncwave enforces this when the store is created:

```python
syncwave.create_store(list[SyncList[int]], name="bad")
```

```console
TypeError: `SyncList` cannot be used here: `list` is not a reactive container.
```

Models follow the same rule. A regular `BaseModel` is a non-reactive container, so giving it a reactive field breaks the chain, and Syncwave rejects it when the store is created too (see [Reactive Fields](./models/#reactive-fields)).

### Non-Reactive Leaves

The reactive chain always ends with non-reactive leaves. These can be simple values such as the `int` in a `SyncList[int]`, ordinary containers such as the `list[int]` in a `SyncDict[str, list[int]]`, or anything else that isn't reactive.

Syncwave copies non-reactive values when you read or write them. [Copies on Read and Write](./syncwave/#copies-on-read-and-write) explains it for stores, but it happens at every level, not just at the `syncwave` root.

For example, in a `SyncDict[str, list[int]]` store, reading one of the nested lists returns a copy, and inserting one copies it too:

```python
store = syncwave.create_store(SyncDict[str, list[int]], name="store")

# read
store["key"] = [1, 2, 3]
store["key"].append(4)  # `append` on a copy
print(store["key"])  # still [1, 2, 3]

# write
my_list = [1, 2, 3]
store["key"] = my_list  # copied on the way in
my_list.append(4)  # doesn't mutate the store
print(store["key"])  # still [1, 2, 3]
```

A copy you read is a regular Python object. It doesn't follow the store, and nothing ever marks it as outdated, not even when the value is deleted from the store. When you need the current data, read it again through its closest reactive parent. To change it, assign a new value through that same parent.

## References

A **reference** is any variable pointing to an object. When that object is reactive, the reference is a live handle on a store: you can mutate it to change the data, and it always shows the current state. You can keep a reference to a whole store or to a value nested inside one:

```python
customers = syncwave["customers"]  # reference to a store
alice = customers["alice"]  # reference to a nested value
```

Unlike non-reactive leaves, reactive values aren't copied when you read them. You get the store's own object, so `customers["alice"] is alice` is `True`.

Syncwave guarantees that a live reference is always synchronized with the file, in both directions. You can pass `alice` to a function or keep it around for the lifetime of your program. Mutations through it are persisted, and external changes show up in it.

## Updates Happen In Place

When a reactive value is replaced, whether by an assignment in Python (like `syncwave["numbers"] = [1, 2, 3]`) or by an edit to the JSON file, Syncwave doesn't rebuild it from scratch. It walks the existing objects and updates them **in place**. Your references keep pointing to the same Python objects, and those objects now hold the new data.

In-place updates raise the question of **identity**: when the data changes, which old object corresponds to which new value? Syncwave uses the only anchors available:

- **`SyncDict` values are identified by key.** A reference to `store["alice"]` represents whatever is stored under `"alice"`.
- **`SyncList` items are identified by position.** A reference to `store[0]` represents whatever is at index 0. See [Position-Based Identity](./collections/#position-based-identity).
- **Model fields are identified by name.** A reference to a reactive field represents whatever that field holds.

## Lifecycle and `SyncState`

A reactive object is always in one of the three states of the `SyncState` enum: `SyncState.INERT`, `SyncState.LIVE`, or `SyncState.DEAD`. The only exception is the `syncwave` instance, which is always live.

The **inert** state happens when you manually create an instance of a reactive type, for example:

```python
SyncList([1, 2, 3])  # <[1, 2, 3] (inert)>, acts like [1, 2, 3]
```

Inert objects are detached: they don't belong to any store. You can think of an inert reactive object as its non-reactive counterpart (a `list` in this case). That goes for validation too. `SyncList[int](["a"])` is accepted, and assigning a field of an inert model isn't validated, unless the model sets `validate_assignment=True` like any Pydantic model would.

An inert object never becomes live (or dead): inserting one in a store is like inserting a normal object, with the usual rule that it gets copied on the way in. The object you hold stays inert.

You get a reactive object in the **live** state by reading it from a store. As long as the object is live, each mutation is validated and updates the JSON file, and each edit to the file updates the object in place.

You never need to build live objects yourself. When plain data enters a store, Syncwave creates the reactive objects its type calls for, at every level. In a `SyncDict[str, SyncList[str]]` store, `playlists["synthwave"] = []` stores a live `SyncList`, so a following `playlists["synthwave"].append("Nightcall")` is synced. See [Deep Instance Creation](./collections/#deep-instance-creation).

Inserting a live object in a store, whether it's the store it belongs to or another one, is like inserting an inert object: it's copied, and only its values are used. For example:

```python
list1 = syncwave.create_store(SyncList[int], name="list1")
list2 = syncwave.create_store(SyncList[int], name="list2")

syncwave["list1"] = [1, 2, 3]
print(list1)  # [1, 2, 3]
print(list2)  # []

syncwave["list2"] = list1
print(list1)  # [1, 2, 3]
print(list2)  # [1, 2, 3]

list1.append(4)
print(list1)  # [1, 2, 3, 4]
print(list2)  # [1, 2, 3]
```

A live object becomes **dead** when it's removed from a store. That can happen in a few different situations:

- **Deleting the whole store.** `del syncwave["store"]` deletes the whole thing, so the reference to the store itself and any reference to a reactive value nested inside it are killed.
- **Deleting part of a store.** Say you have a store defined as `SyncDict[str, Customer]`: a reference kept to a specific customer becomes dead when its key is removed from the store. This happens regardless of whether the deletion comes from Python or from a file edit. With a `SyncList`, remember that references follow positions: deleting an item shifts the following ones, so it's the reference to the last position that dies (see [Updates Happen In Place](#updates-happen-in-place)).
- **Changing an item's type.** That's a bit more niche, but it can happen with a union involving reactive types. For example, take a `SyncList[int | SyncList[int]]` store: an element of the outer list could be a live `SyncList[int]` at a given moment, and a reference to it becomes dead when the item at that index changes to a plain `int`. This also happens between two reactive types, e.g. `SyncList | SyncDict`: if the type changes, the reference is killed. A reactive object never morphs into another type. See [Union Types](#union-types).

When an object dies, every reactive object nested inside it dies too.

A dead object is permanently disconnected and isn't meant to be used anymore. Operations on it raise a `DeadReferenceError` rather than letting it silently drift away from the file, which would be a hard bug to track down. You can still print it and check its state (see below). Inserting a dead object into a store raises the same error.

### `sync_state` and `sync_live`

All reactive objects have the `sync_state` and `sync_live` properties.

`sync_state` returns one of the members of `SyncState`. The state also appears in the `repr` of a reactive object. For example, a live `SyncList` is represented as `<[1, 2, 3] (live)>`.

`sync_live` returns a boolean: `True` if the state is live, `False` otherwise.

## Union Types

Union types are fully supported. They can be used for a whole store or for elements nested in a store:

```python
syncwave.create_store(SyncList[int] | SyncDict[str, int], name="store_union")
syncwave.create_store(SyncList[int | SyncList[int]], name="nested_union")
```

Without a `default`, a store starts with the first empty value that fits, in the order `{}`, `[]`, `""`, `None`. `store_union` would start as an empty `SyncDict` in the example below, even though `SyncList` comes first in the union.

When the type stays the same but the data changes, the existing object is updated in place and references stay live, like in any other store. When the type changes, the old object is killed and a new one takes its place. References to the old object are dead, and you need to read the store again to get the new live one.

```python
ref = syncwave["store_union"]  # currently a SyncDict

syncwave["store_union"] = {"one": 1}  # same type
print(ref.sync_live)  # True
syncwave["store_union"] = [1, 2]  # type switch: SyncDict -> SyncList
print(ref.sync_live)  # False
```

The order of the members matters when several of them accept the same data. Take a store declared as `list[int] | SyncList[int]`: any valid `list[int]` is also a valid `SyncList[int]`, and vice-versa. When two members both fit, the first one wins, so this store always holds a plain `list` and never a `SyncList`, even if you assign a `SyncList` to it. Declared as `SyncList[int] | list[int]`, it's the opposite: the store is always a `SyncList`, and the `list[int]` member is never used.

## What's Next

You have now seen how to use the library, all reactive types, and the system as a whole. [JSON Files](./json_files/) covers additional details about the disk side of the synchronization, with more advanced topics.
