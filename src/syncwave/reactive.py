from __future__ import annotations

from collections.abc import Callable as F
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from threading import RLock
from typing import Any, Generic, Literal, ParamSpec, Protocol, TypeVar, cast, final
from typing_extensions import Self, TypeIs

from .errors import DeadReferenceError, unreachable

__all__ = ["Reactive", "SyncState"]


Context_T = TypeVar("Context_T", bound="Context")


class ReactiveLike(Protocol[Context_T]):
    __syncwave_ctx__: Context_T
    __syncwave_sref__: StoreRef
    __syncwave_state__: SyncState
    __syncwave_is_reactive__: Literal[True]

    def __syncwave_init__(self, sref: StoreRef, ctx: Context_T) -> None: ...
    def __syncwave_kill__(self) -> None: ...
    def __syncwave_update__(self: Self, new: Self) -> None: ...

    @property
    def sync_live(self) -> bool: ...
    @property
    def sync_state(self) -> SyncState: ...


@dataclass(frozen=True)
class Context:
    tp: type[ReactiveLike]


class UnionCtx(dict[type[ReactiveLike], Context]): ...


def union_key(value: ReactiveLike) -> type[ReactiveLike]:
    return value.__dict__.get("__syncwave_validated_as__", type(value))


@dataclass(frozen=True)
class StoreRef:
    lock: RLock
    on_change: F[[], None]


class SyncState(str, Enum):
    """Lifecycle state of a reactive object.

    - `INERT`: created directly, not connected to any store.
    - `LIVE`: connected to a store.
    - `DEAD`: removed from its store. Every operation raises `DeadReferenceError`.

    ---

    Abstract: Usage Documentation
        [Lifecycle](https://syncwave.dev/usage/reactivity/#lifecycle-and-syncstate)

    """

    INERT = "inert"
    LIVE = "live"
    DEAD = "dead"


class Reactive(Generic[Context_T]):
    """Base class of all reactive types.

    Mostly useful for type checks, e.g. `isinstance(value, Reactive)`. It can't be
    instantiated or subclassed directly. Reactive dataclasses don't inherit from it:
    check them with `is_sync_dataclass`.

    A reactive object is either inert, live, or dead (see `SyncState`).

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
        [The Reactive Types](https://syncwave.dev/usage/reactivity/#the-reactive-types)

    """

    __syncwave_ctx__: Context_T
    __syncwave_sref__: StoreRef
    __syncwave_state__: SyncState = SyncState.INERT
    __syncwave_is_reactive__: Literal[True] = True

    def __init_subclass__(cls, *, _syncwave: bool = False, **kwargs: Any) -> None:
        if Reactive in cls.__bases__ and not _syncwave:
            raise TypeError("`Reactive` cannot be subclassed directly.")
        cls.__syncwave_is_reactive__ = True
        super().__init_subclass__(**kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Raise `TypeError`: `Reactive` itself cannot be instantiated."""
        if type(self) is Reactive:
            raise TypeError("`Reactive` is a base class and cannot be instantiated.")

    def __syncwave_init__(self, sref: StoreRef, ctx: Context_T) -> None:
        raise NotImplementedError

    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    def __syncwave_update__(self: Self, new: Self) -> None:
        raise NotImplementedError

    @final
    @property
    def sync_live(self) -> bool:
        """Whether this object is live, i.e. connected to a store.

        `False` for both inert and dead objects. Use `sync_state` to tell them apart.

        Example:
        ```python
        from syncwave import SyncModel, Syncwave

        syncwave = Syncwave()


        @syncwave.store(name="customers")
        class Customer(SyncModel):
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
            [`sync_state` and `sync_live`](https://syncwave.dev/usage/reactivity/#sync_state-and-sync_live)

        """
        return self.__syncwave_state__ is SyncState.LIVE  # atomic, no need to lock

    @final
    @property
    def sync_state(self) -> SyncState:
        """The current state of this object (see `SyncState`).

        ---

        Abstract: Usage Documentation
            [`sync_state` and `sync_live`](https://syncwave.dev/usage/reactivity/#sync_state-and-sync_live)

        """
        return self.__syncwave_state__  # atomic, no need to lock


def is_reactive(o: Any) -> TypeIs[ReactiveLike]:
    return "__syncwave_is_reactive__" in type(o).__dict__


def is_reactive_cls(cls: type[Any]) -> TypeIs[type[ReactiveLike]]:
    # assumes `cls` is a class (called from trusted code)
    return "__syncwave_is_reactive__" in cls.__dict__


# identity function
def _id(value: Any) -> Any:
    return value


X = ParamSpec("X")
Y = TypeVar("Y")


def reactive_op(inert_fn: F | None = None, unwrap: F = _id) -> F[[F[X, Y]], F[X, Y]]:
    def decorator(fn: F[X, Y]) -> F[X, Y]:
        @wraps(fn)
        def wrapper(*args: X.args, **kwargs: X.kwargs) -> Y:
            self = cast(ReactiveLike, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    if inert_fn is None:
                        return fn(*args, **kwargs)
                    return inert_fn(unwrap(self), *args[1:], **kwargs)
                err = "A %s reactive object has no store reference."
                unreachable(err % (self.__syncwave_state__.value), from_=e)

            with sref.lock:
                if self.__syncwave_state__ is SyncState.DEAD:
                    raise DeadReferenceError(reference=self)
                if self.__syncwave_state__ is SyncState.LIVE:
                    return fn(*args, **kwargs)
                unreachable("An inert reactive object has a store reference.")

        return wrapper

    return decorator


def mut_reactive_op(inert_fn: F, unwrap: F = _id) -> F[[F[X, Y]], F[X, None]]:
    def decorator(fn: F[X, Y]) -> F[X, None]:
        @wraps(fn)
        def wrapper(*args: X.args, **kwargs: X.kwargs) -> None:
            self = cast(ReactiveLike, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    inert_fn(unwrap(self), *args[1:], **kwargs)
                    return
                err = "A %s reactive object has no store reference."
                unreachable(err % (self.__syncwave_state__.value), from_=e)

            with sref.lock:
                if self.__syncwave_state__ is SyncState.DEAD:
                    raise DeadReferenceError(reference=self)
                if self.__syncwave_state__ is SyncState.LIVE:
                    result = fn(*args, **kwargs)
                    if result is not None:
                        fn_name = getattr(fn, "__qualname__", repr(fn))
                        unreachable(f"Mutating operation `{fn_name}` returned a value.")
                    sref.on_change()
                    return
                unreachable("An inert reactive object has a store reference.")

        return wrapper

    return decorator


RT = TypeVar("RT", bound=ReactiveLike)


def dead_guard(value: RT) -> RT:
    if value.__syncwave_state__ is SyncState.DEAD:
        raise DeadReferenceError(reference=value)
    return value
