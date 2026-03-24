# There's a problem with implementing SyncDict, SyncList, and SyncSet using the abstract
# collections.abc classes because the "free" mixins are not thread-safe.
# This is a temporary solution just to make it easier to implement.


from __future__ import annotations

from collections.abc import Iterator, MutableMapping, MutableSequence, MutableSet
from dataclasses import dataclass
from types import GenericAlias
from typing import (
    Any,
    Generic,
    NoReturn,
    SupportsIndex,
    TypeVar,
    Union,
    final,
    get_args,
)
from typing_extensions import Self

from pydantic import GetCoreSchemaHandler as Handler
from pydantic import TypeAdapter
from pydantic_core import core_schema as cs

from .reactive import (
    Context,
    ContextMap,
    Reactive,
    StoreRef,
    atomic,
    mut_atomic,
    unreachable,
)

__all__ = ["SyncCollection", "SyncDict", "SyncList", "SyncSet"]


KT = TypeVar("KT")
VT = TypeVar("VT", bound=Union[Reactive, Any])


@final
class SyncCollection(Reactive):
    """Virtual base class for Syncwave's reactive collection types.

    The `SyncCollection` types are `SyncDict`, `SyncList`, and `SyncSet`.

    `SyncCollection` itself cannot be instantiated or subclassed. Can be used for
    `isinstance` and `issubclass` checks.

    Example:
    ```python
    from syncwave import SyncCollection, SyncList, Syncwave

    syncwave = Syncwave()
    sync_list = syncwave.create_store(SyncList[int], name="sync_list")

    print(isinstance(sync_list, SyncCollection))  # True
    print(issubclass(SyncList, SyncCollection))  # True
    ```

    """

    def __init_subclass__(cls, /, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncCollection cannot be subclassed.")

    def __syncwave_init__(self, sref: StoreRef, ctx: Context) -> None:
        raise NotImplementedError

    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    def __syncwave_update__(self, new: Any) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class SyncDictCtx(Context, Generic[KT, VT]):
    tp: type[SyncDict]
    inner_ctx: Context | ContextMap | None
    inner_type_adapter: TypeAdapter[VT]


class SyncDict(MutableMapping[KT, VT], Reactive):
    """A reactive dictionary.

    `SyncDict` behaves like a regular `dict`. Assignments, updates, and deletions
    trigger a write to the backing JSON file, and external changes to the file are
    reflected in place.

    References to reactive values are stable by key: a reference to `store["alice"]`
    continues to represent whatever is stored under `"alice"` until that key is removed,
    at which point `sync_live` is set to `False` and any further operation raises
    `DeadReferenceError`.

    Example:
    ```python
    from syncwave import SyncDict, Syncwave

    syncwave = Syncwave()
    sync_dict = syncwave.create_store(SyncDict[str, int], name="sync_dict")

    sync_dict["a"] = 1
    sync_dict["b"] = 2
    sync_dict["c"] = 3
    print(sync_dict)
    print(list(sync_dict.items()))
    ```

    ---

    Abstract: Usage Documentation
        [SyncDict](https://placeholder.dev/usage/syncwave/)

    """

    __syncwave_ctx__: SyncDictCtx[KT, VT]
    __data: dict[KT, VT]

    @classmethod
    def __new(cls, data: dict[KT, VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            dict_schema = handler.generate_schema(GenericAlias(dict, args))
        else:
            dict_schema = handler.generate_schema(dict)

        inst_schema = cs.is_instance_schema(cls)
        non_inst_schema = cs.no_info_after_validator_function(cls.__new, dict_schema)

        return cs.union_schema(
            [inst_schema, non_inst_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                lambda v, nxt: nxt(v.__data),
                schema=dict_schema,
            ),
        )

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncDictCtx[KT, VT]) -> None:
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        self.__syncwave_live__ = True

        inner_ctx = ctx.inner_ctx
        # case 1: non-reactive content type
        if inner_ctx is None:
            pass
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            for item in self.__data.values():
                item.__syncwave_init__(sref, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            for item in self.__data.values():
                if isinstance(item, Reactive):
                    item.__syncwave_init__(sref, inner_ctx[type(item)])
        else:
            unreachable()

    def __syncwave_kill__(self) -> None:
        for item in self.__data.values():
            if isinstance(item, Reactive):
                item.__syncwave_kill__()
        self.__data = {}
        self.__syncwave_live__ = False

    def __syncwave_update__(self, new: Self) -> None:
        inner_ctx = self.__syncwave_ctx__.inner_ctx

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data = new.__data
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            old_keys, new_keys = set(self.__data.keys()), set(new.__data.keys())
            # items to add and update
            for key in new_keys:
                old_item, new_item = self.__data.get(key), new.__data[key]
                self.__setitem_reactive(key, old_item, new_item, inner_ctx)
            # items to remove
            for key in old_keys - new_keys:
                old_item = self.__data.pop(key)
                old_item.__syncwave_kill__()
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            old_keys, new_keys = set(self.__data.keys()), set(new.__data.keys())
            # items to add and update
            for key in new_keys:
                old_item, new_item = self.__data.get(key), new.__data[key]
                self.__setitem_union(key, old_item, new_item, inner_ctx)
            # items to remove
            for key in old_keys - new_keys:
                old_item = self.__data.pop(key)
                if isinstance(old_item, Reactive):
                    old_item.__syncwave_kill__()
        else:
            unreachable()

    @atomic
    def __getitem__(self, key: KT) -> VT:
        return self.__data[key]

    @mut_atomic
    def __setitem__(self, key: KT, value: VT) -> None:
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        new_item = self.__syncwave_ctx__.inner_type_adapter.validate_python(value)

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data[key] = new_item
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            old_item = self.__data.get(key)
            self.__setitem_reactive(key, old_item, new_item, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            old_item = self.__data.get(key)
            self.__setitem_union(key, old_item, new_item, inner_ctx)
        else:
            unreachable()

    @mut_atomic
    def __delitem__(self, key: KT) -> None:
        old_item = self.__data.pop(key)
        if isinstance(old_item, Reactive):
            old_item.__syncwave_kill__()

    @atomic
    def __iter__(self) -> Iterator[KT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__data))

    @atomic
    def __len__(self) -> int:
        return len(self.__data)

    def __str__(self) -> str:
        items = ", ".join(f"{k!r}: {v}" for k, v in self.__data.items())
        return "{" + items + "}"

    def __repr__(self) -> str:
        status = "live" if self.__syncwave_live__ else "dead"
        return f"<SyncDict {self.__data!r} ({status})>"

    def __setitem_reactive(self, k: KT, o: VT | None, n: VT, ctx: Context) -> None:
        if o is not None:
            o.__syncwave_update__(n)
        else:
            n.__syncwave_init__(self.__syncwave_sref__, ctx)
            self.__data[k] = n

    def __setitem_union(self, k: KT, o: VT | None, n: VT, u_ctx: ContextMap) -> None:
        old_is_reactive = isinstance(o, Reactive)
        new_is_reactive = isinstance(n, Reactive)
        same_type = type(o) is (new_type := type(n))

        if old_is_reactive and new_is_reactive and same_type:
            # `old_is_reactive` means `o` is not None,
            # but aliased conditional expressions are not supported by ty
            o.__syncwave_update__(n)  # ty: ignore[unresolved-attribute]
        else:
            if old_is_reactive:
                # same reason as above, `o` can't be None
                o.__syncwave_kill__()  # ty: ignore[unresolved-attribute]
            if new_is_reactive:
                n.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            self.__data[k] = n


@dataclass(frozen=True)
class SyncListCtx(Context, Generic[VT]):
    tp: type[SyncList]
    inner_ctx: Context | ContextMap | None
    inner_type_adapter: TypeAdapter[VT]


class SyncList(MutableSequence[VT], Reactive):
    """A reactive list.

    `SyncList` behaves like a regular `list`. Appending, replacing, inserting, and
    deleting items trigger a write to the backing JSON file, and external changes to the
    file are reflected in place.

    When a `SyncList` holds reactive items, references are stable by position, not by
    value. A reference to `store[0]` represents whatever is at index `0`. Inserting a
    new element at the beginning shifts that reference to the new element, not to the
    one that was there before.

    Example:
    ```python
    from syncwave import SyncList, Syncwave

    syncwave = Syncwave()
    sync_list = syncwave.create_store(SyncList[int], name="sync_list")

    sync_list.append(1)
    sync_list.extend([2, 3])
    print(sync_list)
    ```

    ---

    Abstract: Usage Documentation
        [SyncList](https://placeholder.dev/usage/syncwave/)

    """

    __syncwave_ctx__: SyncListCtx[VT]
    __data: list[VT]

    @classmethod
    def __new(cls, data: list[VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            list_schema = handler.generate_schema(GenericAlias(list, args))
        else:
            list_schema = handler.generate_schema(list)

        inst_schema = cs.is_instance_schema(cls)
        non_inst_schema = cs.no_info_after_validator_function(cls.__new, list_schema)

        return cs.union_schema(
            [inst_schema, non_inst_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                lambda v, nxt: nxt(v.__data),
                schema=list_schema,
            ),
        )

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncListCtx[VT]) -> None:
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        self.__syncwave_live__ = True

        inner_ctx = ctx.inner_ctx
        # case 1: non-reactive content type
        if inner_ctx is None:
            pass
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            for item in self.__data:
                item.__syncwave_init__(sref, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            for item in self.__data:
                if isinstance(item, Reactive):
                    item.__syncwave_init__(sref, inner_ctx[type(item)])
        else:
            unreachable()

    def __syncwave_kill__(self) -> None:
        for item in self.__data:
            if isinstance(item, Reactive):
                item.__syncwave_kill__()
        self.__data = []
        self.__syncwave_live__ = False

    def __syncwave_update__(self, new: Self) -> None:
        inner_ctx = self.__syncwave_ctx__.inner_ctx

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data = new.__data
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            old_len, new_len = len(self.__data), len(new.__data)
            # items to update
            for i in range(min(old_len, new_len)):
                old_item, new_item = self.__data[i], new.__data[i]
                old_item.__syncwave_update__(new_item)
            # items to add
            if new_len > old_len:
                for i in range(old_len, new_len):
                    new_item = new.__data[i]
                    new_item.__syncwave_init__(self.__syncwave_sref__, inner_ctx)
                    self.__data.append(new_item)
            # items to remove
            elif old_len > new_len:
                for _ in range(old_len - new_len):
                    old_item = self.__data.pop()
                    old_item.__syncwave_kill__()
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            old_len, new_len = len(self.__data), len(new.__data)
            # items to update
            for i in range(min(old_len, new_len)):
                old_item, new_item = self.__data[i], new.__data[i]
                self.__setitem_union(i, old_item, new_item, inner_ctx)
            # items to add
            if new_len > old_len:
                for i in range(old_len, new_len):
                    new_item = new.__data[i]
                    if isinstance(new_item, Reactive):
                        new_item.__syncwave_init__(
                            self.__syncwave_sref__, inner_ctx[type(new_item)]
                        )
                    self.__data.append(new_item)
            # items to remove
            elif old_len > new_len:
                for _ in range(old_len - new_len):
                    old_item = self.__data.pop()
                    if isinstance(old_item, Reactive):
                        old_item.__syncwave_kill__()
        else:
            unreachable()

    @atomic
    def __getitem__(self, index: SupportsIndex) -> VT:
        i = _get_index(index)
        return self.__data[i]

    @mut_atomic
    def __setitem__(self, index: SupportsIndex, value: VT) -> None:
        i = _get_index(index)
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        new_item = self.__syncwave_ctx__.inner_type_adapter.validate_python(value)

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data[i] = new_item
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            self.__data[i].__syncwave_update__(new_item)
        # case 3: union content type
        elif isinstance(inner_ctx, ContextMap):
            old_item = self.__data[i]
            self.__setitem_union(i, old_item, new_item, inner_ctx)
        else:
            unreachable()

    @mut_atomic
    def __delitem__(self, index: SupportsIndex) -> None:
        i = _get_index(index)
        if self.__syncwave_ctx__.inner_ctx is None:
            del self.__data[i]
            return

        data_copy = self.__roundtrip_copy()
        del data_copy[i]
        self_copy = self.__new(data_copy)
        self.__syncwave_update__(self_copy)

    @atomic
    def __len__(self) -> int:
        return len(self.__data)

    @mut_atomic
    def insert(self, index: SupportsIndex, value: VT) -> None:  # noqa: D102
        i = _get_index(index)
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        new_item = self.__syncwave_ctx__.inner_type_adapter.validate_python(value)

        if inner_ctx is None:
            self.__data.insert(i, new_item)
            return

        data_copy = self.__roundtrip_copy()
        data_copy.insert(i, new_item)
        self_copy = self.__new(data_copy)
        self.__syncwave_update__(self_copy)

    def __str__(self) -> str:
        items = ", ".join(str(item) for item in self.__data)
        return "[" + items + "]"

    def __repr__(self) -> str:
        status = "live" if self.__syncwave_live__ else "dead"
        return f"<SyncList {self.__data!r} ({status})>"

    def __setitem_union(self, i: int, o: VT, n: VT, u_ctx: ContextMap) -> None:
        old_is_reactive = isinstance(o, Reactive)
        new_is_reactive = isinstance(n, Reactive)
        same_type = type(o) is (new_type := type(n))

        if old_is_reactive and new_is_reactive and same_type:
            o.__syncwave_update__(n)
        else:
            if old_is_reactive:
                o.__syncwave_kill__()
            if new_is_reactive:
                n.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            self.__data[i] = n

    def __roundtrip_copy(self) -> list[VT]:
        ta = self.__syncwave_ctx__.inner_type_adapter
        return [ta.validate_python(ta.dump_python(item)) for item in self.__data]


def _get_index(index: Any) -> int:
    if isinstance(index, int):
        return index
    if isinstance(index, SupportsIndex):
        return index.__index__()
    if isinstance(index, slice):
        raise TypeError("Slice indices are not supported (yet).")
    raise TypeError(f"SyncList indices must be integers, not {type(index).__name__}.")


@dataclass(frozen=True)
class SyncSetCtx(Context, Generic[VT]):
    tp: type[SyncSet]
    inner_ctx: None  # never holds reactive items
    inner_type_adapter: TypeAdapter[VT]


class SyncSet(MutableSet[VT], Reactive):
    """A reactive set.

    `SyncSet` behaves like a regular `set`. Adding and discarding items trigger a write
    to the backing JSON file, and external changes to the file are reflected in place.

    `SyncSet` can only hold non-reactive, hashable values such as `str`, `int`, `UUID`,
    etc. Reactive types like `SyncCollection` or `SyncModel` are mutable and therefore
    not supported.

    Example:
    ```python
    from syncwave import SyncSet, Syncwave

    syncwave = Syncwave()
    sync_set: SyncSet[int] = syncwave.create_store(SyncSet[int], name="sync_set")

    sync_set.add(1)
    sync_set.add(2)
    sync_set.discard(2)
    print(sync_set)
    ```

    ---

    Abstract: Usage Documentation
        [SyncSet](https://placeholder.dev/usage/syncwave/)

    """

    # SyncSet cannot hold reactive items because a reactive item is mutable
    __syncwave_ctx__: SyncSetCtx[VT]
    __data: set[VT]

    @classmethod
    def __new(cls, data: set[VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            set_schema = handler.generate_schema(GenericAlias(set, args))
        else:
            set_schema = handler.generate_schema(set)

        inst_schema = cs.is_instance_schema(cls)
        non_inst_schema = cs.no_info_after_validator_function(cls.__new, set_schema)

        return cs.union_schema(
            [inst_schema, non_inst_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                lambda v, nxt: nxt(v.__data),
                schema=set_schema,
            ),
        )

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncSetCtx[VT]) -> None:
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        self.__syncwave_live__ = True
        # no need to loop through items since set can't hold reactive items

    def __syncwave_kill__(self) -> None:
        # no need to loop through items since set can't hold reactive items
        self.__data = set()
        self.__syncwave_live__ = False

    def __syncwave_update__(self, new: Self) -> None:
        self.__data = new.__data

    @atomic
    def __contains__(self, value: object) -> bool:
        return value in self.__data

    @atomic
    def __iter__(self) -> Iterator[VT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__data))

    @atomic
    def __len__(self) -> int:
        return len(self.__data)

    @mut_atomic
    def add(self, value: VT) -> None:  # noqa: D102
        new_item = self.__syncwave_ctx__.inner_type_adapter.validate_python(value)
        self.__data.add(new_item)

    @mut_atomic
    def discard(self, value: VT) -> None:  # noqa: D102
        if value in self.__data:
            self.__data.discard(value)

    def __str__(self) -> str:
        if not self.__data:
            return "SyncSet()"
        items = ", ".join(str(item) for item in self.__data)
        return "{" + items + "}"

    def __repr__(self) -> str:
        status = "live" if self.__syncwave_live__ else "dead"
        return f"<SyncSet {self.__data!r} ({status})>"


SyncCollection.register(SyncDict)
SyncCollection.register(SyncList)
SyncCollection.register(SyncSet)


def register(*args: Any, **kwargs: Any) -> NoReturn:
    """SyncCollection does not support class registration."""
    raise TypeError("SyncCollection does not support class registration.")


SyncCollection.register = register  # ty: ignore[invalid-assignment]
