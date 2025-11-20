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
from typing import Any, NoReturn, TypeVar, final, get_args
from typing_extensions import Self

from pydantic import GetCoreSchemaHandler as Handler
from pydantic import TypeAdapter
from pydantic_core import core_schema as cs

from .reactive import Reactive, atomic

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])
VT = TypeVar("VT")


@final
class SyncCollection(Reactive):
    def __init_subclass__(cls: type[SyncCollection], /, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncCollection cannot be subclassed.")


# There's a problem with implementing SyncDict, SyncList, and SyncSet using the abstract
# collections.abc classes because the "free" mixins are not thread-safe.
# This is a temporary solution just to make it easier to implement.


class SyncDict(MutableMapping[KT, VT], Reactive):
    __syncwave_data__: dict[KT, VT]
    __type_adapter: TypeAdapter[SyncDict[KT, VT]]
    __child_type_adapter: TypeAdapter[VT]
    __holds_reactive: bool

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncDict cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncDict], data: dict[KT, VT]) -> Self:
        self: SyncDict = object.__new__(cls)
        self.__syncwave_data__ = data
        return self

    def __syncwave_init__(self) -> None:
        # TODO: Implement this for real
        self.__type_adapter = TypeAdapter(...)
        self.__child_type_adapter = TypeAdapter(...)
        self.__holds_reactive = True

    def __syncwave_update__(self, new: SyncDict[KT, VT] | Mapping[KT, VT]) -> None:
        new = self.__type_adapter.validate_python(new)

        if not self.__holds_reactive:
            self.__syncwave_data__ = new.__syncwave_data__
            return

        old_keys = set(self.__syncwave_data__.keys())
        new_keys = set(new.__syncwave_data__.keys())

        # items to update
        for key in new_keys & old_keys:
            new_item = new.__syncwave_data__[key]
            self.__syncwave_data__[key].__syncwave_update__(new_item)

        # items to add
        for key in new_keys - old_keys:
            self.__syncwave_data__[key] = new.__syncwave_data__[key]

        # items to remove
        for key in old_keys - new_keys:
            old_item = self.__syncwave_data__.pop(key)
            old_item.__syncwave_live__ = False

    @atomic
    def __getitem__(self, key: KT) -> VT:
        return self.__syncwave_data__[key]

    @atomic
    def __setitem__(self, key: KT, value: VT) -> None:
        new_item = self.__child_type_adapter.validate_python(value)

        if not self.__holds_reactive:
            self.__syncwave_data__[key] = new_item
            self.__syncwave_on_change__()
            return

        if key in self.__syncwave_data__:
            self.__syncwave_data__[key].__syncwave_update__(new_item)
        else:
            self.__syncwave_data__[key] = new_item
        self.__syncwave_on_change__()

    @atomic
    def __delitem__(self, key: KT) -> None:
        if not self.__holds_reactive:
            del self.__syncwave_data__[key]
            self.__syncwave_on_change__()
            return

        old_item = self.__syncwave_data__.pop(key)
        old_item.__syncwave_live__ = False
        self.__syncwave_on_change__()

    @atomic
    def __iter__(self) -> Iterator[KT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__syncwave_data__))

    @atomic
    def __len__(self) -> int:
        return len(self.__syncwave_data__)

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        instance_schema = cs.is_instance_schema(cls)
        args = get_args(src)
        if args:
            mapping_t_schema = handler.generate_schema(MutableMapping[args[0], args[1]])
        else:
            mapping_t_schema = handler.generate_schema(MutableMapping)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, mapping_t_schema
        )
        return cs.union_schema([instance_schema, non_instance_schema])

    def __syncwave_abc_marker__(self) -> None:
        pass


class SyncList(MutableSequence[VT], Reactive):
    __syncwave_data__: list[VT]
    __type_adapter: TypeAdapter[SyncList[VT]]
    __child_type_adapter: TypeAdapter[VT]
    __holds_reactive: bool

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncList cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncList], data: list[VT]) -> Self:
        self: SyncList = object.__new__(cls)
        self.__syncwave_data__ = data
        return self

    def __syncwave_init__(self) -> None:
        # TODO: Implement this for real
        self.__type_adapter = TypeAdapter(...)
        self.__child_type_adapter = TypeAdapter(...)
        self.__holds_reactive = True

    def __syncwave_update__(self, new: SyncList[VT] | Sequence[VT]) -> None:
        new = self.__type_adapter.validate_python(new)

        if not self.__holds_reactive:
            self.__syncwave_data__ = new.__syncwave_data__
            return

        old_len = len(self.__syncwave_data__)
        new_len = len(new.__syncwave_data__)

        # items to update
        for i in range(min(old_len, new_len)):
            self.__syncwave_data__[i].__syncwave_update__(new.__syncwave_data__[i])

        # items to add
        if new_len > old_len:
            for i in range(old_len, new_len):
                self.__syncwave_data__.append(new.__syncwave_data__[i])

        # items to remove
        elif old_len > new_len:
            for _ in range(old_len - new_len):
                old_item = self.__syncwave_data__.pop()
                old_item.__syncwave_live__ = False

    @atomic
    def __getitem__(self, index: int) -> VT:
        return self.__syncwave_data__[index]

    @atomic
    def __setitem__(self, index: int, value: VT) -> None:
        new_item = self.__child_type_adapter.validate_python(value)

        if not self.__holds_reactive:
            self.__syncwave_data__[index] = new_item
            self.__syncwave_on_change__()
            return

        self.__syncwave_data__[index].__syncwave_update__(new_item)
        self.__syncwave_on_change__()

    @atomic
    def __delitem__(self, index: int) -> None:
        if not self.__holds_reactive:
            del self.__syncwave_data__[index]
            self.__syncwave_on_change__()
            return

        data_copy = self.__syncwave_data__.copy()
        del data_copy[index]
        self.__syncwave_update__(data_copy)
        self.__syncwave_on_change__()

    @atomic
    def __len__(self) -> int:
        return len(self.__syncwave_data__)

    @atomic
    def insert(self, index: int, value: VT) -> None:
        new_item = self.__child_type_adapter.validate_python(value)

        if not self.__holds_reactive:
            self.__syncwave_data__.insert(index, new_item)
            self.__syncwave_on_change__()
            return

        data_copy = self.__syncwave_data__.copy()
        data_copy.insert(index, new_item)
        self.__syncwave_update__(data_copy)
        self.__syncwave_on_change__()

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        instance_schema = cs.is_instance_schema(cls)

        args = get_args(src)
        if args:
            sequence_t_schema = handler.generate_schema(MutableSequence[args[0]])
        else:
            sequence_t_schema = handler.generate_schema(MutableSequence)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, sequence_t_schema
        )
        return cs.union_schema([instance_schema, non_instance_schema])

    def __syncwave_abc_marker__(self) -> None:
        pass


class SyncSet(MutableSet[VT], Reactive):
    # SyncSet cannot hold reactive items because a reactive item is mutable
    __syncwave_data__: set[VT]
    __type_adapter: TypeAdapter[SyncSet[VT]]
    __child_type_adapter: TypeAdapter[VT]

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncSet cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncSet], data: set[VT]) -> Self:
        self: SyncSet = object.__new__(cls)
        self.__syncwave_data__ = data
        return self

    def __syncwave_init__(self) -> None:
        # TODO: Implement this for real
        self.__type_adapter = TypeAdapter(...)
        self.__child_type_adapter = TypeAdapter(...)

    def __syncwave_update__(self, new: SyncSet[VT] | Set[VT]) -> None:
        new = self.__type_adapter.validate_python(new)
        self.__syncwave_data__ = new.__syncwave_data__

    @atomic
    def __contains__(self, value: object) -> bool:
        return value in self.__syncwave_data__

    @atomic
    def __iter__(self) -> Iterator[VT]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__syncwave_data__))

    @atomic
    def __len__(self) -> int:
        return len(self.__syncwave_data__)

    @atomic
    def add(self, value: VT) -> None:
        new_item = self.__child_type_adapter.validate_python(value)
        self.__syncwave_data__.add(new_item)
        self.__syncwave_on_change__()

    @atomic
    def discard(self, value: VT) -> None:
        if value in self.__syncwave_data__:
            self.__syncwave_data__.discard(value)
            self.__syncwave_on_change__()

    # __repr__ to be implemented
    # __str__ to be implemented
    # __eq__ to be implemented?
    # __hash__ to be implemented?

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        instance_schema = cs.is_instance_schema(cls)

        args = get_args(src)
        if args:
            set_t_schema = handler.generate_schema(MutableSet[args[0]])
        else:
            set_t_schema = handler.generate_schema(MutableSet)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, set_t_schema
        )
        return cs.union_schema([instance_schema, non_instance_schema])

    def __syncwave_abc_marker__(self) -> None:
        pass


SyncCollection.register(SyncDict)
SyncCollection.register(SyncList)
SyncCollection.register(SyncSet)


def register(*args: Any, **kwargs: Any) -> NoReturn:
    """Cannot register a virtual subclass of SyncCollection."""
    raise TypeError("Cannot register a virtual subclass of SyncCollection.")


SyncCollection.register = register
