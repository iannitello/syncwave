from __future__ import annotations

from abc import ABCMeta, abstractmethod
from functools import wraps
from threading import RLock
from typing import Callable, ParamSpec, TypeVar, final
from typing_extensions import Self

Callback = Callable[[], None]


class ReactiveBase(metaclass=ABCMeta):
    __syncwave_lock__: RLock
    __syncwave_live__: bool = False

    @final
    @property
    def sync_live(self) -> bool:
        with self.__syncwave_lock__:
            return self.__syncwave_live__

    @abstractmethod
    def __syncwave_abc_marker__(self) -> None:
        raise NotImplementedError


class Reactive(ReactiveBase):
    __syncwave_on_change__: Callback

    @abstractmethod
    def __syncwave_update__(self, new: Self) -> None:
        raise NotImplementedError


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: ReactiveBase) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


P = ParamSpec("P")
R = TypeVar("R")


def atomic(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(self: ReactiveBase, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.__syncwave_lock__:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            return func(self, *args, **kwargs)

    return wrapper
