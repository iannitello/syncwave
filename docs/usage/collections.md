# Collections

??? abstract "API Reference"

    [`syncwave.SyncCollection`](../api/sync_collections/)

This page introduces the reactive collections: `SyncDict`, `SyncList`, and `SyncSet`. You'll see how Syncwave creates their instances, how they nest into each other, and how each of the three works.

## Why Reactive Collections

The [previous page](./syncwave/) showed the limitations of a store holding a plain `list`:

```python title="main.py"
from syncwave import Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(list[int], name="numbers")

numbers.append(1)              # changes a copy, not the store
syncwave["numbers"].append(2)  # also a copy
print(syncwave["numbers"])     # still []
```

Reads hand out copies, and in-place changes never reach the store. With plain types, the only way to change the store is to assign a whole new value, and you see the current data by reading fresh through the instance.

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

Both `append` calls reached the store and its JSON file, and `numbers` is a live object that follows every change, including edits made to the file while the program runs.

## The `SyncCollection` Types

`SyncList` is one of three reactive collections, the counterparts of Python's built-in containers:

- `SyncDict` for `dict`
- `SyncList` for `list`
- `SyncSet` for `set`

They behave like the originals, except that every change is validated against the declared type and synced to the store's JSON file, in both directions.

The three of them share a common (virtual) base class, `SyncCollection`, which is mostly intended for static typing and for runtime checks, such as:

```python
isinstance(numbers, SyncCollection)   # True
issubclass(SyncList, SyncCollection)  # True
issubclass(SyncCollection, Reactive)  # True
```

??? note "Only three collection types?"

    It might seem like a short list, but it goes further than it looks. A reactive type has to be mutable, since its whole point is to be changed in place, and among the containers that can be represented in JSON, `dict`, `list`, and `set` are just about the only general-purpose mutable ones Python has. `tuple`, for example, is immutable, so a reactive counterpart of it couldn't exist. [The JSON Schema Foundation](./types_and_validation/#the-json-schema-foundation) covers the bigger picture of what maps between Python and JSON.

## Instance Creation

You never call a reactive collection's constructor yourself. Try it to see what happens:

```python title="main.py"
from syncwave import SyncList

numbers = SyncList()
```

```console
TypeError: `SyncList` cannot be instantiated directly. Reactive instances are created automatically when a value enters a store.
```

As the message says, Syncwave automatically creates the instances:

```python title="main.py" hl_lines="6"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(SyncList[int], name="numbers")
print(type(numbers))  # <class 'syncwave.sync_collection.SyncList'>
```

When you call `create_store`, an initial value (from the file or a default) is loaded, and when that value enters the store it becomes a `SyncList`.

If you want to change the whole store, just assign a regular `list` where a reactive collection is expected, and Syncwave knows what to do with it:

```python title="main.py" hl_lines="6"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

numbers = syncwave.create_store(SyncList[int], name="numbers")
syncwave["numbers"] = [1, 2, 3]  # assign a regular list
```

Here `syncwave["numbers"]` was already a `SyncList`, so the assignment didn't create a new one: Syncwave updated the existing instance in place with the new values. That's how the variable `numbers` stays connected; more formally, `numbers is syncwave["numbers"]` is still `True`. See [Updates Happen In Place](./reactivity#updates-happen-in-place) for more details.

??? note "Why instantiation is blocked"

    A reactive object only makes sense when it's tied to a store: mutating it must update a file somewhere. A free-floating `SyncList()` would have no store behind it, so what should a mutation do? It could start out inert and connect when inserted into a store, but then what if you insert the same instance into two stores? Every answer creates complications, and allowing it would buy you nothing: to prepare data before it enters a store, a plain `list` already does the job, and Syncwave converts it on the way in.

This example uses a `SyncList`, but the same is true for the other reactive collections: you never instantiate any of them, and you use their regular counterpart when inserting (`dict` for `SyncDict` and `set` for `SyncSet`).

In practice, this means you won't use the `SyncDict`/`SyncList`/`SyncSet` classes much after store creation. Like `SyncCollection`, they remain useful for static typing and runtime checks, but beyond that, just use the instances like their regular counterparts, keeping in mind that in-place mutations reach the store and that the instances stay synchronized with it.

## Nesting

Reactive collections compose. A store can be a dictionary of lists, a list of sets, and so on, and reactivity reaches all the way down:

```python title="main.py"
from syncwave import SyncDict, SyncList, Syncwave

syncwave = Syncwave()

playlists = syncwave.create_store(SyncDict[str, SyncList[str]], name="playlists")

playlists["synthwave"] = []  # a regular list, converted to a SyncList
playlists["synthwave"].append("Nightcall — Kavinsky")  # reactive, synced

print(playlists)  # {'synthwave': [Nightcall — Kavinsky]}
```

```json title="syncstores/playlists.json"
{
  "synthwave": ["Nightcall — Kavinsky"]
}
```

This is what's generally referred to as "deep reactivity". The store's type is `SyncDict[str, SyncList[str]]`: the outer `SyncDict` holding the playlists is reactive, and so is each individual playlist. That's what lets the `append` call reach the store; it wouldn't have if the store had been declared as `SyncDict[str, list[str]]`.

One rule to keep in mind: the chain of reactive types must be unbroken from the store root down. A reactive collection cannot live inside a plain one, because the plain container would swallow the changes. Syncwave rejects such types upfront:

```python
syncwave.create_store(list[SyncList[int]], name="bad")
```

```console
TypeError: `SyncList` cannot be used here: `list` is not a reactive container.
```

The reverse is fine: a plain type inside a reactive collection (like `SyncList[list[int]]`) is valid; the chain simply ends there. The inner lists are ordinary Python objects with the usual restriction: change them by assignment, not in place.

### Deep Instance Creation

Note how in the above example, the plain `[]` assigned to `playlists["synthwave"]` became a live `SyncList` on the way in. The instance creation described earlier happens at every level, not just at the store root.

This works at any depth, and with more complex data too; the appropriate reactive objects are always constructed on the way in. Say you receive some plain data from elsewhere in your program; you can insert it as is:

```python
incoming_data = {"road_trip": ["Sweet Virginia — The Rolling Stones"]}
playlists.update(incoming_data)
```

Just like that, the new playlist was added to the store as an actual `SyncList`, so it's reactive:

```python
playlists["road_trip"].append("Dreams — Fleetwood Mac")
```

Assuming `syncstores/playlists.json` contained the data from the previous example, it should now be:

```json title="syncstores/playlists.json"
{
  "synthwave": ["Nightcall — Kavinsky"],
  "road_trip": ["Sweet Virginia — The Rolling Stones", "Dreams — Fleetwood Mac"]
}
```

### Keeping Deep References

You already saw that you can keep a whole store in a variable (a reference), like `playlists` above: mutating it updates the file, and it stays synchronized with the file in return.

The same is true for reactive values deeper in the store: with nesting, a store becomes a tree of reactive objects, and any of them can be held in a variable, no matter how deep it sits:

```python
road_trip = playlists["road_trip"]
road_trip.append("Bones — The Killers")  # synced, same as through `playlists`
```

Just like a store-level variable, `road_trip` stays connected in both directions for as long as the value lives in the store. Test it: edit the `"road_trip"` songs in `syncstores/playlists.json` and the `road_trip` variable follows, not just `playlists` or `syncwave["playlists"]`.

This is all pretty intuitive for the items under a `SyncDict`, but it's a bit less so in the case of `SyncList` and `SyncSet`. Make sure you read [Position-Based Identity](#position-based-identity) and [Hashable Items](#hashable-items) (respectively) to understand their less obvious behaviors.

There would be more to say about references, most notably what happens to `road_trip` if you delete the whole entry (either in `playlists` or in the file). However, these concepts apply to all reactive types, not just reactive collections, so let's leave it at that. [Reactivity](./reactivity/) covers everything.

## SyncDict

`SyncDict` is the reactive counterpart of `dict`. It can be parameterized with a key type and a value type, e.g. `SyncDict[str, int]`, or it can be used bare. Here's how we can use it to hold the settings for a small app:

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

A `SyncDict` maps to a JSON object, and the keys of a JSON object are always strings. That puts three requirements on the key type:

1. It must serialize to a string.
2. It must deserialize back to an equal value (it must round-trip through a string).
3. It must be hashable, like any Python `dict` key.

Syncwave checks the key type when the store is created. `str` qualifies trivially, but so do `int`, `float`, `UUID`, `datetime`, and quite a few others; the full list is in [Types and Validation](./types_and_validation/#dictionary-keys).

The conversion is transparent. With a `SyncDict[int, list[int]]` you use integer keys in Python, while the file stores strings. Here's an example of that with a directed acyclic graph (DAG):

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

The round-trip requirement is also what rules out some types. Take a union:

```python
syncwave.create_store(SyncDict[int | str, int], name="ambiguous")
```

`int` and `str` are both valid key types on their own, but together they make every key ambiguous: when Syncwave reads `"1"` from the file, should it become `store[1]` or `store["1"]`? There's no way to tell, so the type is rejected when the store is created. `typing.Any` is excluded for the same reason.

One last detail: a bare `SyncDict`, without type parameters, is interpreted as `SyncDict[str, Any]`. The values can be anything, but the keys stay strings, since `Any` can't be a key type.

## SyncList

`SyncList` is the reactive counterpart of `list`. It can be parameterized with the type of its items, e.g. `SyncList[str]`, or it can be used bare:

```python title="main.py"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

tags = syncwave.create_store(SyncList[str], name="tags")
tags.append("python")
tags.extend(["json", "reactive"])
print(tags)  # [python, json, reactive]

del tags[0]
print(tags)  # [json, reactive]
```

Every mutation lands in the file, so after the `del` it reads:

```json title="syncstores/tags.json"
["json", "reactive"]
```

!!! warning "Slices are not supported yet"

    Slice operations like `tags[1:3]` are not supported for now.

### Position-Based Identity

In Python, the items of a list are identified by position. Nothing surprising when stated like that, but it has a consequence for `SyncList` that you may find counterintuitive: when the items of a `SyncList` are themselves reactive, a reference kept to an item points to a position in the list, regardless of its content.

Let's bring back our music theme to demonstrate:

```python title="main.py"
from syncwave import SyncDict, SyncList, Syncwave

syncwave = Syncwave()

songs = syncwave.create_store(SyncList[SyncDict[str, str]], name="songs")
songs.extend(
    [
        {"title": "Sweet Virginia", "artist": "The Rolling Stones"},
        {"title": "Dreams", "artist": "Fleetwood Mac"},
    ]
)

sweet_virginia = songs[0]  # reference to the song at position 0
songs.insert(0, {"title": "Bones", "artist": "The Killers"})  # takes position 0

print(sweet_virginia)  # wrong song! {'title': Bones, 'artist': The Killers}
```

As soon as another song took the first spot, Syncwave updated the reference based on the position, ignoring the content. There's nothing mechanically wrong with this example, except a poor choice of variable name.

A `SyncList` (especially one holding reactive items) is the appropriate data structure when the items are truly defined by their position. The same example, with a slightly different mental model, makes much more sense:

```python title="main.py"
from syncwave import SyncDict, SyncList, Syncwave

syncwave = Syncwave()

top_songs = syncwave.create_store(SyncList[SyncDict[str, str]], name="top_songs")
top_songs.extend(
    [
        {"title": "Sweet Virginia", "artist": "The Rolling Stones"},
        {"title": "Dreams", "artist": "Fleetwood Mac"},
    ]
)

most_popular_song = top_songs[0]
top_songs.insert(0, {"title": "Bones", "artist": "The Killers"})  # new hit!

print(most_popular_song)  # {'title': Bones, 'artist': The Killers}
```

## SyncSet

`SyncSet` is the reactive counterpart of `set`: items are unique and unordered. It can be parameterized with the type of its items, e.g. `SyncSet[int]`, or it can be used bare. JSON has no set type, so the file representation is an array:

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

### Hashable Items

Like a regular `set`, a `SyncSet` requires its items to be hashable, and Syncwave enforces this on the item type when the store is created:

```python
syncwave.create_store(SyncSet[list[int]], name="bad")
```

```console
TypeError: `SyncSet` must hold hashable elements, got `list`.
```

Notably, this excludes reactive types: hashable values are immutable, while a reactive object must be mutable so it can be updated in place; no type can be both. This makes `SyncSet` the exception to nesting: it can hold `int`, `str`, `UUID`, and other hashable values, but never another reactive type, so it always ends the reactive chain.

## What's Next

The songs from earlier have a weakness. `SyncDict[str, str]` works while every field is a string, but songs rarely stay that uniform. Say each song should also carry its release year: now an `int` needs to fit alongside the strings, and no dict type fits anymore. The best you can do is loosen the items to `SyncDict[str, Any]`, which accepts the year along with everything else, including a typo like `song["titel"]`.

What a song really needs is a fixed set of named fields, each with its own type. That's what a **model** is, and Syncwave can make your Pydantic models reactive just like the collections on this page: assigning a field is validated and written to the file, and fields can themselves hold reactive collections, so the whole structure stays reactive all the way down. [Models](./models/) covers it.

One last thing before you move on: much of what this page introduced is not specific to collections; it applies to all reactive types, `SyncModel` included. You never create the instances yourself, nesting follows the same rules, and references behave the same way. The next page revisits all of it in more detail as it applies to models.

For the complete API of the types presented on this page, see the [API Reference](../api/sync_collections/).
