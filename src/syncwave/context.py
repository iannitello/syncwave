from __future__ import annotations

from dataclasses import dataclass
from inspect import isclass
from threading import RLock
from typing import (
    Annotated,
    Any,
    Callable,
    Generic,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

from pydantic import TypeAdapter

from .reactive import Reactive
from .sync_collection import SyncDict, SyncList, SyncSet
from .sync_model import SyncModel, SyncModelSupported

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])  # key type
VT = TypeVar("VT")  # value type
MT = TypeVar("MT", bound=SyncModelSupported)  # model type


# --------------------------------- Static Contexts ---------------------------------- #


@dataclass
class PartialContext:
    tp: type[Reactive]
    type_adapter: TypeAdapter[Reactive]


class UnionPCtx(dict[type[Reactive], PartialContext]): ...


@dataclass
class SyncDictPCtx(Generic[KT, VT], PartialContext):
    tp: type[SyncDict]
    type_adapter: TypeAdapter[SyncDict[KT, VT]]

    inner_ctx: PartialContext | UnionPCtx | None
    inner_type_adapter: TypeAdapter[VT]


@dataclass
class SyncListPCtx(Generic[VT], PartialContext):
    tp: type[SyncList]
    type_adapter: TypeAdapter[SyncList[VT]]

    inner_ctx: PartialContext | UnionPCtx | None
    inner_type_adapter: TypeAdapter[VT]


@dataclass
class SyncSetPCtx(Generic[VT], PartialContext):
    tp: type[SyncSet]
    type_adapter: TypeAdapter[SyncSet[VT]]

    inner_ctx: None  # never holds reactive items
    inner_type_adapter: TypeAdapter[VT]


@dataclass
class SyncModelPCtx(Generic[MT], PartialContext):
    tp: type[SyncModel]
    type_adapter: TypeAdapter[SyncModel[MT]]

    fields_ctx: dict[str, PartialContext | UnionPCtx]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


# ------------------------------------- Contexts ------------------------------------- #


@dataclass(frozen=True)
class Context:
    lock: RLock
    on_change: Callable[[], None]


class UnionCtx(dict[type[Reactive], Context]): ...


@dataclass(frozen=True)
class SyncDictCtx(SyncDictPCtx[KT, VT], Context):
    inner_ctx: Context | UnionCtx | None


@dataclass(frozen=True)
class SyncListCtx(SyncListPCtx[VT], Context):
    inner_ctx: Context | UnionCtx | None


@dataclass(frozen=True)
class SyncSetCtx(SyncSetPCtx[VT], Context):
    inner_ctx: None  # never holds reactive items


@dataclass(frozen=True)
class SyncModelCtx(SyncModelPCtx[MT], Context):
    fields_ctx: dict[str, Context | UnionCtx]


# ------------------------------------- Helpers -------------------------------------- #


def get_pctx(tp: Any, reactive_allowed: bool) -> PartialContext | UnionPCtx | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if isclass(origin) and issubclass(origin, Reactive):
        if not reactive_allowed:
            raise TypeError("Cannot break the reactivity chain.")
        if issubclass(origin, SyncModel):
            pass  # TODO: implement
        if issubclass(origin, SyncDict):
            return _get_sync_dict_pctx(tp)
        if issubclass(origin, SyncList):
            return _get_sync_list_pctx(tp)
        if issubclass(origin, SyncSet):
            return _get_sync_set_pctx(tp)
        raise TypeError(f"Unknown reactive type: {origin.__name__}")  # shouldn't happen

    if origin is Annotated:
        if len(args) < 1:
            raise ValueError("Annotated must have arguments.")
        return get_pctx(args[0], reactive_allowed)

    if origin is Union or str(origin) == "typing.Union":
        return _get_union_pctx(args, reactive_allowed)

    for arg in args:
        get_pctx(arg, reactive_allowed=False)

    return None


def _get_sync_dict_pctx(tp: type[SyncDict[KT, VT]]) -> SyncDictPCtx[KT, VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 2:
        vt = args[1]
        inner_ctx = get_pctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncDict must have 0 or 2 arguments.")

    return SyncDictPCtx(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_pctx(tp: type[SyncList[VT]]) -> SyncListPCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        inner_ctx = get_pctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncList must have 0 or 1 argument.")

    return SyncListPCtx(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_pctx(tp: type[SyncSet[VT]]) -> SyncSetPCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        get_pctx(vt, reactive_allowed=False)  # will raise if reactive
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncSet must have 0 or 1 argument.")

    return SyncSetPCtx(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )


def _get_union_pctx(args: tuple[Any, ...], reactive_allowed: bool) -> UnionPCtx | None:
    if not args:
        raise ValueError("Union must have arguments.")

    contexts = [get_pctx(tp, reactive_allowed) for tp in args]
    pctx_map = {pctx.tp: pctx for pctx in contexts if pctx is not None}

    if not pctx_map:
        return None

    return UnionPCtx(pctx_map)
