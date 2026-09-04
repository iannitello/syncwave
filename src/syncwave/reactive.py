from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections.abc import Callable as F
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from threading import RLock
from typing import Any, NoReturn, ParamSpec, TypeVar, final
from typing_extensions import TypeIs

from pydantic import SerializerFunctionWrapHandler as Handler

from .errors import DeadReferenceError, unreachable

__all__ = ["Reactive"]


C = TypeVar("C", bound="Context")
R = TypeVar("R", bound="Reactive")


class State(str, Enum):
    INERT = "inert"
    LIVE = "live"
    DEAD = "dead"


@dataclass(frozen=True)
class StoreRef:
    lock: RLock
    on_change: F[[], None]


@dataclass(frozen=True)
class Context:
    tp: type[Reactive]


class UnionCtx(dict[type["Reactive"], Context]): ...


class Reactive(metaclass=ABCMeta):
    """Base class shared by all reactive values in Syncwave.

    All reactive types (`SyncDict`, `SyncList`, `SyncSet` and `SyncModel`) are
    subclasses of `Reactive`. You will mainly encounter it for type checks:
    `isinstance(value, Reactive)`.

    A reactive object can become dead when the corresponding store entry is removed or
    replaced. Once dead, `sync_live` returns `False` and any further operation raises
    `DeadReferenceError`.

    Example:
    ```python
    from syncwave import Reactive, SyncDict, Syncwave

    syncwave = Syncwave()
    sync_dict = syncwave.create_store(SyncDict[str, int], name="sync_dict")
    print(isinstance(sync_dict, Reactive))  # True
    print(issubclass(SyncDict, Reactive))  # True
    ```

    ---

    Abstract: Usage Documentation
        [Reactive](https://syncwave.dev/usage/syncwave/)

    """

    __syncwave_reactive__ = True
    __syncwave_state__: State = State.INERT
    __syncwave_sref__: StoreRef
    __syncwave_ctx__: Context

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:  # ruff: ignore[undocumented-public-method]
        raise TypeError(
            f"`{cls.__qualname__}` cannot be instantiated directly. "
            "Reactive instances are created automatically when a value enters a store."
        )

    @abstractmethod
    def __syncwave_init__(self, sref: StoreRef, ctx: C) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def __syncwave_update__(self, new: R) -> None:
        raise NotImplementedError

    @final
    @property
    def sync_live(self) -> bool:
        """Whether this reactive object is connected to a store and syncing.

        Returns `False` in two cases. The object is dead: it has been removed or
        replaced in its parent store, for example because a key was deleted from a
        `SyncDict`, and any further operation on it raises `DeadReferenceError`. Or
        the object is inert: it never entered a store, and it behaves like its plain
        counterpart until a store ingests a copy of it.

        Example:
        ```python
        from pydantic import BaseModel
        from syncwave import Syncwave

        syncwave = Syncwave()


        @syncwave.register(name="customers")
        class Customer(BaseModel):
            name: str
            age: int


        customers = syncwave["customers"]
        customers.append({"name": "Alice", "age": 30})
        alice = customers[0]
        print(alice.sync_live)  # True
        del customers[0]
        print(alice.sync_live)  # False
        ```

        ---

        Abstract: Usage Documentation
            [Reactive](https://syncwave.dev/usage/syncwave/)

        """
        return self.__syncwave_state__ is State.LIVE  # atomic, no need to lock


def is_reactive(value: Any) -> TypeIs[Reactive]:
    # Internal faster replacement for `isinstance(value, Reactive)`.
    # See https://github.com/python/cpython/issues/92810
    # This intentionally returns False for `Syncwave` (virtual subclass of `Reactive`).
    return getattr(type(value), "__syncwave_reactive__", False)


X = ParamSpec("X")
Y = TypeVar("Y")


# A reactive object has a store reference iff it is not inert.
_NO_SREF = "A {} reactive object has no store reference."
_INERT_WITH_SREF = "An inert reactive object has a store reference."


def _id(value: Any) -> Any:  # identity function
    return value


def reactive_op(inert_fn: F, unwrap: F[[R], Any] = _id) -> F[[F[X, Y]], F[X, Y]]:
    def decorator(fn: F[X, Y]) -> F[X, Y]:
        @wraps(fn)
        def wrapper(self: R, *args: X.args, **kwargs: X.kwargs) -> Y:
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is State.INERT:
                    return inert_fn(unwrap(self), *args, **kwargs)
                unreachable(_NO_SREF.format(self.__syncwave_state__.value), from_=e)

            with sref.lock:
                if self.__syncwave_state__ is State.DEAD:
                    raise DeadReferenceError(reference=self)
                if self.__syncwave_state__ is State.LIVE:
                    return fn(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]
                unreachable(_INERT_WITH_SREF)

        return wrapper  # ty: ignore[invalid-return-type]

    return decorator


def mut_reactive_op(inert_fn: F, unwrap: F[[R], Any] = _id) -> F[[F[X, Y]], F[X, None]]:
    def decorator(fn: F[X, Y]) -> F[X, None]:
        @wraps(fn)
        def wrapper(self: R, *args: X.args, **kwargs: X.kwargs) -> None:
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is State.INERT:
                    inert_fn(unwrap(self), *args, **kwargs)
                    return
                unreachable(_NO_SREF.format(self.__syncwave_state__.value), from_=e)

            with sref.lock:
                if self.__syncwave_state__ is State.DEAD:
                    raise DeadReferenceError(reference=self)
                if self.__syncwave_state__ is State.LIVE:
                    result = fn(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]
                    if result is not None:
                        fn_name = getattr(fn, "__qualname__", repr(fn))
                        unreachable(f"Mutating operation `{fn_name}` returned a value.")
                    sref.on_change()
                    return
                unreachable(_INERT_WITH_SREF)

        return wrapper  # ty: ignore[invalid-return-type]

    return decorator


def dead_guard(value: R) -> R:
    if value.__syncwave_state__ is State.DEAD:
        raise DeadReferenceError(reference=value)
    return value


def ser_factory(unwrap: F[[R], Any] = _id) -> F[[Any, Handler], Any]:
    def serialize(value: Any, handler: Handler) -> Any:
        if is_reactive(value):
            return handler(unwrap(value))
        return handler(value)  # plain-value fallback, e.g. an un-validated default

    return serialize
