# Reactivity

## 1. Definitions

### Collection

> **Definition**
>
> A **collection** is a type whose associated _JSON Schema_ "type" is "array" (`[]`) or "object" (`{}`).
>
> There are two types of **collections**, categorized by how their elements are accessed and mutated:
>
> 1.  **attr-collection**: `__getattr__`/`__setattr__`/`__delattr__`
> 2.  **item-collection**: `__getitem__`/`__setitem__`/`__delitem__`

Example:

```python
# 1. attr-collection
a_dataclass.attr = value

# 2. item-collection
a_list[0] = value
a_dict["item"] = value
```

### Reactivity & Reactive Types

> **Definition**
>
> **Reactivity** is the property of a _mutable_ **collection** that enables bidirectional synchronization with on-disk data.
>
> A **collection** is **reactive** if a modification to one of its items or attributes triggers a write to a JSON file. Conversely, an external modification to the file also updates the in-memory **collection**.

Specifically, a **collection** is **reactive** if it meets two conditions:

1.  Its implementation of `__setattr__`/`__delattr__` (for an **attr-collection**) or `__setitem__`/`__delitem__` (for an **item-collection**) triggers a write to a JSON file. (Python -> JSON)
2.  A callback exists somewhere to update the **collection** when the JSON file is modified by an external process. (JSON -> Python)

### Reactive Reference

> **Definition**
>
> A **reactive reference** is a Python variable that points to a **reactive** object managed by _Syncwave_. This object can be an entire data store or a nested part of it. It will simply be referred to as **reference** from now on.

For example, if we have `my_store = syncwave["customers"]`, then the variable `my_store` (a **reactive collection**) is a **reference**. _Syncwave_ guarantees that any **reference** is always bidirectionally synchronized with its corresponding on-disk data.

You can also create **references** to nested data, like `john_doe = my_store[1]`. Operations performed through `john_doe` will be **reactive** and persist to disk. The specific types of **reactive** objects you can create **references** to will be detailed in the following sections.

## 2. `Syncwave`

The `Syncwave` object is the root of the **reactive** system. It is a `MutableMapping` that can hold any type of data (`MutableMapping[str, Any]`). Its items are stores, each associated with a JSON file.

`Syncwave` is a **reactive item-collection**.

### Creating Stores

There are two ways to create a store:

1. `syncwave.create_store`: Creates a store for a given type. The type can be any type supported by _Pydantic_. _Syncwave_ creates the associated JSON file if it doesn't exist, or loads existing data from it.
2. `@syncwave.register`: see the section [SyncModel](#4-syncmodel).

### Using Stores

Any JSON-serializable data can be saved in `Syncwave`. You access data via `syncwave["my_store"]`, edit it with a new assignment like `syncwave["my_store"] = ["a", "new", "value"]`, and delete the store with `del syncwave["my_store"]`. Since `Syncwave` is a `MutableMapping`, all other standard `dict` operations are available.

> **Warning**
>
> When assigning a standard, non-**reactive** Python **collection**, like `syncwave["my_list"] = my_list`, only the top-level `syncwave` instance is **reactive**. The variable `my_list` is not, and thus subsequent mutations won't trigger a write. Also, to prevent unintended side effects, _Syncwave_ stores a _deep copy_ of the assigned value, not the original object so that `syncwave["my_list"]` is not affected.
>
> ```python
> my_list = [1, 2, 3]            # a standard, non-reactive list
> syncwave["my_list"] = my_list  # a deep copy is stored and written on-disk
> my_list.append(4)              # nothing happens on `syncwave` and on-disk
> ```
>
> **Important: in-place mutations to the stored copy itself will break the synchronization guarantee!**
>
> ```python
> # Do not do this!
> syncwave["my_list"].append(4)
> ```
>
> The in-memory data would change, but the on-disk data would not, causing them to diverge.
>
> _Syncwave_ cannot detect this in-place mutation on a standard Python collection, so it is **strongly discouraged**. This is a fundamental aspect of Python's data model; the `append` operation modifies the list in-place and does not trigger the parent object's `__setitem__` method.
>
> _Pydantic_ behaves similarly; an operation like `customer.a_list.append(4)` does not trigger validation, even when the model is configured with `validate_assignment=True`.
>
> For **reactivity** to propagate _into_ the list, you need a **reactive collection**. In that case, the previous operation would be perfectly fine, and it would even be possible to create a **reference** to it so that `my_reactive_list.append(4)` would work. See the [next section](#3-synccollection).

## 3. `SyncCollection`

`SyncCollection` is an abstract base class for **reactive collections** provided by _Syncwave_. There are three concrete implementations:

- `SyncDict`: Inherits from `collections.abc.MutableMapping`.
- `SyncList`: Inherits from `collections.abc.MutableSequence`.
- `SyncSet`: Inherits from `collections.abc.MutableSet`.

`SyncCollection` types are **reactive item-collections**.

### Instantiation

They cannot be instantiated directly. Instead, _Syncwave_ creates them when a compatible type is assigned to a store. For example, if a store is registered as `store: SyncDict[int, SyncList[int]]`, assigning a standard `list` such as `store[0] = [1, 2, 3]` will cause _Syncwave_ to automatically convert the `list` into a **reactive** `SyncList`.

<!-- ### Configuration (To be implemented)

Each `SyncCollection` type can be configured using `typing.Annotated`. For instance, `LimitedList = Annotated[SyncList, max_items(10)]` would set a maximum number of allowed items. The idea is to cover every **collection** definable with _JSON Schema_ through a combination of `SyncCollection` and `Annotated`. -->

## 4. `SyncModel`

A `SyncModel` is a user-defined, model-like class that _Syncwave_ has made **reactive**.

To create a `SyncModel`, use `syncwave.make_reactive`. The supported types are:

1. subclasses of `pydantic.BaseModel`,
2. subclasses of `pydantic.RootModel`,
3. classes decorated with `@pydantic.dataclasses.dataclass`.

Internally, _Syncwave_ creates a new class that inherits from both the original class and `SyncModel`.

`SyncModel` types are **reactive attr-collections**.

### Model Registration

A model can directly be registered into a store using the class decorator `@syncwave.register`. This is similar to using `make_reactive` on the model, and then adding it in a store with `create_store`.

When creating a store using this method, _Syncwave_ automatically wraps the model in a `SyncCollection`. The `collection` parameter controls this behavior and accepts one of the following values: `SyncDict`, `SyncList`, `None`, or the literal string `"auto"`.

- `SyncDict` or `SyncList` (e.g., `collection=SyncList`): Explicitly specify which **collection** wrapper to use. `SyncDict` also accepts a single type argument in that context to specify the key, e.g. `collection=SyncDict[int]`.
- `None`: Explicitly disable wrapping. The store will hold a single instance of the model rather than a **collection** of them.
- `"auto"` (default): _Syncwave_ selects the appropriate **collection** as follows:
  - `None`: If the model is a `RootModel`.
  - `SyncDict`: If the model is a `BaseModel` or a _Pydantic_ `dataclass`, and if it has a field named `key`. In that case, the type of the `key` field is used as the `SyncDict` key type.
  - `SyncList`: Otherwise.

## 5. `Reactive`

`Reactive` is an abstract base class that serves as a unifying trait for all **reactive** types managed by _Syncwave_:

- `Syncwave`
- `SyncCollection` (`SyncDict`, `SyncList`, `SyncSet`)
- `SyncModel`

This makes it possible to inspect an object with `isinstance` or a class with `issubclass` to determine if it is a **reactive** type.

### The `sync_live` Property

A **reference** can become "dead" or "stale." For example:

```python
store: SyncList[int] = syncwave["store"]
del syncwave["store"]
print(store)     # `store` still exists, but it is now a dead reference
store.append(0)  # this operation should fail

# Sub-references can also become stale.
store: SyncDict[str, SyncList[int]] = syncwave["store"]
inner: SyncList[int] = store["inner"]  # `inner` is a sub-reference
del store["inner"]                     # deletes the entry, killing `inner`
inner.append(4)                        # should also fail
```

To prevent errors caused by operations on stale data, each `Reactive` object has a read-only `sync_live` property. This flag is managed by _Syncwave_. If an operation "kills" a **reference**, its `sync_live` property will be set to `False`.

Every operation on a `Reactive` object first checks this property. If the object is not live, it will raise a `DeadReferenceError`.

### Composability

**Reactive** types can be composed to create complex, nested data structures. `Syncwave` is always the root, but you can compose any `SyncCollection` and `SyncModel` underneath it: a `SyncList` inside a `SyncDict`, a `SyncSet` in a `SyncList`, a `SyncDict` as an attribute of a **reactive** `BaseModel`, etc.

> **Warning**
>
> For **reactivity** to work when composing types, the chain of **reactive** objects must be unbroken from the `Syncwave` root downwards. As soon as a non-**reactive** type is introduced, all types nested within it must also be non-**reactive**.
