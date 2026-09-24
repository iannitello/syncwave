# There's a problem with implementing SyncDict, SyncList, and SyncSet using the abstract
# collections.abc classes because the "free" mixins are not thread-safe.
# This is a temporary solution just to make it easier to implement.


from __future__ import annotations

from abc import ABCMeta
from collections.abc import (
    Callable as F,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
)
from copy import deepcopy
from dataclasses import dataclass
from inspect import isclass
from types import GenericAlias
from typing import Any, Generic, NoReturn, SupportsIndex, final, get_args, get_origin
from typing_extensions import Self, TypeVar

from pydantic import GetCoreSchemaHandler as Handler, TypeAdapter
from pydantic_core import SchemaSerializer, core_schema as cs

from .errors import unreachable
from .ownership import detach, ingest
from .reactive import (
    Context,
    Reactive,
    StoreRef,
    SyncState,
    UnionCtx,
    dead_guard,
    mut_reactive_op,
    reactive_op,
)

__all__ = ["SyncCollection", "SyncDict", "SyncList", "SyncSet"]


KT = TypeVar("KT", default=str)
VT = TypeVar("VT", bound=Reactive | Any, default=Any)
CT = TypeVar("CT", bound="SyncDict | SyncList | SyncSet")


@final
class SyncCollection(Reactive, metaclass=ABCMeta, _syncwave_root=True):
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

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        """Raise `TypeError`: `SyncCollection` itself cannot be instantiated."""
        raise TypeError("`SyncCollection` is a base class and cannot be instantiated.")

    def __syncwave_init__(self, sref: StoreRef, ctx: Context) -> None:
        raise NotImplementedError

    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    def __syncwave_update__(self, new: Any) -> None:
        raise NotImplementedError


def type_args(tp: Any, root: type[CT]) -> tuple[Any, ...]:
    # `get_args` for SyncCollection types, but works for `class Tags(SyncList[str])`.
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if origin is root or not (isclass(origin) and issubclass(origin, root)):
        return args

    for base in origin.__dict__.get("__orig_bases__", origin.__bases__):
        base_origin = get_origin(base) or base
        if isclass(base_origin) and issubclass(base_origin, root):
            base_args = type_args(base, root)
            break
    else:
        return args

    # fills TypeVars left open
    tp_vars = tuple(dict.fromkeys(a for a in base_args if isinstance(a, TypeVar)))
    if not args:
        return () if tp_vars else base_args
    if not base_args:
        return args
    tp_name = origin.__qualname__
    if not tp_vars:
        raise TypeError(f"`{tp_name}` is not generic and cannot be subscripted.")
    if (len_a := len(args)) != (len_tpv := len(tp_vars)):
        raise TypeError(f"`{tp_name}` expects {len_tpv} type argument(s), got {len_a}.")
    substitutions = dict(zip(tp_vars, args, strict=True))
    return tuple(substitutions.get(a, a) for a in base_args)


@dataclass(frozen=True)
class SyncDictCtx(Context, Generic[KT, VT]):
    tp: type[SyncDict[Any, Any]]
    inner_ctx: Context | UnionCtx | None
    key_type_adapter: TypeAdapter[KT] | TypeAdapter[str]
    value_type_adapter: TypeAdapter[VT]


class SyncDict(MutableMapping[KT, VT], Reactive, _syncwave_root=True):
    """A reactive dictionary.

    `SyncDict` behaves like a regular `dict`. Assignments, updates, and deletions
    trigger a write to the backing JSON file, and external changes to the file are
    reflected in place.

    Creating a `SyncDict` yourself, with the same arguments as `dict`, gives an inert
    object: it behaves like a regular `dict` and is not connected to any store. Nothing
    is validated while inert, so type arguments (e.g. `SyncDict[str, int]`) are only
    enforced when the value enters a store, which copies it.

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
        [SyncDict](https://syncwave.dev/usage/syncwave/)

    """

    __data: dict[KT, VT]
    __syncwave_ctx__: SyncDictCtx[KT, VT]
    __pydantic_serializer__: SchemaSerializer

    def __init__(
        self,
        data: Mapping[KT, VT] | Iterable[tuple[KT, VT]] = (),
        /,
        **kwargs: VT,
    ) -> None:
        """Create an inert `SyncDict`.

        Args:
            data: Same as for `dict`: a mapping, or an iterable of key/value pairs.
            **kwargs: Same as for `dict`: additional entries, keyed by name.

        """
        self.__data = dict(data, **kwargs)

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        dict_schema = (
            handler.generate_schema(GenericAlias(dict, args))
            if (args := type_args(src, SyncDict))
            # a bare SyncDict is treated as SyncDict[str, Any]
            else handler.generate_schema(GenericAlias(dict, (str, Any)))
        )
        ser_schema = cs.wrap_serializer_function_ser_schema(
            _serializer_factory(cls, unwrap=cls.__data_unwrap), schema=dict_schema
        )
        return cs.no_info_wrap_validator_function(
            _validator_factory(cls, new=cls.__new, unwrap=cls.__data_unwrap),
            dict_schema,
            serialization=ser_schema,
        )

    @classmethod
    def __new(cls, data: dict[KT, VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    def __data_unwrap(self) -> dict[KT, VT]:
        return self.__data

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncDictCtx[KT, VT]) -> None:
        self.__syncwave_state__ = SyncState.LIVE
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx

        inner_ctx = ctx.inner_ctx
        # case 1: non-reactive content type
        if inner_ctx is None:
            pass
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            for value in self.__data.values():
                value.__syncwave_init__(sref, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, UnionCtx):
            for value in self.__data.values():
                if isinstance(value, Reactive):
                    value.__syncwave_init__(sref, inner_ctx[type(value)])
        else:
            unreachable()

    def __syncwave_kill__(self) -> None:
        for value in self.__data.values():
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        self.__syncwave_state__ = SyncState.DEAD

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
                old_value, new_value = self.__data.get(key), new.__data[key]
                self.__setitem_reactive(key, old_value, new_value, inner_ctx)
            # items to remove
            for key in old_keys - new_keys:
                old_value = self.__data.pop(key)
                old_value.__syncwave_kill__()
        # case 3: union content type
        elif isinstance(inner_ctx, UnionCtx):
            old_keys, new_keys = set(self.__data.keys()), set(new.__data.keys())
            # items to add and update
            for key in new_keys:
                old_value, new_value = self.__data.get(key), new.__data[key]
                self.__setitem_union(key, old_value, new_value, inner_ctx)
            # items to remove
            for key in old_keys - new_keys:
                old_value = self.__data.pop(key)
                if isinstance(old_value, Reactive):
                    old_value.__syncwave_kill__()
        else:
            unreachable()

    @reactive_op(inert_fn=dict.__getitem__, unwrap=__data_unwrap)
    def __getitem__(self, key: KT) -> VT:
        return detach(self.__data[key], self.__syncwave_ctx__.value_type_adapter)

    @mut_reactive_op(inert_fn=dict.__setitem__, unwrap=__data_unwrap)
    def __setitem__(self, key: KT, value: VT) -> None:
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        key = ingest(key, self.__syncwave_ctx__.key_type_adapter)
        new_value = ingest(value, self.__syncwave_ctx__.value_type_adapter)

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data[key] = new_value
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            old_value = self.__data.get(key)
            self.__setitem_reactive(key, old_value, new_value, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, UnionCtx):
            old_value = self.__data.get(key)
            self.__setitem_union(key, old_value, new_value, inner_ctx)
        else:
            unreachable()

    @mut_reactive_op(inert_fn=dict.__delitem__, unwrap=__data_unwrap)
    def __delitem__(self, key: KT) -> None:
        old_value = self.__data.pop(key)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_kill__()

    @reactive_op(inert_fn=dict.__iter__, unwrap=__data_unwrap)
    def __iter__(self) -> Iterator[KT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__data))

    @reactive_op(inert_fn=dict.__len__, unwrap=__data_unwrap)
    def __len__(self) -> int:
        return len(self.__data)

    def __str__(self) -> str:
        return str(self.__data)

    def __repr__(self) -> str:
        tp_name, state = type(self).__qualname__, self.__syncwave_state__.value
        return f"<{tp_name} {self.__data!r} ({state})>"

    @reactive_op()
    def __copy__(self) -> Self:
        return self.__new(dict(self))

    @reactive_op()
    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self.__new(deepcopy(self.__data, memo))

    copy = __copy__

    @reactive_op()
    def __eq__(self, other: object, /) -> bool:
        if isinstance(other, dict):
            return self.__data == other
        if isinstance(other, SyncDict):
            return self.__data == dead_guard(other).__data
        return NotImplemented

    __hash__ = None

    def __setitem_reactive(self, k: KT, old: VT | None, new: VT, ctx: Context) -> None:
        if old is not None:
            old.__syncwave_update__(new)
        else:
            new.__syncwave_init__(self.__syncwave_sref__, ctx)
            self.__data[k] = new

    def __setitem_union(self, k: KT, old: VT | None, new: VT, u_ctx: UnionCtx) -> None:
        old_is_reactive = isinstance(old, Reactive)
        new_is_reactive = isinstance(new, Reactive)
        same_type = type(old) is (new_type := type(new))

        if old_is_reactive and new_is_reactive and same_type:
            old.__syncwave_update__(new)
        else:
            if old_is_reactive:
                old.__syncwave_kill__()
            if new_is_reactive:
                new.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            self.__data[k] = new


@dataclass(frozen=True)
class SyncListCtx(Context, Generic[VT]):
    tp: type[SyncList[Any]]
    inner_ctx: Context | UnionCtx | None
    item_type_adapter: TypeAdapter[VT]


class SyncList(MutableSequence[VT], Reactive, _syncwave_root=True):
    """A reactive list.

    `SyncList` behaves like a regular `list`. Appending, replacing, inserting, and
    deleting items trigger a write to the backing JSON file, and external changes to the
    file are reflected in place.

    Creating a `SyncList` yourself, with the same arguments as `list`, gives an inert
    object: it behaves like a regular `list` and is not connected to any store. Nothing
    is validated while inert, so type arguments are only enforced when the value enters
    a store, which copies it: `SyncList[int](["a"])` is accepted, but assigning it to a
    `SyncList[int]` store raises a `ValidationError`.

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
        [SyncList](https://syncwave.dev/usage/syncwave/)

    """

    __data: list[VT]
    __syncwave_ctx__: SyncListCtx[VT]
    __pydantic_serializer__: SchemaSerializer

    def __init__(self, iterable: Iterable[VT] = ()) -> None:
        """Create an inert `SyncList`.

        Args:
            iterable: Initial items, same as for `list`.

        """
        self.__data = list(iterable)

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        list_schema = (
            handler.generate_schema(GenericAlias(list, args))
            if (args := type_args(src, SyncList))
            else handler.generate_schema(list)
        )
        ser_schema = cs.wrap_serializer_function_ser_schema(
            _serializer_factory(cls, unwrap=cls.__data_unwrap), schema=list_schema
        )
        return cs.no_info_wrap_validator_function(
            _validator_factory(cls, new=cls.__new, unwrap=cls.__data_unwrap),
            list_schema,
            serialization=ser_schema,
        )

    @classmethod
    def __new(cls, data: list[VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    def __data_unwrap(self) -> list[VT]:
        return self.__data

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncListCtx[VT]) -> None:
        self.__syncwave_state__ = SyncState.LIVE
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx

        inner_ctx = ctx.inner_ctx
        # case 1: non-reactive content type
        if inner_ctx is None:
            pass
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            for item in self.__data:
                item.__syncwave_init__(sref, inner_ctx)
        # case 3: union content type
        elif isinstance(inner_ctx, UnionCtx):
            for item in self.__data:
                if isinstance(item, Reactive):
                    item.__syncwave_init__(sref, inner_ctx[type(item)])
        else:
            unreachable()

    def __syncwave_kill__(self) -> None:
        for item in self.__data:
            if isinstance(item, Reactive):
                item.__syncwave_kill__()
        self.__syncwave_state__ = SyncState.DEAD

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
        elif isinstance(inner_ctx, UnionCtx):
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

    @reactive_op(inert_fn=list.__getitem__, unwrap=__data_unwrap)
    def __getitem__(self, index: SupportsIndex) -> VT:
        i = self.__get_index(index)
        return detach(self.__data[i], self.__syncwave_ctx__.item_type_adapter)

    @mut_reactive_op(inert_fn=list.__setitem__, unwrap=__data_unwrap)
    def __setitem__(self, index: SupportsIndex, value: VT) -> None:
        i = self.__get_index(index)
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        new_item = ingest(value, self.__syncwave_ctx__.item_type_adapter)

        # case 1: non-reactive content type
        if inner_ctx is None:
            self.__data[i] = new_item
        # case 2: fixed reactive content type
        elif isinstance(inner_ctx, Context):
            self.__data[i].__syncwave_update__(new_item)
        # case 3: union content type
        elif isinstance(inner_ctx, UnionCtx):
            old_item = self.__data[i]
            self.__setitem_union(i, old_item, new_item, inner_ctx)
        else:
            unreachable()

    @mut_reactive_op(inert_fn=list.__delitem__, unwrap=__data_unwrap)
    def __delitem__(self, index: SupportsIndex) -> None:
        i = self.__get_index(index)
        if self.__syncwave_ctx__.inner_ctx is None:
            del self.__data[i]
            return

        data_copy = self.__roundtrip_copy()
        del data_copy[i]
        self_copy = self.__new(data_copy)
        self.__syncwave_update__(self_copy)

    @reactive_op(inert_fn=list.__len__, unwrap=__data_unwrap)
    def __len__(self) -> int:
        return len(self.__data)

    @mut_reactive_op(inert_fn=list.insert, unwrap=__data_unwrap)
    def insert(self, index: SupportsIndex, value: VT) -> None:  # ruff: ignore[undocumented-public-method]
        i = self.__get_index(index)
        inner_ctx = self.__syncwave_ctx__.inner_ctx
        new_item = ingest(value, self.__syncwave_ctx__.item_type_adapter)

        if inner_ctx is None:
            self.__data.insert(i, new_item)
            return

        data_copy = self.__roundtrip_copy()
        data_copy.insert(i, new_item)
        self_copy = self.__new(data_copy)
        self.__syncwave_update__(self_copy)

    def __str__(self) -> str:
        return str(self.__data)

    def __repr__(self) -> str:
        tp_name, state = type(self).__qualname__, self.__syncwave_state__.value
        return f"<{tp_name} {self.__data!r} ({state})>"

    @reactive_op()
    def __copy__(self) -> Self:
        return self.__new(list(self))

    @reactive_op()
    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self.__new(deepcopy(self.__data, memo))

    copy = __copy__

    @reactive_op()
    def __eq__(self, other: object, /) -> bool:
        if isinstance(other, list):
            return self.__data == other
        if isinstance(other, SyncList):
            return self.__data == dead_guard(other).__data
        return NotImplemented

    __hash__ = None

    def __setitem_union(self, i: int, old: VT, new: VT, u_ctx: UnionCtx) -> None:
        old_is_reactive = isinstance(old, Reactive)
        new_is_reactive = isinstance(new, Reactive)
        same_type = type(old) is (new_type := type(new))

        if old_is_reactive and new_is_reactive and same_type:
            old.__syncwave_update__(new)
        else:
            if old_is_reactive:
                old.__syncwave_kill__()
            if new_is_reactive:
                new.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            self.__data[i] = new

    def __roundtrip_copy(self) -> list[VT]:
        ta = self.__syncwave_ctx__.item_type_adapter
        return [ta.validate_python(ta.dump_python(item)) for item in self.__data]

    @staticmethod
    def __get_index(index: Any) -> int:
        if isinstance(index, int):
            return index
        if isinstance(index, SupportsIndex):
            return index.__index__()
        if isinstance(index, slice):
            raise TypeError("Slice indices are not supported (yet).")
        tp_name = type(index).__qualname__
        raise TypeError(f"SyncList indices must be integers, not {tp_name}.")


@dataclass(frozen=True)
class SyncSetCtx(Context, Generic[VT]):
    tp: type[SyncSet[Any]]
    inner_ctx: None  # never holds reactive items
    item_type_adapter: TypeAdapter[VT]


class SyncSet(MutableSet[VT], Reactive, _syncwave_root=True):
    """A reactive set.

    `SyncSet` behaves like a regular `set`. Adding and discarding items trigger a write
    to the backing JSON file, and external changes to the file are reflected in place.

    `SyncSet` can only hold non-reactive, hashable values such as `str`, `int`, `UUID`,
    etc. Reactive types like `SyncCollection` or `SyncModel` are mutable and therefore
    not supported.

    Creating a `SyncSet` yourself, with the same arguments as `set`, gives an inert
    object: it behaves like a regular `set` and is not connected to any store. Nothing
    is validated while inert, so type arguments (e.g. `SyncSet[int]`) are only enforced
    when the value enters a store, which copies it.

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
        [SyncSet](https://syncwave.dev/usage/syncwave/)

    """

    # SyncSet cannot hold reactive items because a reactive item is mutable
    __data: set[VT]
    __syncwave_ctx__: SyncSetCtx[VT]
    __pydantic_serializer__: SchemaSerializer

    def __init__(self, iterable: Iterable[VT] = ()) -> None:
        """Create an inert `SyncSet`.

        Args:
            iterable: Initial items, same as for `set`.

        """
        self.__data = set(iterable)

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        set_schema = (
            handler.generate_schema(GenericAlias(set, args))
            if (args := type_args(src, SyncSet))
            else handler.generate_schema(set)
        )
        ser_schema = cs.wrap_serializer_function_ser_schema(
            _serializer_factory(cls, unwrap=cls.__data_unwrap), schema=set_schema
        )
        return cs.no_info_wrap_validator_function(
            _validator_factory(cls, new=cls.__new, unwrap=cls.__data_unwrap),
            set_schema,
            serialization=ser_schema,
        )

    @classmethod
    def __new(cls, data: set[VT]) -> Self:
        self = object.__new__(cls)
        self.__data = data
        return self

    def __data_unwrap(self) -> set[VT]:
        return self.__data

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncSetCtx[VT]) -> None:
        self.__syncwave_state__ = SyncState.LIVE
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        # no need to loop through items since set can't hold reactive items

    def __syncwave_kill__(self) -> None:
        # no need to loop through items since set can't hold reactive items
        self.__syncwave_state__ = SyncState.DEAD

    def __syncwave_update__(self, new: Self) -> None:
        self.__data = new.__data

    @reactive_op(inert_fn=set.__contains__, unwrap=__data_unwrap)
    def __contains__(self, value: object) -> bool:
        return value in self.__data

    @reactive_op(inert_fn=set.__iter__, unwrap=__data_unwrap)
    def __iter__(self) -> Iterator[VT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__data))

    @reactive_op(inert_fn=set.__len__, unwrap=__data_unwrap)
    def __len__(self) -> int:
        return len(self.__data)

    @mut_reactive_op(inert_fn=set.add, unwrap=__data_unwrap)
    def add(self, value: VT) -> None:  # ruff: ignore[undocumented-public-method]
        new_item = ingest(value, self.__syncwave_ctx__.item_type_adapter)
        self.__data.add(new_item)

    @mut_reactive_op(inert_fn=set.discard, unwrap=__data_unwrap)
    def discard(self, value: VT) -> None:  # ruff: ignore[undocumented-public-method]
        if value in self.__data:
            self.__data.discard(value)

    def __str__(self) -> str:
        return str(self.__data)

    def __repr__(self) -> str:
        tp_name, state = type(self).__qualname__, self.__syncwave_state__.value
        return f"<{tp_name} {self.__data!r} ({state})>"

    @reactive_op()
    def __copy__(self) -> Self:
        return self.__new(set(self))

    @reactive_op()
    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self.__new(deepcopy(self.__data, memo))

    copy = __copy__

    @reactive_op()
    def __eq__(self, other: object, /) -> bool:
        if isinstance(other, (set, frozenset)):
            return self.__data == other
        if isinstance(other, SyncSet):
            return self.__data == dead_guard(other).__data
        return NotImplemented

    __hash__ = None


ValFn, SerFn = cs.NoInfoWrapValidatorFunction, cs.WrapSerializerFunction


def _validator_factory(cls: type[CT], new: F[[Any], CT], unwrap: F[[CT], Any]) -> ValFn:
    def validate(value: Any, handler: cs.ValidatorFunctionWrapHandler) -> CT:
        if isinstance(value, Reactive):
            dead_guard(value)
            if isinstance(value, cls):
                value = unwrap(value)
        return new(handler(value))

    return validate


def _serializer_factory(cls: type[CT], unwrap: F[[CT], Any]) -> SerFn:
    def serialize(value: Any, handler: cs.SerializerFunctionWrapHandler) -> Any:
        if isinstance(value, cls):
            return handler(unwrap(value))
        return handler(value)  # plain-value fallback, e.g. an un-validated default

    return serialize


# Serialization "as Any" uses __pydantic_serializer__; schema hook is not called.
SyncDict.__pydantic_serializer__ = TypeAdapter(SyncDict[Any, Any]).serializer
SyncList.__pydantic_serializer__ = TypeAdapter(SyncList[Any]).serializer
SyncSet.__pydantic_serializer__ = TypeAdapter(SyncSet[Any]).serializer

SyncCollection.register(SyncDict)
SyncCollection.register(SyncList)
SyncCollection.register(SyncSet)


def _register_forbidden(*args: Any, **kwargs: Any) -> NoReturn:
    """SyncCollection does not support class registration."""
    raise TypeError("SyncCollection does not support class registration.")


SyncCollection.register = _register_forbidden  # ty: ignore[invalid-assignment]
