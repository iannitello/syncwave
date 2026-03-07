from __future__ import annotations

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, Callable, NoReturn, TypeVar, final
from typing_extensions import ParamSpec

__all__ = ["DeadReferenceError", "Reactive"]


@dataclass(frozen=True)
class StoreRef:
    lock: RLock
    on_change: Callable[[], None]


@dataclass(frozen=True)
class Context:
    tp: type[Reactive]


class ContextMap(dict[type["Reactive"], Context]): ...


CtxSubCls = TypeVar("CtxSubCls", bound=Context)
ReactiveSubCls = TypeVar("ReactiveSubCls", bound="Reactive")


class Reactive(metaclass=ABCMeta):
    __syncwave_sref__: StoreRef
    __syncwave_ctx__: Context
    __syncwave_live__: bool

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("Reactive types can never be instantiated directly.")

    @final
    @property
    def sync_live(self) -> bool:
        return self.__syncwave_live__  # atomic, no need to lock

    @abstractmethod
    def __syncwave_init__(self, sref: StoreRef, ctx: CtxSubCls) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_update__(self, new: ReactiveSubCls) -> None:
        raise NotImplementedError


P = ParamSpec("P")
R = TypeVar("R")


def atomic(fn: Callable[P, R]) -> Callable[P, R]:
    @wraps(fn)
    def wrapper(self: Reactive, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.__syncwave_sref__.lock:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            return fn(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    return wrapper  # ty: ignore[invalid-return-type]


def mut_atomic(fn: Callable[P, R]) -> Callable[P, None]:
    @wraps(fn)
    def wrapper(self: Reactive, *args: P.args, **kwargs: P.kwargs) -> None:
        with self.__syncwave_sref__.lock:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            result = fn(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]
            if result is not None:
                unreachable()
            self.__syncwave_sref__.on_change()

    return wrapper  # ty: ignore[invalid-return-type]


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: Reactive) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


def unreachable() -> NoReturn:
    raise RuntimeError("Internal Error")
