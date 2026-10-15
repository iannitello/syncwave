# Cheat Sheet

## Basics

<table markdown style="width: 100%;">
<thead>
<tr><th>Item</th><th>Overview</th></tr>
</thead>
<tbody markdown>
<tr markdown>
<td markdown>[Instance](../usage/syncwave/#the-syncwave-instance)</td>
<td markdown="block">

Create an instance:

```python
syncwave = Syncwave()  # default store folder is `./syncstores/`
```

Choose a different location:

```python
syncwave = Syncwave(root_path="path/to/dir")
```

</td>
</tr>
<tr markdown>
<td markdown>[Stores](../usage/syncwave/#create-a-store)</td>
<td markdown="block">

Create a store:

```python
syncwave.create_store(list[int], name="numbers")  # syncstores/numbers.json
```

Default value:

```python
syncwave.create_store(int, name="counter", default=0)
```

Manage stores:

```python
len(syncwave), list(syncwave), "numbers" in syncwave
del syncwave["numbers"]  # deletes the file too!
```

</td>
</tr>
<tr markdown>
<td markdown>[Read and write](../usage/syncwave/#change-the-data-from-python)</td>
<td markdown="block">

```python
syncwave["numbers"] = [1, 2, 3]  # written to the file
syncwave["numbers"]  # [1, 2, 3], updated on file change
```

</td>
</tr>
<tr markdown>
<td markdown>[Types and validation](../usage/syncwave/#validation-both-ways)</td>
<td markdown="block">

Flexible types:

```python
syncwave.create_store(Any, name="anything")  # no validation
syncwave.create_store(dict[str, list[int] | int], name="data")
syncwave.create_store(Literal["light", "dark"], name="theme", default="light")
```

Validated both ways:

```python
syncwave["numbers"] = [1, "two"]  # ValidationError
# invalid edits to the file are reverted
```

</td>
</tr>
<tr markdown>
<td markdown>[Copies (non-reactive types)](../usage/syncwave/#copies-on-read-and-write)</td>
<td markdown="block">

Reading a non-reactive value returns a copy:

```python
numbers = syncwave["numbers"]  # `numbers` is a copy
numbers.append(4)  # doesn't reach the store
syncwave["numbers"].append(4)  # still no change
```

Values are copied on insert as well:

```python
numbers = [1, 2, 3]
syncwave["numbers"] = numbers
numbers.append(4)  # only `numbers` is mutated, not the store or file
```

</td>
</tr>
</tbody>
</table>

## Reactivity

<table markdown style="width: 100%;">
<thead>
<tr><th>Item</th><th>Overview</th></tr>
</thead>
<tbody markdown>
<tr markdown>
<td markdown>[Reactive types](../usage/reactivity/#the-reactive-types)</td>
<td markdown="block">

[`Syncwave`](../usage/syncwave/) is itself reactive.

`dict` -> [`SyncDict`](../usage/collections/#syncdict)

`list` -> [`SyncList`](../usage/collections/#synclist)

`set` -> [`SyncSet`](../usage/collections/#syncset)

`pydantic.BaseModel` -> [`SyncModel`](../usage/models/#the-syncmodel-class)

`pydantic.RootModel` -> [`SyncRoot`](../usage/models/#single-values-with-syncroot)

`pydantic.dataclasses.dataclass` -> [`@sync_dataclass`](../usage/models/#reactive-dataclasses)

</td>
</tr>
<tr markdown>
<td markdown>[Reactive stores](../usage/syncwave/#why-reactivity)</td>
<td markdown="block">

Use a reactive type for the store:

```python
numbers = syncwave.create_store(SyncList[int], name="numbers")
```

Mutate it in place:

```python
numbers.append(4)  # written to the file
syncwave["numbers"].append(5)  # same thing
numbers  # [4, 5], updated on file change
```

</td>
</tr>
<tr markdown>
<td markdown>[Reactive chain](../usage/reactivity/#the-reactive-chain)</td>
<td markdown="block">

Reactive types compose:

```python
SyncDict[str, SyncList[int]]  # nested collections
SyncList[str, Customer]  # reactive model in collection


class Settings(SyncModel):
    recent_files: SyncList[str]  # reactive field
    window: Window  # another reactive model
```

Plain data becomes reactive on the way in, at any depth:

```python
playlists = syncwave.create_store(SyncDict[str, SyncList[str]], name="playlists")
playlists["road_trip"] = []  # stored as a live SyncList
```

Non-reactive values inside a reactive one are copied, like at the store level:

```python
store = syncwave.create_store(SyncDict[str, list[int]], name="store")
store["key"] = [1, 2, 3]
store["key"].append(4)  # a copy, doesn't reach the store
```

</td>
</tr>
<tr markdown>
<td markdown>[Lifecycle](../usage/reactivity/#lifecycle-and-syncstate)</td>
<td markdown="block">

Created by hand, an object is inert and acts like its plain counterpart:

```python
inert = SyncList([1, 2, 3])  # SyncState.INERT
syncwave["numbers"] = inert  # the store keeps a copy, `inert` stays inert
```

Read from a store, it's live:

```python
numbers = syncwave["numbers"]  # SyncState.LIVE
```

Removed from the store, it's dead (and so is everything inside it):

```python
del syncwave["numbers"]  # SyncState.DEAD
numbers.append(4)  # DeadReferenceError
```

Check the state:

```python
numbers.sync_state  # SyncState.INERT, LIVE, or DEAD
numbers.sync_live  # True only if live
```

</td>
</tr>
<tr markdown>
<td markdown>[Unions](../usage/reactivity/#union-types)</td>
<td markdown="block">

```python
syncwave.create_store(SyncList[int] | SyncDict[str, int], name="union")
```

A change of type kills the old object:

```python
ref = syncwave["union"]  # a SyncDict
syncwave["union"] = [1, 2]  # now a SyncList
ref.sync_state  # SyncState.DEAD
ref = syncwave["union"]  # take a new ref, now a live SyncList
```

</td>
</tr>
</tbody>
</table>

## Collections

<table markdown style="width: 100%;">
<thead>
<tr><th>Item</th><th>Overview</th></tr>
</thead>
<tbody markdown>
<tr markdown>
<td markdown>[`SyncDict`](../usage/collections/#syncdict)</td>
<td markdown="block">

```python
settings = syncwave.create_store(SyncDict[str, int], name="settings")
settings["volume"] = 8
```

Keys must round-trip through a JSON string:

```python
SyncDict[int, str]  # fine, keys are strings in the file
SyncDict[int | str, str]  # TypeError, ambiguous keys
SyncDict  # same as SyncDict[str, Any]
```

</td>
</tr>
<tr markdown>
<td markdown>[`SyncList`](../usage/collections/#synclist)</td>
<td markdown="block">

```python
tags = syncwave.create_store(SyncList[str], name="tags")
tags.append("python")
```

References follow positions, not content:

```python
first = songs[0]
songs.insert(0, new_song)
first  # now `new_song`
```

</td>
</tr>
<tr markdown>
<td markdown>[`SyncSet`](../usage/collections/#syncset)</td>
<td markdown="block">

```python
ids = syncwave.create_store(SyncSet[int], name="ids")
ids.add(1)  # a JSON array in the file
```

Items must be hashable, so reactive items aren't allowed:

```python
SyncSet[list[int]]  # TypeError
SyncSet[SyncList[int]]  # TypeError
```

</td>
</tr>
</tbody>
</table>

## Models

<table markdown style="width: 100%;">
<thead>
<tr><th>Item</th><th>Overview</th></tr>
</thead>
<tbody markdown>
<tr markdown>
<td markdown>[`SyncModel`](../usage/models/#the-syncmodel-class)</td>
<td markdown="block">

Subclass `SyncModel` instead of `BaseModel`:

```python
class Settings(SyncModel):
    volume: int = 8
    theme: Literal["light", "dark"] = "light"


settings = syncwave.create_store(Settings, name="settings")
settings.volume = 7  # validated, written to the file
```

Pass a `default` if some fields don't have one:

```python
syncwave.create_store(Settings, name="settings", default={"volume": 8})
```

</td>
</tr>
<tr markdown>
<td markdown>[`store` decorator](../usage/models/#the-store-decorator)</td>
<td markdown="block">

Define a model and create its store in one step:

```python
@syncwave.store(name="customers")
class Customer(SyncModel):
    name: str
    age: int


customers: SyncList[Customer] = syncwave["customers"]
```

By default (`collection="auto"`), the store type is:

- `SyncDict[<key type>, <model>]` if the model has a `key` field
- `SyncList[<model>]` without a `key` field
- `<model>` itself (no wrapping) if it's a `SyncRoot`

Or pick the collection yourself:

```python
collection = SyncDict[int]  # SyncDict[int, <model>]
collection = SyncList  # SyncList[<model>]
collection = None  # <model>, no wrapping
```

</td>
</tr>
<tr markdown>
<td markdown>[`SyncRoot`](../usage/models/#single-values-with-syncroot)</td>
<td markdown="block">

A reactive single value:

```python
locale = syncwave.create_store(SyncRoot[str], name="locale", default="en-US")
locale.root = "fr-CA"  # the file holds "fr-CA"
```

</td>
</tr>
<tr markdown>
<td markdown>[`sync_dataclass`](../usage/models/#reactive-dataclasses)</td>
<td markdown="block">

```python
@sync_dataclass
class Point:
    x: int = 0
    tags: SyncList[str] = field(default_factory=SyncList)


is_sync_dataclass(Point)  # True
```

</td>
</tr>
<tr markdown>
<td markdown>[Restrictions](../usage/models/#restrictions)</td>
<td markdown="block">

- No frozen models or fields.
- Defaults must be valid (checked when the class is defined).
- Validators and serializers must be pure and idempotent.
- No `exclude=True` fields.
- Fields can't be deleted on a live instance.

</td>
</tr>
</tbody>
</table>
