from __future__ import annotations

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, Callable, TypeVar, final
from typing_extensions import ParamSpec, Self

from pydantic import TypeAdapter

Callback = Callable[[], None]


@dataclass(frozen=True)
class Context:
    lock: RLock
    on_change: Callback
    type_adapter: TypeAdapter[Any]


P = ParamSpec("P")
R = TypeVar("R")


class Reactive(metaclass=ABCMeta):
    __ctx: Context
    __syncwave_live__: bool = False

    @staticmethod
    def atomic(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(self: Reactive, *args: P.args, **kwargs: P.kwargs) -> R:
            with self.__ctx.lock:
                if not self.__syncwave_live__:
                    raise DeadReferenceError(reference=self)
                return func(self, *args, **kwargs)

        return wrapper

    @final
    @property
    def sync_live(self) -> bool:
        with self.__ctx.lock:
            return self.__syncwave_live__

    @final
    def __syncwave_init__(self, context: Context) -> None:
        self.__ctx = context
        self.__syncwave_bind__(context)

    @abstractmethod
    def __syncwave_bind__(self, context: Context) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_update__(self, new: Self) -> None:
        raise NotImplementedError


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: Reactive) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


atomic = Reactive.atomic
