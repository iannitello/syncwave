from __future__ import annotations

from collections.abc import Callable as F
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from threading import RLock
from typing import Any, ParamSpec, TypeVar, cast, final

from .errors import DeadReferenceError, unreachable

__all__ = ["Reactive", "SyncState"]


C = TypeVar("C", bound="Context")
R = TypeVar("R", bound="Reactive")


class SyncState(str, Enum):
    """Lifecycle of a reactive object.

    `INERT`: never entered a store, behaves like the plain counterpart.
    `LIVE`: connected to a store.
    `DEAD`: removed from its store, every operation raises `DeadReferenceError`.
    """

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


class Reactive:
    """Base class shared by all reactive values in Syncwave.

    All reactive types (`SyncDict`, `SyncList`, `SyncSet`, and `SyncModel`) are
    subclasses of `Reactive`. You will mainly encounter it for type checks:
    `isinstance(value, Reactive)`. `Reactive` itself cannot be instantiated or
    subclassed directly; subclass one of the reactive types instead.

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

    __syncwave_state__: SyncState = SyncState.INERT
    __syncwave_sref__: StoreRef
    __syncwave_ctx__: Context

    def __init_subclass__(cls, *, _syncwave_root: bool = False, **kwargs: Any) -> None:
        if Reactive in cls.__bases__ and not _syncwave_root:
            raise TypeError("`Reactive` cannot be subclassed directly.")
        super().__init_subclass__(**kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Raise `TypeError`: `Reactive` itself cannot be instantiated."""
        if type(self) is Reactive:
            raise TypeError("`Reactive` is a base class and cannot be instantiated.")

    def __syncwave_init__(self, sref: StoreRef, ctx: C) -> None:
        raise NotImplementedError

    def __syncwave_kill__(self) -> None:
        raise NotImplementedError

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
        counterpart until a store ingests a copy of it. Use `sync_state` to distinguish
        inert from dead.

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
        return self.__syncwave_state__ is SyncState.LIVE  # atomic, no need to lock

    @final
    @property
    def sync_state(self) -> SyncState:
        """The current state of this reactive object. See `SyncState`."""
        return self.__syncwave_state__  # atomic, no need to lock


X = ParamSpec("X")
Y = TypeVar("Y")


# A reactive object has a store reference iff it is not inert.
_NO_SREF = "A {} reactive object has no store reference."
_INERT_WITH_SREF = "An inert reactive object has a store reference."


def _id(value: Any) -> Any:  # identity function
    return value


def reactive_op(inert_fn: F | None = None, unwrap: F = _id) -> F[[F[X, Y]], F[X, Y]]:
    def decorator(fn: F[X, Y]) -> F[X, Y]:
        @wraps(fn)
        def wrapper(*args: X.args, **kwargs: X.kwargs) -> Y:
            self = cast(R, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    if inert_fn is None:
                        return fn(*args, **kwargs)
                    return inert_fn(unwrap(self), *args[1:], **kwargs)
                unreachable(_NO_SREF.format(self.__syncwave_state__.value), from_=e)

            with sref.lock:
                if self.__syncwave_state__ is SyncState.DEAD:
                    raise DeadReferenceError(reference=self)
                if self.__syncwave_state__ is SyncState.LIVE:
                    return fn(*args, **kwargs)
                unreachable(_INERT_WITH_SREF)

        return wrapper

    return decorator


def mut_reactive_op(inert_fn: F, unwrap: F = _id) -> F[[F[X, Y]], F[X, None]]:
    def decorator(fn: F[X, Y]) -> F[X, None]:
        @wraps(fn)
        def wrapper(*args: X.args, **kwargs: X.kwargs) -> None:
            self = cast(R, args[0])
            try:
                sref = self.__syncwave_sref__
            except AttributeError as e:
                if self.__syncwave_state__ is SyncState.INERT:
                    inert_fn(unwrap(self), *args[1:], **kwargs)
                    return
                unreachable(_NO_SREF.format(self.__syncwave_state__.value), from_=e)

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
                unreachable(_INERT_WITH_SREF)

        return wrapper

    return decorator


def dead_guard(value: R) -> R:
    if value.__syncwave_state__ is SyncState.DEAD:
        raise DeadReferenceError(reference=value)
    return value
