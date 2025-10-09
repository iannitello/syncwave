# Reactivity

## 1. Definitions

> **Collection**
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

> **Reactivity & Reactive Types**
>
> **Reactivity** is the property of a _mutable_ **collection** that enables bidirectional synchronization with on-disk data.
>
> A **collection** is **reactive** if a modification to one of its items or attributes triggers a write to a file (typically JSON). Conversely, an external modification to the file must also update the in-memory **collection**.

Only mutable **collections** can be **reactive**. This may seem obvious, but it rules out types like `tuple` and `namedtuple`. While a tuple can contain a **reactive** object (e.g., `my_tuple[0].attr = 4`), it is the object _inside_ the tuple that is **reactive**, not the tuple itself.

Specifically, a **collection** is **reactive** if it meets two conditions:

1.  Its implementation of `__setattr__`/`__delattr__` (for an **attr-collection**) or `__setitem__`/`__delitem__` (for an **item-collection**) triggers a write to a JSON file. (Python -> JSON)
2.  A callback exists somewhere to update the **collection** when the JSON file is modified by an external process. (JSON -> Python)

> **Warning**
>
> Mutating objects _contained within_ a **reactive collection** does not trigger a write (unless the inner objects are themselves **reactive**). For example:
>
> ```python
> reactive_object["a_list"] = [1, 2, 3]  # triggers a write
> reactive_object["a_list"].append(4)    # does not trigger a write
> ```
>
> **The second operation breaks the synchronization guarantee!** The in-memory data will change, but the on-disk data will not, causing them to diverge.
>
> This is a fundamental aspect of Python's data model; the `append` operation modifies the list in-place and does not trigger the parent object's `__setitem__` method. _Syncwave_ cannot detect this in-place mutation, so it is strongly discouraged.
>
> _Pydantic_ behaves similarly, where an operation like `customer.a_list.append(4)` does not trigger validation, even when the model is configured with `validate_assignment=True`.

## 2. `Syncwave`

The `Syncwave` object is the root of the **reactive** system. It is a `MutableMapping` that can hold any type of data (`MutableMapping[str, Any]`). Its items are stores, each associated with a JSON file.

`Syncwave` is a **reactive item-collection**.

There are two ways to create a store:

1.  **Direct Assignment (Unregistered Store)**: This is the simplest and most flexible method. With an assignment like `syncwave["my_store"] = "my_value"`, _Syncwave_ automatically creates a JSON file, tracks its content, and keeps it synchronized with the value at `syncwave["my_store"]`.
2.  **Registration (Registered Store)**: This method is much more powerful. It offers many configuration options and, most importantly, provides type checking (via `pydantic.TypeAdapter`) for both in-memory and on-disk edits. It would look something like `syncwave.register(str, name="my_store")`.

Any JSON-serializable data can be saved in `Syncwave`. You access data via `syncwave["my_store"]`, edit it with a new assignment like `syncwave["my_store"] = ["a", "new", "value"]`, and delete the store with `del syncwave["my_store"]`. Since `Syncwave` is a `MutableMapping`, all other standard `dict` operations are available.

> **Warning**
>
> When you assign a standard, non-**reactive** Python collection, like `syncwave["my_list"] = my_list`, only the top-level `Syncwave` object is **reactive**, not the `my_list` variable itself.
>
> To prevent unintended side effects, _Syncwave_ stores a _deep copy_ of the assigned value, not a reference to the original object. Consequently, subsequent mutations to the original `my_list` (e.g., `my_list.append("new_value")`) will not affect the data stored in _Syncwave_.
>
> However, in-place mutations to the stored copy itself, like `syncwave["my_list"].append(4)`, will break the synchronization guarantee. For **reactivity** to propagate _into_ the list, you need a **reactive** collection. See the [next section](#3-synccollection).

## 3. `SyncCollection`

`SyncCollection` is a type alias for one of three **reactive collection** classes provided by _Syncwave_:

- `SyncDict`: Inherits from `collections.abc.MutableMapping`.
- `SyncList`: Inherits from `collections.abc.MutableSequence`.
- `SyncSet`: Inherits from `collections.abc.MutableSet`.

`SyncCollection` types are **reactive item-collections**.

Each can be configured using `typing.Annotated`. For instance, `Annotated[SyncList, max_items(10)]` could set a maximum number of allowed items.

The goal is to cover every **collection** type definable with _JSON Schema_ through a combination of `SyncCollection` and `Annotated`.

When registering a type, _Syncwave_ can wrap it in a `SyncCollection`. The `collection` parameter controls this wrapping behavior and can be one of three values:

- A `SyncCollection` type (e.g., `collection=SyncDict`): The user can explicitly specify which collection wrapper to use, overriding the `"auto"` logic.
- `None` (e.g., `collection=None`): The user can explicitly disable collection wrapping. The store will hold a single instance of the registered type rather than a collection of them.
- `"auto"` (default): Syncwave intelligently selects the appropriate collection based on the registered type:
  - `None`: If the registered type is itself a `SyncCollection` or a `RootModel`.
  - `SyncDict`: If the registered type is a `BaseModel` or a `dataclass`.
  - `SyncList`: For all other types, including Python primitives (`str`, `list`, `list[bool]`, etc.).

`SyncCollection` types cannot be instantiated directly by the user. Instead, _Syncwave_ creates them when a compatible type is assigned to a store. For example, if a store is registered as `store: SyncDict[int, SyncList[int]`, assigning a standard `list` such as `store[0] = [1, 2, 3]` will cause _Syncwave_ to automatically convert the `list` into a **reactive** `SyncList`.

## 4. `SyncModel`

`SyncModel` is a type alias for model-like classes that can be made **reactive**, such as:

- `pydantic.BaseModel`
- `pydantic.RootModel`
- `pydantic.dataclasses.dataclass`
- `dataclasses.dataclass`

`SyncModel` types are **reactive attr-collections**.

Unlike `SyncCollection`, `SyncModel` types are not inherently **reactive**. To make them **reactive**, _Syncwave_ subclasses them when they are registered. The inheritance chain would look like: `pydantic.BaseModel` -> `__main__.Customer` -> `syncwave.Customer`.

When a `SyncModel` is used within a `SyncCollection` (e.g., a store of type `SyncDict[int, Customer]`), new instances of that model are automatically added to their parent **collection** upon creation. This behavior is especially powerful with libraries like _FastAPI_:

```python
customers: SyncList[Customer] = syncwave["customers"]


@app.post("/customer")
def create_customer(customer: Customer) -> Customer:
    # The `customer` instance created by FastAPI from the request body
    # has already been automatically added to the `customers` store.
    # No `customers.append(customer)` is needed.
    customer.age += 1  # This is a reactive operation that writes to JSON.
    return customer
```

For advanced cases where a non-**reactive**, transient instance is needed (e.g., for intermediate validation), the user can define a plain model and then create a **reactive** version of it:

```python
# A normal, non-reactive BaseModel
class Customer(BaseModel):
    id: int
    name: str


# A new, reactive version of the Customer class
SyncCustomer = syncwave.make_reactive(Customer)


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

## 5. `Reactive`

Together, `Syncwave`, `SyncCollection`, and `SyncModel` constitute the **reactive** types provided by _Syncwave_.

(I have yet to consider how to let users implement their own.)

### Composability

**Reactive** types can be composed to create complex, nested data structures. `Syncwave` is always the root, but you can compose any `SyncCollection` and `SyncModel` underneath it: a `SyncList` inside a `SyncDict`, a `SyncSet` in a `SyncList`, a `SyncDict` as an attribute of a **reactive** `BaseModel`, etc.

> **Warning**
>
> For **reactivity** to work, the chain of **reactive** objects must be unbroken from the `Syncwave` root downwards. As soon as a non-**reactive** type is introduced, all types nested within it will also be non-**reactive**.
>
> For example, even if `Customer` is a **reactive** model, `SyncDict[list[Customer]]` would not work as intended, because the standard Python `list` breaks the **reactivity** chain. Operations on the `list` or on `Customer` will not be tracked.

### Reference

> **Definition: Reference**
>
> A **reference** is a Python variable that points to a **reactive** object.
>
> _Syncwave_ guarantees that a **reference** is always bidirectionally synchronized with its corresponding data on-disk.

For example, if we have `customers: SyncDict[int, Customer] = syncwave["customers"]`, then `customers` is a **reference**. Modifying it will update the JSON file, and external edits to the JSON file will be reflected in the `customers` object. You can also create **references** to nested objects, like `john = customers[0]`, and use them to read and write data.

An instance of `Syncwave` can also be considered a **reference**, even though multiple files are associated with it rather than a single one.

### `sync_live`

A **reference** can become "dead" or "stale." For example:

```python
store: SyncList[int] = syncwave["store"]
del syncwave["store"]
print(store)     # `store` still exists, but it is now a dead reference
store.append(0)  # this operation should fail

# Sub-references can also become stale.
data: SyncDict[str, SyncList[int]] = syncwave["data"]
sub_ref: SyncList[int] = data["inner"]
data["inner"] = [1, 2, 3]  # replaces the list, unaliving `sub_ref`
sub_ref.append(4)          # should also fail
```

To prevent errors from operating on stale data, each **reactive** object has a read-only `@property` called `sync_live`. This flag is managed by _Syncwave_. If an operation "kills" a **reference**, its `sync_live` property will be set to `False`.

Every read and write operation on a **reactive** object first checks this property. If the object is not live, it will raise a `DeadReferenceError`.
