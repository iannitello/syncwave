# Collections

??? abstract "API Reference"

    [`syncwave.SyncCollection`](../api/sync_collections/)

The [previous page](./syncwave/) showed the limitations of a store holding a plain `list`:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(list[int], name="numbers")

numbers.append(1)              # changes a copy, not the store
syncwave["numbers"].append(2)  # also a copy

print(syncwave["numbers"])  # still []
```

Reads hand out copies, and in-place changes never reach the store. With plain types, you change a store by assigning a whole new value, and you see the current data by reading fresh through the instance. If you haven't read [Syncwave](./syncwave/) yet, it's the best place to start; this page builds on it.

Now declare the same store with `SyncList`, the reactive counterpart of `list`:

```python title="main.py" hl_lines="1 5"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(SyncList[int], name="numbers")

numbers.append(1)
syncwave["numbers"].append(2)

print(syncwave["numbers"])  # [1, 2]
print(numbers)              # [1, 2]
```

Both `append` calls reached the store, `syncstores/numbers.json` holds `[1, 2]`, and `numbers` is a live object that follows every change, including edits made to the file while the program runs.

### The `SyncCollection` Types

`SyncList` is one of three reactive collections, the counterparts of Python's built-in containers:

- `SyncDict` for `dict`
- `SyncList` for `list`
- `SyncSet` for `set`

They behave like the originals, except that every change is validated against the declared type and synced to the store's JSON file, in both directions.

The three of them share a common (virtual) base class, `SyncCollection`, which is mostly for static typing and for runtime checks:

```python
isinstance(numbers, SyncCollection)   # True
issubclass(SyncList, SyncCollection)  # True
issubclass(SyncCollection, Reactive)  # True
```

!!! note "Only three collection types?"

    It might seem like a short list, but it goes further than it looks. A reactive type has to be mutable, since its whole point is to be changed in place, and among the containers that can be represented in JSON, `dict`, `list`, and `set` are just about the only general-purpose mutable ones Python has. `tuple`, for example, is immutable, so a reactive counterpart of it couldn't exist. [The JSON Schema Foundation](./types_and_validation/#the-json-schema-foundation) covers the bigger picture of what maps between Python and JSON.

## Syncwave Creates the Instances

You never call a reactive collection's constructor yourself. Try it:

```python
from syncwave import SyncDict

settings = SyncDict()
```

```console
TypeError: `SyncDict` cannot be instantiated directly. Reactive instances are created automatically when a value enters a store.
```

As the message says, instances are created by Syncwave. Declare the collection in a store's type, and the store's value comes back as a live instance:

```python
from syncwave import SyncDict, Syncwave

syncwave = Syncwave()

settings = syncwave.create_store(SyncDict[str, int], name="settings")
print(type(settings))  # <class 'syncwave.sync_collection.SyncDict'>
```

To bring your own data in, use plain values: assign a regular `dict` or `list` where a reactive collection is expected, and Syncwave validates it and converts it to a live instance. You'll see this at work throughout the page.

??? note "Why instantiation is blocked"

    A reactive object only makes sense when it's tied to a store: mutating it must update a file somewhere. A free-floating `SyncDict()` would have no store behind it, so what should a mutation do? It could start out inert and connect when inserted into a store, but then what if you insert the same instance into two stores? Every answer creates complications, and allowing it would buy you nothing: to prepare data before it enters a store, a plain `dict` already does the job, and Syncwave converts it on the way in.

## SyncDict

`SyncDict` is the reactive counterpart of `dict`, parameterized with a key type and a value type. Let's use the `settings` store from above to hold the settings of a small app (keep it around, we'll come back to it at the end of the page):

```python title="main.py"
from syncwave import SyncDict, Syncwave

syncwave = Syncwave()

settings = syncwave.create_store(SyncDict[str, int], name="settings")
settings["volume"] = 8
settings["brightness"] = 5

print(settings)  # {'volume': 8, 'brightness': 5}
```

Run the program, then open the file:

```json title="syncstores/settings.json"
{
  "volume": 8,
  "brightness": 5
}
```

The usual `dict` operations (`get`, `items`, `update`, deletions, iteration, and so on) all work, and every mutation is synced.

### Key Types

A `SyncDict` maps to a JSON object, and JSON object keys are always strings. That puts two requirements on the key type. It must be hashable, like any Python dict key. And it must round-trip through a string: serialize to a string, and parse back from that string to an equal value. Syncwave checks the key type when the store is created.

`str` qualifies trivially, but so do `int`, `float`, `UUID`, `datetime`, and quite a few others; the full list is in [Types and Validation](./types_and_validation/#dictionary-keys). The conversion is transparent. With a `SyncDict[int, list[int]]` you use integer keys in Python, while the file stores strings:

```python
dag = syncwave.create_store(SyncDict[int, list[int]], name="dag")
dag[1] = [2, 3]
print(dag[1])  # [2, 3]
```

```json title="syncstores/dag.json"
{
  "1": [2, 3]
}
```

The round-trip requirement is also what rules some types out. Take a union:

```python
syncwave.create_store(SyncDict[int | str, int], name="ambiguous")
```

`int` and `str` are both valid key types on their own, but together they make every key ambiguous: when Syncwave reads `"1"` from the file, should it become `store[1]` or `store["1"]`? There's no way to tell, so the type is rejected when the store is created. `typing.Any` is excluded for the same reason.

One last detail: a bare `SyncDict`, without type parameters, is interpreted as `SyncDict[str, Any]`. The values can be anything, but the keys stay strings, since `Any` can't be a key type.

## SyncList

`SyncList` is the reactive counterpart of `list`, parameterized with the type of its items:

```python title="main.py"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

tags = syncwave.create_store(SyncList[str], name="tags")
tags.append("python")
tags.extend(["json", "docs"])
print(tags)  # [python, json, docs]

del tags[0]
print(tags)  # [json, docs]
```

Every mutation lands in the file, so after the `del` it reads:

```json title="syncstores/tags.json"
["json", "docs"]
```

!!! warning "Slices are not supported yet"

    Indexing works with integers, but slice operations like `tags[1:3]` are not supported for now.

One property of `SyncList` to keep in mind: it identifies its items by position, not by content. In a store like this one it makes no difference, since reading `tags[0]` hands you a snapshot string anyway. It starts to matter when the items are themselves reactive, which brings us to nesting.

## Nesting

Reactive collections compose. A store can be a dictionary of lists, a list of lists, and so on, and reactivity reaches all the way down:

```python title="main.py"
from syncwave import SyncDict, SyncList, Syncwave

syncwave = Syncwave()

playlists = syncwave.create_store(SyncDict[str, SyncList[str]], name="playlists")

playlists["road_trip"] = []  # a regular list, converted to a SyncList
playlists["road_trip"].append("Bohemian Rhapsody")  # reactive, synced

print(playlists)  # {'road_trip': [Bohemian Rhapsody]}
```

```json title="syncstores/playlists.json"
{
  "road_trip": ["Bohemian Rhapsody"]
}
```

Note how the plain `[]` assigned to `playlists["road_trip"]` came back as a live `SyncList`: the conversion described earlier happens at every level, not just at the store root.

One rule to keep in mind: the chain of reactive types must be unbroken from the store root down. A reactive collection cannot live inside a plain one, because the plain container would swallow the changes. Syncwave rejects such types upfront:

```python
syncwave.create_store(list[SyncList[int]], name="bad")
```

```console
TypeError: `SyncList` cannot be used here: `list` is not a reactive container.
```

The reverse is fine: a plain type inside a reactive collection (like `SyncList[list[int]]`) is valid, the chain simply ends there. The inner lists are ordinary Python objects with the usual restriction: change them by assignment, not in place.

## SyncSet

`SyncSet` is the reactive counterpart of `set`: items are unique and unordered. JSON has no set type, so the file representation is an array:

```python title="main.py"
from syncwave import SyncSet, Syncwave

syncwave = Syncwave()

ids = syncwave.create_store(SyncSet[int], name="ids")
ids.add(1)
ids.add(2)
ids.add(1)  # already present, no effect

print(ids)  # {1, 2}
```

```json title="syncstores/ids.json"
[1, 2]
```

If you write duplicate values into the file by hand, they collapse into one item when loaded.

Like a regular `set`, a `SyncSet` requires its items to be hashable, and Syncwave enforces this on the item type when the store is created:

```python
syncwave.create_store(SyncSet[list[int]], name="bad")
```

```console
TypeError: `SyncSet` must hold hashable elements, got `list`.
```

This requirement makes `SyncSet` the exception to nesting. Hashable values are immutable, while a reactive object must be mutable so it can be updated in place; no type can be both. So a `SyncSet` can hold `int`, `str`, `UUID`, and other hashable values, but never another reactive type. It always ends the reactive chain.

## Keeping References

With nesting, a store becomes a tree of reactive objects, and any of them can be held in a variable, no matter how deep it sits:

```python
road_trip = playlists["road_trip"]
road_trip.append("Hotel California")  # synced, same as through `playlists`
```

Just like a store-level variable, `road_trip` stays connected in both directions for as long as the value lives in the store.

What exactly does such a reference point to? For a `SyncDict`, values are identified by key: `playlists["road_trip"]` represents whatever is stored under `"road_trip"`. For a `SyncList`, items are identified by position, as mentioned earlier, and that can be surprising:

```python
matrix = syncwave.create_store(SyncList[SyncList[int]], name="matrix")
matrix.append([1, 2])

first = matrix[0]
print(first)  # [1, 2]

matrix.insert(0, [9, 9])
print(first)  # [9, 9]
```

`first` is a reference to position 0, not to the `[1, 2]` object. The insert shifted everything, position 0 came to hold `[9, 9]`, and `first` followed. If your items have natural identifiers, a `SyncDict` keyed on them gives you references that follow the entity instead of the position.

A reference stays live until the value it points to is removed from the store. Every reactive object has a `sync_live` property telling you which side of that line it's on:

```python
print(road_trip.sync_live)  # True
del playlists["road_trip"]
print(road_trip.sync_live)  # False
```

Once removed, the object is permanently disconnected, and any further operation on it raises a `DeadReferenceError`:

```python
road_trip.append("x")
```

```console
syncwave.reactive.DeadReferenceError: Operation attempted on a dead reference: <SyncList [] (dead)>
```

None of this is specific to collections: `sync_live`, `DeadReferenceError`, and the identity rules apply to every reactive object, including the models of the next page. [Reactivity](./reactivity/) covers the whole lifecycle in depth.

## What's Next

The `settings` store from earlier has a weakness. `SyncDict[str, int]` works while every setting is an `int`, but settings rarely stay that uniform. Say you add a `theme` that must be one of `"light"`, `"dark"`, or `"system"`: no dict type fits anymore. The best you can do is loosen the store to `SyncDict[str, Any]`, which accepts your theme along with everything else, including a typo like `settings["thme"]`.

What that store really needs is a fixed set of named fields, each with its own type. That's what a **model** is, and Syncwave can make your Pydantic models reactive just like the collections on this page: assigning a field is validated and written to the file, and fields can themselves hold reactive collections, so the whole structure stays reactive all the way down. [Models](./models/) covers it.

For the complete API of the types presented on this page, see the [API Reference](../api/sync_collections/).
