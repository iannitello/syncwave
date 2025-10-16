from __future__ import annotations

from collections.abc import Iterator, MutableMapping, MutableSequence, MutableSet
from threading import RLock
from typing import Any, NoReturn, TypeVar, Union
from typing_extensions import Self

from .reactive import Reactive

JSONKey = Union[str, int, float, bool, None]
VT = TypeVar("VT")


class SyncCollection(Reactive):
    def __init_subclass__(cls: type[SyncCollection], /, **kwargs: Any) -> None:
        raise TypeError("SyncCollection cannot be subclassed.")


# There's a problem with implementing SyncDict, SyncList, and SyncSet using the abstract
# collections.abc classes because the "free" mixins are not thread-safe.
# This is a temporary solution just to make it easier to implement.


class SyncDict(MutableMapping[JSONKey, VT], Reactive):
    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncDict cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncDict], data: dict[JSONKey, VT]) -> Self:
        self: SyncDict = object.__new__(cls)
        self.__syncwave_lock__ = RLock()
        self.__syncwave_data__ = data
        self.__init__()
        return self

    def __init__(self) -> None:
        """
        Initialization hook. Override this method to perform initialization logic.
        No parameters can be passed to this method.
        """
        pass

    def __getitem__(self, key: JSONKey) -> VT:
        with self.__syncwave_lock__:
            return self.__syncwave_data__[key]

    def __setitem__(self, key: JSONKey, value: VT) -> None:
        with self.__syncwave_lock__:
            self.__syncwave_data__[key] = value

    def __delitem__(self, key: JSONKey) -> None:
        with self.__syncwave_lock__:
            del self.__syncwave_data__[key]

    def __iter__(self) -> Iterator[JSONKey]:
        with self.__syncwave_lock__:
            # first convert to a list so the iterator is over a frozen object
            return iter(list(self.__syncwave_data__))

    def __len__(self) -> int:
        with self.__syncwave_lock__:
            return len(self.__syncwave_data__)

    def __syncwave_abc_marker__(self) -> None:
        pass

    # repr to be implemented
    # str to be implemented


class SyncList(MutableSequence[VT], Reactive):
    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncList cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncList], data: list[VT]) -> Self:
        self: SyncList = object.__new__(cls)
        self.__syncwave_lock__ = RLock()
        self.__syncwave_data__ = data
        self.__init__()
        return self

    def __init__(self) -> None:
        """
        Initialization hook. Override this method to perform initialization logic.
        No parameters can be passed to this method.
        """
        pass

    def __getitem__(self, index: int) -> VT:
        with self.__syncwave_lock__:
            return self.__syncwave_data__[index]

    def __setitem__(self, index: int, value: VT) -> None:
        with self.__syncwave_lock__:
            self.__syncwave_data__[index] = value

    def __delitem__(self, index: int) -> None:
        with self.__syncwave_lock__:
            del self.__syncwave_data__[index]

    def __len__(self) -> int:
        with self.__syncwave_lock__:
            return len(self.__syncwave_data__)

    def insert(self, index: int, value: VT) -> None:
        with self.__syncwave_lock__:
            self.__syncwave_data__.insert(index, value)

    def __syncwave_abc_marker__(self) -> None:
        pass

    # repr to be implemented
    # str to be implemented


class SyncSet(MutableSet[VT], Reactive):
    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncSet cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls: type[SyncSet], data: set[VT]) -> Self:
        self: SyncSet = object.__new__(cls)
        self.__syncwave_lock__ = RLock()
        self.__syncwave_data__ = data
        self.__init__()
        return self

    def __init__(self) -> None:
        """
        Initialization hook. Override this method to perform initialization logic.
        No parameters can be passed to this method.
        """
        pass

    def __contains__(self, value: VT) -> bool:
        with self.__syncwave_lock__:
            return value in self.__syncwave_data__

    def __iter__(self) -> Iterator[VT]:
        with self.__syncwave_lock__:
            # first convert to a list so the iterator is over a frozen object
            return iter(list(self.__syncwave_data__))

    def __len__(self) -> int:
        with self.__syncwave_lock__:
            return len(self.__syncwave_data__)

    def add(self, value: VT) -> None:
        with self.__syncwave_lock__:
            self.__syncwave_data__.add(value)

    def discard(self, value: VT) -> None:
        with self.__syncwave_lock__:
            self.__syncwave_data__.discard(value)

    def __syncwave_abc_marker__(self) -> None:
        pass


SyncCollection.register(SyncDict)
SyncCollection.register(SyncList)
SyncCollection.register(SyncSet)
