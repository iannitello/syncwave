# Types and Validation

Every store has a type, and that type is the contract Syncwave enforces on both sides of the sync. This page explains which types are possible, how initial values are determined, and where validation happens.

## The JSON Schema Foundation

The domain of Python objects is vast; the domain of things a JSON file can hold is small. Syncwave bridges the two the same way Pydantic and FastAPI do: through **JSON Schema**. The guiding principle is simple — if a type can be described by a JSON Schema, it can round-trip between Python and a JSON file, and Syncwave can sync it.

In practice, Syncwave delegates all of this to [Pydantic](https://docs.pydantic.dev/latest/). Any type Pydantic can validate and serialize is a valid store type: primitives, containers, `datetime` and friends, `UUID`, `Enum`, `Literal`, your own models, unions, and arbitrary nesting of all of the above.

## What Can Be a Store

Some examples beyond the collections seen so far. A store can be a single scalar with a rich Python type:

```python
from datetime import date

syncwave.create_store(date, name="release", default=date(2026, 1, 1))
print(syncwave["release"])  # datetime.date(2026, 1, 1)
```

```json title="syncstores/release.json"
"2026-01-01"
```

The Python side works with a real `date` object; the JSON side stores the standard string representation. The conversion in both directions is Pydantic's, so it matches what you know from Pydantic models.

Constraints travel with the type, using the standard `Annotated` syntax:

```python
from typing import Annotated

from pydantic import Field

syncwave.create_store(Annotated[int, Field(ge=0)], name="count", default=0)
syncwave["count"] = -1  # ValidationError: Input should be greater than or equal to 0
```

And if you don't want a schema at all, `typing.Any` accepts any JSON-serializable data:

```python
from typing import Any

syncwave.create_store(Any, name="anything", default=None)
syncwave["anything"] = {"mixed": [1, "two", None]}
```

## Initial Values

When a store is created and its file is missing or empty, Syncwave needs an initial value. It first tries to infer a natural "empty" one by validating, in order: `{}`, `[]`, `""`, and `None`. The first that satisfies the store's type wins — an empty dict for mappings, an empty list for sequences and sets, and so on.

When none of them fits, you must provide the value yourself through the `default` parameter:

```python
syncwave.create_store(int, name="counter")
```

```console
ValueError: Unable to create store 'counter' without a default.
```

```python
syncwave.create_store(int, name="counter", default=0)  # works
```

Two details to keep in mind:

- The `default` parameter is **ignored when an empty value can be inferred**. For a union like `Union[SyncList[int], SyncDict[str, int]]`, the inference tries `{}` first and succeeds, so the store starts as an empty dict regardless of any `default` you pass.
- The file always wins. Inference and `default` only apply when the file is missing or empty; existing content is validated and loaded instead.

## Where Validation Happens

Every path into a store goes through validation against its type:

- **At creation**, when existing file content is loaded.
- **On every Python-side change**: assigning to the store, setting an item in a sync collection, assigning to a model field. Invalid data raises a `pydantic.ValidationError` and nothing is written.
- **On every file-side change**: if the file's new content is malformed JSON or doesn't match the type, the change is rejected and the file is reverted to the last valid state.

Validation runs in Pydantic's default lax mode, so the usual coercions apply — assigning `["1", 2]` to a `list[int]` store gives you `[1, 2]`. If you want stricter behavior, use Pydantic's standard tools (`Field(strict=True)`, `Strict*` types) in the store's type; Syncwave passes them through untouched.

## Dictionary Keys

JSON object keys are always strings, which puts a special requirement on `SyncDict` (and plain `dict`) key types: the key must serialize to a string and parse back to an equal value. Syncwave validates key types when the store is created and rejects those that cannot round-trip.

The supported key types are: `str`, `int`, `float`, `bool`, `bytes`, `enum.Enum`, `typing.Literal`, `decimal.Decimal`, `re.Pattern`, `pathlib.Path`, `datetime.date`, `datetime.datetime`, `datetime.time`, `datetime.timedelta`, `uuid.UUID`, the `ipaddress` address/interface/network types, and `pydantic.ByteSize`.

The conversion is transparent: with a `SyncDict[int, str]` you use integer keys in Python, and the file stores `"1"`, `"2"`, and so on.

## Hashability

Sets add one more requirement: items must be hashable. Syncwave checks the item type when the store is created, including the tricky recursive cases (`tuple` and `frozenset` are hashable only if their element types are, an `Enum` only if its member values are):

```python
syncwave.create_store(SyncSet[list[int]], name="bad")
```

```console
TypeError: `SyncSet` must hold hashable elements, got `list`.
```

This is also why a `SyncSet` can never contain reactive objects: reactive objects are mutable, and mutable objects are not hashable.

## Unions

Union types are fully supported, including unions of reactive types — a slot typed `Union[SyncList[int], SyncDict[str, int]]` holds one member at a time and can switch between them. What a type switch means for existing references is covered in [Reactivity](./reactivity/).
