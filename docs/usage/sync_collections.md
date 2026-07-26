# Sync Collections

The sync collections are the reactive counterparts of Python's built-in containers: `SyncDict` for `dict`, `SyncList` for `list`, and `SyncSet` for `set`. They behave like the originals, with one addition: every change is validated against the collection's type, written to the store's JSON file, and external changes to the file are applied to the collection in place.

If you read [Syncwave](./syncwave/), you saw that stores holding plain types can only be changed by reassigning a whole new value. Sync collections lift that restriction. You work with the collection directly — `append`, `add`, item assignment — and Syncwave takes care of the rest.

All three types share the `SyncCollection` virtual base class, which is useful for `isinstance` checks.

## You Never Instantiate Them

Sync collections cannot be created directly; calling `SyncList()` raises an error. Instead, you declare them in a store's type and Syncwave creates them for you:

```python
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

tags = syncwave.create_store(SyncList[str], name="tags")
```

Plain values are accepted everywhere and converted automatically. If a store expects a `SyncList[int]` somewhere, you can assign a regular `[1, 2]` to that spot: Syncwave validates it and turns it into a live `SyncList`. You will see this at work in the nesting section below.

## SyncList

`SyncList` works like a `list`. It is parameterized with the type of its items:

```python
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

tags = syncwave.create_store(SyncList[str], name="tags")
tags.append("python")
tags.extend(["json", "sync"])

print(tags)     # [python, json, sync]
print(tags[0])  # python
print(len(tags))

del tags[0]
print(tags)  # [json, sync]
```

Each of these mutations is written to `syncstores/tags.json`:

```json title="syncstores/tags.json"
[
  "json",
  "sync"
]
```

!!! note "Slices are not supported yet"

    Indexing works with integers, but slice operations like `tags[1:3]` are not supported for now.

## SyncDict

`SyncDict` works like a `dict`. It is parameterized with a key type and a value type:

```python
from syncwave import SyncDict, Syncwave

syncwave = Syncwave()

settings = syncwave.create_store(SyncDict[str, int], name="settings")
settings["volume"] = 8
settings["brightness"] = 5

print(settings)  # {'volume': 8, 'brightness': 5}
```

```json title="syncstores/settings.json"
{
  "volume": 8,
  "brightness": 5
}
```

### Key Types

JSON object keys are always strings, so a `SyncDict` key type must serialize to a string and parse back to the same value. Syncwave checks this when the store is created. Supported key types include `str`, `int`, `float`, `bool`, `UUID`, dates and times, IP addresses, `enum.Enum`, and `typing.Literal` — the full list is in [Types and Validation](./types_and_validation/#dictionary-keys).

Non-string keys are converted transparently. With a `SyncDict[int, str]`, you keep using integers in Python while the file stores strings:

```python
scores = syncwave.create_store(SyncDict[int, str], name="scores")
scores[1] = "one"
print(scores[1])  # one
```

```json title="syncstores/scores.json"
{
  "1": "one"
}
```

## SyncSet

`SyncSet` works like a `set`: items are unique and unordered. Since JSON has no set type, the file representation is an array:

```python
from syncwave import SyncSet, Syncwave

syncwave = Syncwave()

ids = syncwave.create_store(SyncSet[int], name="ids")
ids.add(1)
ids.add(2)
ids.add(1)  # already present, no effect

print(ids)  # {1, 2}
```

```json title="syncstores/ids.json"
[
  1,
  2
]
```

Set items must be hashable, and Syncwave enforces this on the item type when the store is created. In particular, a `SyncSet` cannot contain other reactive objects: reactive objects are mutable, and mutable objects are not hashable. If you write duplicate values into the JSON file by hand, they collapse into one item when loaded.

## Nesting

Sync collections compose. A store can be a dictionary of lists, a list of lists, and so on, and reactivity reaches all the way down:

```python
from syncwave import SyncDict, SyncList, Syncwave

syncwave = Syncwave()

playlists = syncwave.create_store(SyncDict[str, SyncList[str]], name="playlists")

playlists["road_trip"] = []  # a plain list, converted to a SyncList
playlists["road_trip"].append("Bohemian Rhapsody")  # reactive, synced

print(playlists)  # {'road_trip': [Bohemian Rhapsody]}
```

```json title="syncstores/playlists.json"
{
  "road_trip": [
    "Bohemian Rhapsody"
  ]
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

The reverse is fine: a plain type inside a reactive collection (like `SyncList[list[int]]`) is valid, but the inner values are ordinary Python objects with the usual restriction — change them by assignment, not in place.

## References Stay Connected

Because reactive objects are updated in place rather than replaced, you can keep a reference to any part of a store and it stays connected:

```python
road_trip = playlists["road_trip"]
road_trip.append("Hotel California")  # synced, same as through `playlists`
```

A reference remains valid until the value it points to is removed from the store. At that point the object is disconnected — its `sync_live` property turns `False`, and any further operation raises `DeadReferenceError`:

```python
print(road_trip.sync_live)  # True
del playlists["road_trip"]
print(road_trip.sync_live)  # False

road_trip.append("x")
```

```console
syncwave.reactive.DeadReferenceError: Operation attempted on a dead reference: <SyncList [] (dead)>
```

How references behave over time — what exactly they point to, and what kills them — is covered in depth in [Reactivity](./reactivity/).

## What's Next

Collections cover structured data, but the items themselves are still plain values or nested collections. To give the items a schema of their own, Syncwave can make your Pydantic models reactive as well — that is the topic of [Sync Models](./sync_models/). For the complete API of the types presented here, see the [API Reference](../api/sync_collections/).
