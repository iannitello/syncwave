from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from threading import RLock
from typing import Any, Callable, Generic, Literal, TypeVar, Union

from pydantic import TypeAdapter

from .sync_collection import SyncDict, SyncList, SyncSet
from .sync_model import SyncModel, SyncModelSupported

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])
VT = TypeVar("VT")


@dataclass(frozen=True)
class StaticContext: ...


@dataclass(frozen=True)
class Context:
    lock: RLock
    on_change: Callable[[], None]


class ContentCategory(Enum):
    NON_REACTIVE = auto()
    REACTIVE = auto()
    MIXED = auto()


@dataclass(frozen=True)
class SyncDictStaticContext(Generic[KT, VT], StaticContext):
    type_adapter: TypeAdapter[SyncDict[KT, VT]]
    content_type_adapter: TypeAdapter[VT]
    content_ctx: StaticContext | None
    content_ctg: ContentCategory


@dataclass(frozen=True)
class SyncDictContext(SyncDictStaticContext[KT, VT], Context):
    content_ctx: Context | None


@dataclass(frozen=True)
class SyncListStaticContext(Generic[VT], StaticContext):
    type_adapter: TypeAdapter[SyncList[VT]]
    content_type_adapter: TypeAdapter[VT]
    content_ctx: StaticContext | None
    content_ctg: ContentCategory


@dataclass(frozen=True)
class SyncListContext(SyncListStaticContext[VT], Context):
    content_ctx: Context | None


@dataclass(frozen=True)
class SyncSetStaticContext(Generic[VT], StaticContext):
    type_adapter: TypeAdapter[SyncSet[VT]]
    content_type_adapter: TypeAdapter[VT]
    content_ctx: None  # never holds reactive items
    content_ctg: Literal[ContentCategory.NON_REACTIVE]  # never holds reactive items


@dataclass(frozen=True)
class SyncSetContext(SyncSetStaticContext[VT], Context):
    content_ctx: None  # never holds reactive items


T = TypeVar("T", bound=SyncModelSupported)


@dataclass(frozen=True)
class SyncModelStaticContext(Generic[T], StaticContext):
    type_adapter: TypeAdapter[SyncModel[T]]
    fields_ctx: dict[str, StaticContext]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


@dataclass(frozen=True)
class SyncModelContext(SyncModelStaticContext[T], Context):
    fields_ctx: dict[str, Context]
    on_create: Any  # Callable[[T], None]
