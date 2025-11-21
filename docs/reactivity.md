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
> A **collection** is **reactive** if a modification to one of its items or attributes triggers a write to a file (typically JSON). Conversely, an external modification to the file must also update the in-memory **collection**.

Only mutable **collections** can be **reactive**. This may seem obvious, but it rules out types like `tuple` and `namedtuple`. While a tuple can contain a **reactive** object (e.g., `my_tuple[0].attr = 4`), it is the object _inside_ the tuple that is **reactive**, not the tuple itself.

Specifically, a **collection** is **reactive** if it meets two conditions:

1.  Its implementation of `__setattr__`/`__delattr__` (for an **attr-collection**) or `__setitem__`/`__delitem__` (for an **item-collection**) triggers a write to a JSON file. (Python -> JSON)
2.  A callback exists somewhere to update the **collection** when the JSON file is modified by an external process. (JSON -> Python)

### Reference

> **Definition**
>
> A **reference** is a Python variable that points to a **reactive** object managed by _Syncwave_. This object can be an entire data store or a nested part of it.

For example, if we have `my_store = syncwave["customers"]`, then `my_store` (a **reactive collection**) is a **reference**. _Syncwave_ guarantees that any **reference** is always bidirectionally synchronized with its corresponding on-disk data.

You can also create **references** to nested data, like `john_doe = my_store[1]`. Operations performed through `john_doe` will be **reactive** and persist to disk. The specific types of **reactive** objects you can create **references** to will be detailed in the following sections.

## 2. `Syncwave`

The `Syncwave` object is the root of the **reactive** system. It is a `MutableMapping` that can hold any type of data (`MutableMapping[str, Any]`). Its items are stores, each associated with a JSON file.

`Syncwave` is a **reactive item-collection**.

### Creating Stores

There are two ways to create a store:

1.  **Direct Assignment (Unregistered Store)**: This is the simplest and most flexible method. With an assignment like `syncwave["my_store"] = "my_value"`, _Syncwave_ automatically creates a JSON file, tracks its content, and keeps it synchronized with the value at `syncwave["my_store"]`.
2.  **Registration (Registered Store)**: This method is much more powerful. It offers many configuration options and, most importantly, provides type checking (via `pydantic.TypeAdapter`) for both in-memory and on-disk edits. It would look something like `syncwave.register(str, name="my_store")`.

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

### Wrapping

When registering a type, _Syncwave_ can wrap it in a `SyncCollection`. The `collection` parameter controls this wrapping behavior and accepts one of three values: a `SyncCollection` type, `None`, or the literal string `"auto"`.

- `SyncCollection` (e.g., `collection=SyncDict`): Explicitly specify which **collection** wrapper to use.
- `None`: Explicitly disable wrapping. The store will hold a single instance of the registered type rather than a **collection** of them.
- `"auto"` (default): _Syncwave_ selects the appropriate **collection** as follows:
  - `None`: If the registered type is itself a `SyncCollection`, or if it is a `RootModel`.
  - `SyncDict`: If the registered type is a `BaseModel` or a `dataclass`.
  - `SyncList`: For all other types, including Python primitives (`str`, `list`, `list[bool]`, etc.).

### Configuration

Each `SyncCollection` type can be configured using `typing.Annotated`. For instance, `LimitedList = Annotated[SyncList, max_items(10)]` would set a maximum number of allowed items. The idea is to cover every **collection** definable with _JSON Schema_ through a combination of `SyncCollection` and `Annotated`.

## 4. `SyncModel`

A `SyncModel` is a user-defined, model-like class that _Syncwave_ has made **reactive**.

`SyncModel` types are **reactive attr-collections**.

### Supported Types

This introduces a distinction between model types that _can be made_ **reactive** and those that _are_ **reactive**.

- **`SyncModelSupported`**: This is a protocol for types that _Syncwave_ can make **reactive** (they are **not reactive** on their own). It includes:

  - `pydantic.BaseModel`
  - `pydantic.RootModel`
  - `pydantic.dataclasses.dataclass`
  - `dataclasses.dataclass`

- **`SyncModel`**: This is a marker base class for models that have been made **reactive** by _Syncwave_.

When a `SyncModelSupported` class (like a `pydantic.BaseModel`) is registered with _Syncwave_, it is automatically made **reactive** by default (but it is possible to opt-out). Internally, _Syncwave_ creates a new class that inherits from both the original class and `SyncModel`. See [class_naming.py](class_naming.py) for more details about this process.

### Auto-Adding Instances

When a `SyncModel` is used within a `SyncCollection` (e.g., a store of type `SyncDict[int, Customer]`), new instances of that model are automatically added to their parent **collection** upon creation.

This behavior is especially powerful with libraries like _FastAPI_:

```python
# When registering, Syncwave also makes Customer reactive
@syncwave.register(name="customers", key="id")
class Customer(BaseModel):
    id: int
    name: str
    age: int


customers: SyncDict[int, Customer] = syncwave["customers"]

@app.post("/customer")
def create_customer(customer: Customer) -> Customer:
    # The `customer` instance created by FastAPI from the request body
    # has automatically been added to the `customers` store.
    # No `customers.append(customer)` is needed.
    customer.age += 1  # This is a reactive operation that writes to JSON.
    return customer
```

For advanced cases where a non-**reactive**, transient instance is needed, the user can define a plain model and then create a **reactive** version of it using `syncwave.reactive`:

```python
# A normal, non-reactive BaseModel
class Customer(BaseModel):
    id: int
    name: str


# A new, reactive version of the Customer class
SyncCustomer = syncwave.reactive(Customer)

# The new class is used to register
syncwave.register(SyncCustomer, name="customers", key="id")


@app.post("/customer")
def create_customer(customer: Customer) -> SyncCustomer:
    # `customer` is a normal, non-reactive instance.
    # We can perform validation or other operations on it first.
    customer = process_customer_data(customer)
    # Now, create a reactive instance to persist the data.
    # This instance is automatically added to the `customers` store.
    sync_customer = SyncCustomer(**customer.model_dump())
    return sync_customer
```

TODO: There's an issue with "illegal" models; e.g. a user instantiate a model nested in a SyncSet, and an "equal" model already exists in the set. What happens then? Maybe the model is created but is dead (`sync_live=False`), or maybe the instantiation raises an error?

## 5. `Reactive`

`Reactive` is an abstract base class that serves as a unifying trait. All **reactive** types exposed by _Syncwave_ inherit from `Reactive`:

- `Syncwave`
- `SyncCollection`
- `SyncModel`

This makes it possible to inspect an object with `isinstance` or a class with `issubclass` to determine if an entity is part of the **reactive** system.

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
store["inner"] = [1, 2, 3]             # replaces the list, unaliving `inner`
inner.append(4)                        # should also fail
```

To prevent errors caused by operations on stale data, each `Reactive` object has a read-only `sync_live` property. This flag is managed by _Syncwave_. If an operation "kills" a **reference**, its `sync_live` property will be set to `False`.

Every read and write operation on a `Reactive` object first checks this property. If the object is not live, it will raise a `DeadReferenceError`.

### Composability

**Reactive** types can be composed to create complex, nested data structures. `Syncwave` is always the root, but you can compose any `SyncCollection` and `SyncModel` underneath it: a `SyncList` inside a `SyncDict`, a `SyncSet` in a `SyncList`, a `SyncDict` as an attribute of a **reactive** `BaseModel`, etc.

> **Warning**
>
> For **reactivity** to work when composing types, the chain of **reactive** objects must be unbroken from the `Syncwave` root downwards. As soon as a non-**reactive** type is introduced, all types nested within it will also be non-**reactive**.
>
> For example, even if `Customer` is a **reactive** model, `SyncDict[list[Customer]]` would not work as intended, because the standard Python `list` breaks the **reactivity** chain. Operations on the `list` or on `Customer` instances within it will not be tracked.
