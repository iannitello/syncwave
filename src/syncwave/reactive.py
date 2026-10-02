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


CTX = TypeVar("CTX", bound="Context")


class ReactiveProtocol(Protocol[CTX]):
    __syncwave_ctx__: CTX
    __syncwave_sref__: StoreRef
    __syncwave_state__: SyncState
    __syncwave_is_reactive__: Literal[True]

    def __syncwave_init__(self, sref: StoreRef, ctx: CTX) -> None: ...
    def __syncwave_kill__(self) -> None: ...
    def __syncwave_update__(self: Self, new: Self) -> None: ...

    @property
    def sync_live(self) -> bool: ...
    @property
    def sync_state(self) -> SyncState: ...


@dataclass(frozen=True)
class Context:
    tp: type[ReactiveProtocol]


class UnionCtx(dict[type[ReactiveProtocol], Context]): ...


@dataclass(frozen=True)
class StoreRef:
    lock: RLock
    on_change: F[[], None]


class SyncState(str, Enum):
    """Lifecycle of a reactive object.

    - `INERT`: never entered a store, behaves like the plain counterpart.
    - `LIVE`: connected to a store.
    - `DEAD`: removed from its store, every operation raises `DeadReferenceError`.
    """

    INERT = "inert"
    LIVE = "live"
    DEAD = "dead"


class Reactive(Generic[CTX]):
    """Base class shared by all reactive values in Syncwave.

    All reactive types are subclasses of `Reactive`. You will mainly encounter it for
    type checks: `isinstance(value, Reactive)`. `Reactive` itself cannot be instantiated
    or subclassed directly; subclass one of the reactive types instead. Reactive
    dataclasses (see `sync_dataclass`) keep their own MRO, so they are the exception:
    check them with `is_sync_dataclass`.

    A reactive object is always in one of three states, available as `sync_state`:

    - Inert: the object was created directly (e.g. `SyncList([1, 2])`) and never
      entered a store. It behaves like its plain counterpart.
    - Live: the object belongs to a store. Changes made through it are validated and
      written to the JSON file, and changes to the file are applied to it.
    - Dead: the object was removed or replaced in its store. `sync_live` returns
      `False` and any further operation raises `DeadReferenceError`.

    A value enters a store as a copy: the store creates a live object holding the same
    data, and the original stays inert for good. Read the store to get the live object.

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

    __syncwave_ctx__: CTX
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

    def __syncwave_init__(self, sref: StoreRef, ctx: CTX) -> None:
        raise NotImplementedError

    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

    def __syncwave_update__(self: Self, new: Self) -> None:
        raise NotImplementedError

    @final
    @property
    def sync_live(self) -> bool:
        """Whether this reactive object is connected to a store and syncing.

        Returns `False` in two cases. The object is dead: it has been removed or
        replaced in its parent store, for example because a key was deleted from a
        `SyncDict`, and any further operation on it raises `DeadReferenceError`. Or
        the object is inert: it never entered a store, and it behaves like its plain
        counterpart until a store ingests a copy of it. Use `sync_state` to distinguish
        inert from dead.

        Example:
        ```python
        from syncwave import SyncModel, Syncwave

        syncwave = Syncwave()


        @syncwave.register(name="customers")
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
            [Reactive](https://syncwave.dev/usage/syncwave/)

        """
        return self.__syncwave_state__ is SyncState.LIVE  # atomic, no need to lock

    @final
    @property
    def sync_state(self) -> SyncState:
        """The current state of this reactive object. See `SyncState`."""
        return self.__syncwave_state__  # atomic, no need to lock


def is_reactive(o: Any) -> TypeIs[ReactiveProtocol]:
    return "__syncwave_is_reactive__" in type(o).__dict__


def is_reactive_cls(cls: type[Any]) -> TypeIs[type[ReactiveProtocol]]:
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
            self = cast(ReactiveProtocol, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    if inert_fn is None:
                        return fn(*args, **kwargs)
                    return inert_fn(unwrap(self), *args[1:], **kwargs)
                err = "A {} reactive object has no store reference."
                unreachable(err.format(self.__syncwave_state__.value), from_=e)

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
            self = cast(ReactiveProtocol, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    inert_fn(unwrap(self), *args[1:], **kwargs)
                    return
                err = "A {} reactive object has no store reference."
                unreachable(err.format(self.__syncwave_state__.value), from_=e)

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


RP = TypeVar("RP", bound=ReactiveProtocol)


def dead_guard(value: RP) -> RP:
    if value.__syncwave_state__ is SyncState.DEAD:
        raise DeadReferenceError(reference=value)
    return value
