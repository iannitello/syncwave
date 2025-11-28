from __future__ import annotations

from abc import ABCMeta, abstractmethod
from enum import Enum, auto
from functools import wraps
from threading import RLock
from typing import Callable, TypeVar, final
from typing_extensions import ParamSpec, Self

from .context import Context


class Reactivity(Enum):
    NON_REACTIVE = auto()
    REACTIVE = auto()
    MIXED = auto()


class Reactive(metaclass=ABCMeta):
    __syncwave_live__: bool
    __syncwave_lock__: RLock

    @final
    @property
    def sync_live(self) -> bool:
        with self.__syncwave_lock__:
            return self.__syncwave_live__

    @abstractmethod
    def __syncwave_init__(self, context: Context) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_update__(self, new: Self) -> None:
        raise NotImplementedError


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: Reactive) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


P = ParamSpec("P")
R = TypeVar("R")
WrappedMethod = Callable[P, R]


def atomic(func: WrappedMethod) -> WrappedMethod:
    @wraps(func)
    def wrapper(self: Reactive, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.__syncwave_lock__:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            return func(self, *args, **kwargs)

    return wrapper
