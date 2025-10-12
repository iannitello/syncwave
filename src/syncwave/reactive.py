from __future__ import annotations

from typing import Any, TypeVar, final

Self = TypeVar("Self", bound="Reactive")


class DeadReferenceError(RuntimeError):
    def __init__(self, *, reference: object) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


class Reactive:
    __syncwave_live__ = False

    def __new__(cls: type[Self], *args: Any, **kwargs: Any) -> Self:
        if cls is Reactive:
            raise TypeError("Reactive is a mixin and cannot be instantiated directly.")
        return super().__new__(cls)

    @final
    @property
    def sync_live(self) -> bool:
        return self.__syncwave_live__
