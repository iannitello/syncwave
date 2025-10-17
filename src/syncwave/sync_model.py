from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta, abstractmethod
from typing import Any, final

from pydantic import BaseModel, RootModel

from .reactive import Reactive
from .syncwave import Syncwave


class SyncModelSupportedMeta(ABCMeta):
    def __subclasscheck__(self, subclass: type[Any]) -> bool:
        if not isinstance(subclass, type):
            return False
        is_model = any(base in (BaseModel, RootModel) for base in subclass.__mro__)
        is_dc = dc.is_dataclass(subclass)
        return is_model or is_dc

    def __instancecheck__(self, instance: Any) -> bool:
        return self.__subclasscheck__(type(instance))

    def register(self, subclass: type[Any]) -> type[Any]:
        raise TypeError("SyncModelSupported does not support class registration.")


@final
class SyncModelSupported(metaclass=SyncModelSupportedMeta):
    """
    An ABC that acts as a protocol for types that Syncwave can make reactive.

    This includes:
        - pydantic.BaseModel
        - pydantic.RootModel
        - pydantic.dataclasses.dataclass
        - dataclasses.dataclass
    """

    def __init_subclass__(cls: type[SyncModelSupported], /, **kwargs: Any) -> None:
        raise TypeError("SyncModelSupported cannot be subclassed.")

    @abstractmethod
    def __syncwave_abc_marker__(self) -> None:
        raise NotImplementedError


class SyncModel(Reactive):
    """A marker base class for models that have been made reactive by Syncwave."""

    @staticmethod
    def _reactive(syncwave: Syncwave, cls: type[SyncModelSupported]) -> type[SyncModel]:
        pass
