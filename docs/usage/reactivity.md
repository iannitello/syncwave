# Reactivity

!!! danger "Old"

    The previous pages introduced the reactive types one by one. This page steps back and explains
    the system they form: what a reference points to over time, and when it stops being valid. After
    this page, the sync behavior should feel predictable instead of magic.

The reactive types ([Collections](./collections/) and [Models](./models/)) each have their own page. This page steps back and explains the system they form: what a reference points to over time, and when it stops being valid. After this page, the sync behavior should feel predictable instead of magic.

## The Reactive Chain

Every store is a tree of values, with the `Syncwave` instance at the root. Reactivity flows down that tree through reactive types: a `SyncDict` holding `SyncList` holding reactive models is reactive at every level, and a change anywhere in the tree is synced.

The chain must be unbroken. A reactive type cannot sit inside a plain container, because the plain container would swallow the changes before Syncwave could see them. Syncwave enforces this when the store is created:

```python
syncwave.create_store(list[SyncList[int]], name="bad")
```

```console
TypeError: `SyncList` cannot be used here: `list` is not a reactive container.
```

The other direction is allowed. A plain type inside a reactive container (`SyncList[list[int]]`) simply ends the chain: the inner lists are ordinary Python objects, changed by assignment like any plain store value.

## References

A **reference** is any variable pointing to a reactive object, either a whole store or a value nested inside one:

```python
customers = syncwave["customers"]  # reference to a store
alice = customers[0]  # reference to a nested value
```

Syncwave guarantees that a live reference is always synchronized with the file, in both directions. You can pass `alice` to a function or keep it around for the lifetime of your program. Mutations through it are persisted, and external changes show up in it.

## Updates Happen In Place

This guarantee relies on one central design decision: when the JSON file changes, Syncwave does not rebuild the store from scratch. It walks the existing objects and updates them **in place**. Your references keep pointing to the same Python objects, and those objects now hold the new data.

In place updates raise the question of **identity**: when the file changes, which old object corresponds to which new value? JSON has no notion of object identity, so Syncwave uses the only anchors available:

- **`SyncDict` values are identified by key.** A reference to `store["alice"]` represents whatever is stored under `"alice"`.
- **`SyncList` items are identified by position.** A reference to `store[0]` represents whatever is at index 0.
- **Model fields are identified by name.** A reference to a reactive field represents whatever that field holds.

The list case can be surprising. Consider a store of reactive `Person` models, and an external edit that inserts a new person at the front of the JSON array:

```python
people = syncwave["people"]
first = people[0]
print(first)  # name='Alice'

# meanwhile, someone edits the file: [{"name": "Zoe"}, {"name": "Alice"}]

print(first)  # name='Zoe'
```

`first` did not keep pointing to Alice. It is a reference to _position 0_, and position 0 now holds Zoe. If your items have a natural identifier, a `SyncDict` keyed on it gives you references that follow the entity instead of the position.

## The Lifecycle of a Reference

A reactive object is **live** from the moment it enters a store until the moment it leaves it. It leaves when:

- its key or index is deleted (`del store["alice"]`, `del items[0]` shifting the last item out),
- the store itself is deleted (`del syncwave["name"]`),
- its slot is replaced by a value of a different type (see [Union Slots](#union-slots) below).

At that point the object is **killed**, along with every reactive object nested inside it. A killed object is permanently disconnected: its `sync_live` property turns `False`, and any operation on it raises `DeadReferenceError`:

```python
road_trip = playlists["road_trip"]
del playlists["road_trip"]

print(road_trip.sync_live)  # False
road_trip.append("x")  # raises DeadReferenceError
```

This is deliberate: a dead reference that kept accepting changes would silently diverge from the file, and that kind of bug is hard to track down. An immediate error is easier to deal with. When in doubt, check `sync_live` before operating on a long-lived reference.

## Union Slots

A store (or a nested slot) can be typed as a union of reactive types:

```python
from typing import Union

from syncwave import SyncDict, SyncList

syncwave.create_store(Union[SyncList[int], SyncDict[str, int]], name="flex")
```

The slot then holds one of the members at a time, and what happens on a change depends on whether the type switches:

- **Same type**: the existing object is updated in place, references stay live.
- **Different type**: the old object is killed and a new one takes its place. References to the old object are dead; read the slot again to get the new one.

```python
ref = syncwave["flex"]  # currently a SyncDict
syncwave["flex"] = [1, 2]  # type switch: SyncDict -> SyncList
print(ref.sync_live)  # False

ref = syncwave["flex"]  # re-read: a live SyncList
syncwave["flex"] = [9, 8]  # same type: updated in place
print(ref.sync_live)  # True
```

## Non-Reactive Leaves

Plain values inside reactive containers (the `int`s in a `SyncList[int]`, or the `list[int]` at the end of a chain) are not reactive objects. They are replaced on change, not updated, and holding a reference to one is just holding a regular Python object: it will not see updates, and nothing marks it as stale. Keep references to reactive objects, and read plain leaves through their reactive parent when you need the current value.

The [Collections](./collections/) and [Models](./models/) pages cover the individual types, and the [API Reference](../api/reactive/) documents `Reactive`, `sync_live`, and `DeadReferenceError`.

<!-- One last case worth considering:

```python title="main.py"
from syncwave import SyncList, Syncwave

syncwave = Syncwave()

list1 = syncwave.create_store(SyncList[int], name="list1")
list2 = syncwave.create_store(SyncList[int], name="list2")

syncwave["list1"] = [1, 2, 3]
print(list1)  # [1, 2, 3]
print(list2)  # []

syncwave["list2"] = list1
print(list1)  # [1, 2, 3]
print(list2)  # [1, 2, 3]

list1.append(101)
list2.append(102)
print(list1)  # [1, 2, 3, 101]
print(list2)  # [1, 2, 3, 102]
```
-->
