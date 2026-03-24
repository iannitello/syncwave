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
        [Reactive](https://placeholder.dev/usage/syncwave/)

    """

    __syncwave_sref__: StoreRef
    __syncwave_ctx__: Context
    __syncwave_live__: bool

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:  # noqa: D102
        raise TypeError("Reactive types can never be instantiated directly.")

    @final
    @property
    def sync_live(self) -> bool:
        """Whether this reactive object is still connected to the store.

        Returns `False` once the object has been removed or replaced in its parent
        store, for example because a key was deleted from a `SyncDict`. After that, any
        operation on the object raises `DeadReferenceError`.

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
            [Reactive](https://placeholder.dev/usage/syncwave/)

        """
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
    """Raised when an operation is attempted on a dead reactive object.

    A reactive object becomes dead when it is removed from the store, for example
    by deleting a key from a `SyncDict` or by deleting the store entirely. Catching
    this error is one way to check whether a reference is still valid, though
    checking `sync_live` first is usually cleaner.

    Example:
    ```python
    from pydantic import BaseModel
    from syncwave import DeadReferenceError, Syncwave

    syncwave = Syncwave()


    @syncwave.register(name="customers")
    class Customer(BaseModel):
        name: str
        age: int


    customers = syncwave["customers"]
    customers.append({"name": "Alice", "age": 30})
    alice = customers[0]
    del customers[0]

    try:
        alice.age = 31
    except DeadReferenceError as e:
        print(e)
    ```

    """

    def __init__(self, *, reference: Reactive) -> None:  # noqa: D107
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


def unreachable() -> NoReturn:
    raise RuntimeError("Internal Error")
