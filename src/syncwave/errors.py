from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

__all__ = ["DeadReferenceError"]

if TYPE_CHECKING:
    from .reactive import Reactive


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

    def __init__(self, *, reference: Reactive) -> None:  # ruff: ignore[undocumented-public-init]
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


class _InternalError(RuntimeError): ...


def unreachable(detail: str = "", /, *, from_: Exception | None = None) -> NoReturn:
    message = (detail or "Unreachable code was reached.") + "\n\n"
    message += "This is a bug in Syncwave, please report it at https://github.com/iannitello/syncwave/issues."
    if from_ is not None:
        raise _InternalError(message) from from_
    raise _InternalError(message)
