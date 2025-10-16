from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import final


class DeadReferenceError(RuntimeError):
    """Exception raised for operations on a dead or stale reactive reference."""

    def __init__(self, *, reference: Reactive) -> None:
        message = f"Operation attempted on a dead reference: {reference!r}"
        super().__init__(message)


class Reactive(metaclass=ABCMeta):
    """A mixin class that marks an object as part of the Syncwave reactive system."""

    __syncwave_live__ = False

    @abstractmethod
    def __syncwave_abc_marker__(self) -> None:
        raise NotImplementedError

    @final
    @property
    def sync_live(self) -> bool:
        return self.__syncwave_live__
