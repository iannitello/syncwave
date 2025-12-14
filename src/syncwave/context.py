from __future__ import annotations

from inspect import isclass
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin

from pydantic import TypeAdapter

from .reactive import Context, ContextMap, Reactive
from .sync_collection import (
    SyncDict,
    SyncDictCtx,
    SyncList,
    SyncListCtx,
    SyncSet,
    SyncSetCtx,
)
from .sync_model import SyncModel

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])
VT = TypeVar("VT")


def get_ctx(tp: Any, reactive_allowed: bool) -> Context | ContextMap | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if isclass(origin) and issubclass(origin, Reactive):
        if not reactive_allowed:
            raise TypeError("Cannot break the reactivity chain.")
        if issubclass(origin, SyncModel):
            ctx = getattr(origin, "__syncwave_ctx__", None)
            if ctx is None:
                raise TypeError(
                    f"'{origin.__name__}' is a SyncModel but has no context. "
                    f"Ensure dependent models are made reactive first."
                )
            return ctx
        if issubclass(origin, SyncDict):
            return _get_sync_dict_ctx(tp)
        if issubclass(origin, SyncList):
            return _get_sync_list_ctx(tp)
        if issubclass(origin, SyncSet):
            return _get_sync_set_ctx(tp)
        raise TypeError(f"Unknown reactive type: {origin.__name__}")  # shouldn't happen

    if origin is Annotated:
        if len(args) < 1:
            raise ValueError("Annotated must have arguments.")
        return get_ctx(args[0], reactive_allowed)

    if origin is Union or str(origin) == "typing.Union":
        return _get_union_ctx(args, reactive_allowed)

    for arg in args:
        get_ctx(arg, reactive_allowed=False)

    return None


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 2:
        vt = args[1]
        inner_ctx = get_ctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncDict must have 0 or 2 arguments.")

    return SyncDictCtx(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_ctx(tp: type[SyncList[VT]]) -> SyncListCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        inner_ctx = get_ctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncList must have 0 or 1 argument.")

    return SyncListCtx(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        get_ctx(vt, reactive_allowed=False)  # will raise if reactive
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncSet must have 0 or 1 argument.")

    return SyncSetCtx(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )


def _get_union_ctx(args: tuple[Any, ...], reactive_allowed: bool) -> ContextMap | None:
    if not args:
        raise ValueError("Union must have arguments.")

    contexts = [get_ctx(tp, reactive_allowed) for tp in args]
    ctx_map = {ctx.tp: ctx for ctx in contexts if ctx is not None}

    if not ctx_map:
        return None

    return ContextMap(ctx_map)
