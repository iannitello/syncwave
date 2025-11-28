# There's a problem with implementing SyncDict, SyncList, and SyncSet using the abstract
# collections.abc classes because the "free" mixins are not thread-safe.
# This is a temporary solution just to make it easier to implement.


from __future__ import annotations

from collections.abc import (
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
    Sequence,
    Set,
)
from typing import Any, NoReturn, TypeVar, Union, final, get_args
from typing_extensions import Self

from pydantic import GetCoreSchemaHandler as Handler
from pydantic_core import core_schema as cs

from .context import SyncDictContext, SyncListContext, SyncSetContext
from .reactive import Reactive, Reactivity, atomic

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])
VT = TypeVar("VT")


@final
class SyncCollection(Reactive):
    def __init_subclass__(cls: type[SyncCollection], /, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncCollection cannot be subclassed.")


class SyncDict(MutableMapping[KT, VT], Reactive):
    __ctx: SyncDictContext[KT, VT]
    __data: dict[KT, VT]

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncDict cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncDict], data: dict[KT, VT]) -> Self:
        self: SyncDict = object.__new__(cls)
        self.__data = data
        return self

    def __syncwave_init__(self, context: SyncDictContext[KT, VT]) -> None:
        if context.inner_reactivity is Reactivity.NON_REACTIVE:
            self.__syncwave_update__ = self.__update_non_reactive
            self.__setitem__ = self.__setitem_non_reactive
            self.__delitem__ = self.__delitem_non_reactive
        elif context.inner_reactivity is Reactivity.REACTIVE:
            self.__syncwave_update__ = self.__update_reactive
            self.__setitem__ = self.__setitem_reactive
            self.__delitem__ = self.__delitem_reactive
        elif context.inner_reactivity is Reactivity.MIXED:
            self.__syncwave_update__ = self.__update_mixed
            self.__setitem__ = self.__setitem_mixed
            self.__delitem__ = self.__delitem_mixed

        self.__ctx = context
        self.__syncwave_lock__ = context.lock
        self.__syncwave_live__ = True

    def __syncwave_update__(self, new: Mapping[KT, VT]) -> None: ...

    def __update_non_reactive(self, new: Mapping[KT, VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        self.__data = new.__data

    def __update_reactive(self, new: Mapping[KT, VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        old_keys, new_keys = set(self.__data.keys()), set(new.__data.keys())

        # items to update
        for key in new_keys & old_keys:
            old_item, new_item = self.__data[key], new.__data[key]
            old_item.__syncwave_update__(new_item)

        # items to add
        for key in new_keys - old_keys:
            new_item = new.__data[key]
            new_item.__syncwave_init__(self.__ctx.inner_ctx)
            self.__data[key] = new_item

        # items to remove
        for key in old_keys - new_keys:
            old_item = self.__data.pop(key)
            old_item.__syncwave_live__ = False

    def __update_mixed(self, new: Mapping[KT, VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        old_keys, new_keys = set(self.__data.keys()), set(new.__data.keys())

        # items to update
        for key in new_keys & old_keys:
            old_item, new_item = self.__data[key], new.__data[key]

            old_is_reactive = isinstance(old_item, Reactive)
            safe_to_update = old_is_reactive and isinstance(new_item, type(old_item))

            if safe_to_update:
                old_item.__syncwave_update__(new_item)
            else:
                if old_is_reactive:
                    old_item.__syncwave_live__ = False
                if isinstance(new_item, Reactive):
                    new_item.__syncwave_init__(self.__ctx.inner_ctx)
                self.__data[key] = new_item

        # items to add
        for key in new_keys - old_keys:
            new_item = new.__data[key]
            if isinstance(new_item, Reactive):
                new_item.__syncwave_init__(self.__ctx.inner_ctx)
            self.__data[key] = new_item

        # items to remove
        for key in old_keys - new_keys:
            old_item = self.__data.pop(key)
            if isinstance(old_item, Reactive):
                old_item.__syncwave_live__ = False

    @atomic
    def __getitem__(self, key: KT) -> VT:
        return self.__data[key]

    def __setitem__(self, key: KT, value: VT) -> None: ...

    @atomic
    def __setitem_non_reactive(self, key: KT, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        self.__data[key] = new_item
        self.__ctx.on_change()

    @atomic
    def __setitem_reactive(self, key: KT, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        new_item.__syncwave_init__(self.__ctx.inner_ctx)
        if key not in self.__data:
            self.__data[key] = new_item
        else:
            self.__data[key].__syncwave_update__(new_item)
        self.__ctx.on_change()

    @atomic
    def __setitem_mixed(self, key: KT, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        if isinstance(new_item, Reactive):
            new_item.__syncwave_init__(self.__ctx.inner_ctx)

        if key not in self.__data:
            self.__data[key] = new_item
            self.__ctx.on_change()
            return

        old_item = self.__data[key]

        old_is_reactive = isinstance(old_item, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_item, type(old_item))

        if safe_to_update:
            old_item.__syncwave_update__(new_item)
        else:
            if old_is_reactive:
                old_item.__syncwave_live__ = False
            self.__data[key] = new_item
        self.__ctx.on_change()

    def __delitem__(self, key: KT) -> None: ...

    @atomic
    def __delitem_non_reactive(self, key: KT) -> None:
        del self.__data[key]
        self.__ctx.on_change()

    @atomic
    def __delitem_reactive(self, key: KT) -> None:
        old_item = self.__data.pop(key)
        old_item.__syncwave_live__ = False
        self.__ctx.on_change()

    @atomic
    def __delitem_mixed(self, key: KT) -> None:
        old_item = self.__data.pop(key)
        if isinstance(old_item, Reactive):
            old_item.__syncwave_live__ = False
        self.__ctx.on_change()

    @atomic
    def __iter__(self) -> Iterator[KT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__data))

    @atomic
    def __len__(self) -> int:
        return len(self.__data)

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            mapping_t_schema = handler.generate_schema(Mapping[args[0], args[1]])
        else:
            mapping_t_schema = handler.generate_schema(Mapping)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, mapping_t_schema
        )
        instance_schema = cs.is_instance_schema(cls)
        return cs.union_schema([instance_schema, non_instance_schema])


class SyncList(MutableSequence[VT], Reactive):
    __ctx: SyncListContext[VT]
    __data: list[VT]

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncList cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncList], data: list[VT]) -> Self:
        self: SyncList = object.__new__(cls)
        self.__data = data
        return self

    def __syncwave_init__(self, context: SyncListContext[VT]) -> None:
        if context.inner_reactivity is Reactivity.NON_REACTIVE:
            self.__syncwave_update__ = self.__update_non_reactive
            self.__setitem__ = self.__setitem_non_reactive
            self.__delitem__ = self.__delitem_non_reactive
            self.insert = self.__insert_non_reactive
        elif context.inner_reactivity is Reactivity.REACTIVE:
            self.__syncwave_update__ = self.__update_reactive
            self.__setitem__ = self.__setitem_reactive
            self.__delitem__ = self.__delitem_reactive
            self.insert = self.__insert_reactive
        elif context.inner_reactivity is Reactivity.MIXED:
            self.__syncwave_update__ = self.__update_mixed
            self.__setitem__ = self.__setitem_mixed
            self.__delitem__ = self.__delitem_reactive  # same logic
            self.insert = self.__insert_reactive  # same logic

        self.__ctx = context
        self.__syncwave_lock__ = context.lock
        self.__syncwave_live__ = True

    def __syncwave_update__(self, new: Sequence[VT]) -> None: ...

    def __update_non_reactive(self, new: Sequence[VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        self.__data = new.__data

    def __update_reactive(self, new: Sequence[VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        old_len, new_len = len(self.__data), len(new.__data)

        # items to update
        for i in range(min(old_len, new_len)):
            old_item, new_item = self.__data[i], new.__data[i]
            old_item.__syncwave_update__(new_item)

        # items to add
        if new_len > old_len:
            for i in range(old_len, new_len):
                new_item = new.__data[i]
                new_item.__syncwave_init__(self.__ctx.inner_ctx)
                self.__data.append(new_item)

        # items to remove
        elif old_len > new_len:
            for _ in range(old_len - new_len):
                old_item = self.__data.pop()
                old_item.__syncwave_live__ = False

    def __update_mixed(self, new: Sequence[VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
        old_len, new_len = len(self.__data), len(new.__data)

        # items to update
        for i in range(min(old_len, new_len)):
            old_item, new_item = self.__data[i], new.__data[i]

            old_is_reactive = isinstance(old_item, Reactive)
            safe_to_update = old_is_reactive and isinstance(new_item, type(old_item))

            if safe_to_update:
                old_item.__syncwave_update__(new_item)
            else:
                if old_is_reactive:
                    old_item.__syncwave_live__ = False
                if isinstance(new_item, Reactive):
                    new_item.__syncwave_init__(self.__ctx.inner_ctx)
                self.__data[i] = new_item

        # items to add
        if new_len > old_len:
            for i in range(old_len, new_len):
                new_item = new.__data[i]
                if isinstance(new_item, Reactive):
                    new_item.__syncwave_init__(self.__ctx.inner_ctx)
                self.__data.append(new_item)

        # items to remove
        elif old_len > new_len:
            for _ in range(old_len - new_len):
                old_item = self.__data.pop()
                if isinstance(old_item, Reactive):
                    old_item.__syncwave_live__ = False

    @atomic
    def __getitem__(self, index: int) -> VT:
        return self.__data[index]

    def __setitem__(self, index: int, value: VT) -> None: ...

    @atomic
    def __setitem_non_reactive(self, index: int, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        self.__data[index] = new_item
        self.__ctx.on_change()

    @atomic
    def __setitem_reactive(self, index: int, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        new_item.__syncwave_init__(self.__ctx.inner_ctx)
        self.__data[index].__syncwave_update__(new_item)
        self.__ctx.on_change()

    @atomic
    def __setitem_mixed(self, index: int, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        if isinstance(new_item, Reactive):
            new_item.__syncwave_init__(self.__ctx.inner_ctx)

        old_item = self.__data[index]

        old_is_reactive = isinstance(old_item, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_item, type(old_item))

        if safe_to_update:
            old_item.__syncwave_update__(new_item)
        else:
            if old_is_reactive:
                old_item.__syncwave_live__ = False
            self.__data[index] = new_item
        self.__ctx.on_change()

    def __delitem__(self, index: int) -> None: ...

    @atomic
    def __delitem_non_reactive(self, index: int) -> None:
        del self.__data[index]
        self.__ctx.on_change()

    @atomic
    def __delitem_reactive(self, index: int) -> None:
        data_copy = self.__data.copy()
        del data_copy[index]
        self.__syncwave_update__(data_copy)
        self.__ctx.on_change()

    @atomic
    def __len__(self) -> int:
        return len(self.__data)

    def insert(self, index: int, value: VT) -> None: ...

    @atomic
    def __insert_non_reactive(self, index: int, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        self.__data.insert(index, new_item)
        self.__ctx.on_change()

    @atomic
    def __insert_reactive(self, index: int, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        data_copy = self.__data.copy()
        data_copy.insert(index, new_item)
        self.__syncwave_update__(data_copy)
        self.__ctx.on_change()

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            sequence_t_schema = handler.generate_schema(Sequence[args[0]])
        else:
            sequence_t_schema = handler.generate_schema(Sequence)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, sequence_t_schema
        )
        instance_schema = cs.is_instance_schema(cls)
        return cs.union_schema([instance_schema, non_instance_schema])


class SyncSet(MutableSet[VT], Reactive):
    # SyncSet cannot hold reactive items because a reactive item is mutable
    __ctx: SyncSetContext[VT]
    __data: set[VT]

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncSet cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncSet], data: set[VT]) -> Self:
        self: SyncSet = object.__new__(cls)
        self.__data = data
        return self

    def __syncwave_init__(self, context: SyncSetContext[VT]) -> None:
        self.__ctx = context
        self.__syncwave_lock__ = context.lock
        self.__syncwave_live__ = True

    def __syncwave_update__(self, new: Set[VT]) -> None:
        new = self.__ctx.type_adapter.validate_python(new)
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

    @atomic
    def add(self, value: VT) -> None:
        new_item = self.__ctx.inner_type_adapter.validate_python(value)
        self.__data.add(new_item)
        self.__ctx.on_change()

    @atomic
    def discard(self, value: VT) -> None:
        if value in self.__data:
            self.__data.discard(value)
            self.__ctx.on_change()

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        args = get_args(src)
        if args:
            set_t_schema = handler.generate_schema(Set[args[0]])
        else:
            set_t_schema = handler.generate_schema(Set)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, set_t_schema
        )
        instance_schema = cs.is_instance_schema(cls)
        return cs.union_schema([instance_schema, non_instance_schema])


SyncCollection.register(SyncDict)
SyncCollection.register(SyncList)
SyncCollection.register(SyncSet)


def register(*args: Any, **kwargs: Any) -> NoReturn:
    """SyncCollection does not support class registration."""
    raise TypeError("SyncCollection does not support class registration.")


SyncCollection.register = register
