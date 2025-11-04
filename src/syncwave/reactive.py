from __future__ import annotations

from abc import ABCMeta, abstractmethod
from functools import wraps
from threading import RLock
from typing import Callable, ParamSpec, TypeVar, final

Callback = Callable[[], None]


class Reactive(metaclass=ABCMeta):
    __syncwave_lock__: RLock
    __syncwave_live__: bool = False
    __syncwave_on_change__: Callback

    @final
    @property
    def sync_live(self) -> bool:
        with self.__syncwave_lock__:
            return self.__syncwave_live__

    @abstractmethod
    def __syncwave_abc_marker__(self) -> None:
        raise NotImplementedError


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: Reactive) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


P = ParamSpec("P")
R = TypeVar("R")


def live_only(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(self: Reactive, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.__syncwave_lock__:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            return func(self, *args, **kwargs)

    return wrapper
