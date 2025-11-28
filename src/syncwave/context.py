from __future__ import annotations

from dataclasses import dataclass
from inspect import isclass
from threading import RLock
from typing import (
    Annotated,
    Any,
    Callable,
    Generic,
    Literal,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

from pydantic import TypeAdapter

from .reactive import Reactive, Reactivity
from .sync_collection import SyncDict, SyncList, SyncSet
from .sync_model import SyncModel, SyncModelSupported

KT = TypeVar("KT", bound=Union[str, int, float, bool, None])  # key type
VT = TypeVar("VT")  # value type
MT = TypeVar("MT", bound=SyncModelSupported)  # model type

# --------------------------------- Static Contexts ---------------------------------- #


@dataclass(frozen=True)
class StaticContext: ...


@dataclass(frozen=True)
class UnionStaticContext(StaticContext):
    inner_reactivity: Literal[Reactivity.REACTIVE, Reactivity.MIXED]
    inner_ctx_map: dict[type[Reactive], StaticContext]


@dataclass(frozen=True)
class SyncDictStaticContext(Generic[KT, VT], StaticContext):
    tp: type[SyncDict]
    type_adapter: TypeAdapter[SyncDict[KT, VT]]

    inner_ctx: StaticContext | None
    inner_reactivity: Reactivity
    inner_type_adapter: TypeAdapter[VT]


@dataclass(frozen=True)
class SyncListStaticContext(Generic[VT], StaticContext):
    tp: type[SyncList]
    type_adapter: TypeAdapter[SyncList[VT]]

    inner_ctx: StaticContext | None
    inner_reactivity: Reactivity
    inner_type_adapter: TypeAdapter[VT]


@dataclass(frozen=True)
class SyncSetStaticContext(Generic[VT], StaticContext):
    tp: type[SyncSet]
    type_adapter: TypeAdapter[SyncSet[VT]]

    inner_ctx: None  # never holds reactive items
    inner_reactivity: Literal[Reactivity.NON_REACTIVE]  # never holds reactive items
    inner_type_adapter: TypeAdapter[VT]


@dataclass(frozen=True)
class SyncModelStaticContext(Generic[MT], StaticContext):
    tp: type[SyncModel[MT]]
    type_adapter: TypeAdapter[SyncModel[MT]]

    fields_ctx: dict[str, StaticContext]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


# ------------------------------------- Contexts ------------------------------------- #


@dataclass(frozen=True)
class Context:
    lock: RLock
    on_change: Callable[[], None]


@dataclass(frozen=True)
class UnionContext(UnionStaticContext, Context):
    inner_ctx_map: dict[type[Reactive], Context]


@dataclass(frozen=True)
class SyncDictContext(SyncDictStaticContext[KT, VT], Context):
    inner_ctx: Context | None


@dataclass(frozen=True)
class SyncListContext(SyncListStaticContext[VT], Context):
    inner_ctx: Context | None


@dataclass(frozen=True)
class SyncSetContext(SyncSetStaticContext[VT], Context):
    inner_ctx: None  # never holds reactive items


@dataclass(frozen=True)
class SyncModelContext(SyncModelStaticContext[MT], Context):
    on_create: Callable[[SyncModel[MT]], None]
    fields_ctx: dict[str, Context]


# ------------------------------------- Helpers -------------------------------------- #


def get_static_ctx(
    tp: type[Any],
    root_level: bool,
    reactive_allowed: bool,
) -> StaticContext | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if isclass(origin) and issubclass(origin, Reactive):
        if not reactive_allowed:
            raise TypeError("Cannot break the reactivity chain.")
        if issubclass(origin, SyncModel):
            if root_level:
                return origin.__syncwave_static_ctx__
            raise TypeError("SyncModel types cannot be nested.")
        if issubclass(origin, SyncDict):
            return _get_sync_dict_sctx(tp)
        if issubclass(origin, SyncList):
            return _get_sync_list_sctx(tp)
        if issubclass(origin, SyncSet):
            return _get_sync_set_sctx(tp)
        raise TypeError(f"Unknown reactive type: {origin.__name__}")  # shouldn't happen

    if origin is Annotated:
        if len(args) < 1:
            raise ValueError("Annotated must have arguments.")
        return get_static_ctx(args[0], root_level, reactive_allowed)

    if origin is Union or str(origin) == "typing.Union":
        return _get_union_sctx(args, root_level, reactive_allowed)

    for arg in args:
        get_static_ctx(arg, root_level, reactive_allowed=False)

    return None


def _get_sync_dict_sctx(tp: type[SyncDict[KT, VT]]) -> SyncDictStaticContext[KT, VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 2:
        vt = args[1]
        inner_ctx = get_static_ctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncDict must have 0 or 2 arguments.")

    return SyncDictStaticContext(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_reactivity=_get_inner_reactivity(inner_ctx),
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_sctx(tp: type[SyncList[VT]]) -> SyncListStaticContext[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        inner_ctx = get_static_ctx(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncList must have 0 or 1 argument.")

    return SyncListStaticContext(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_reactivity=_get_inner_reactivity(inner_ctx),
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_sctx(tp: type[SyncSet[VT]]) -> SyncSetStaticContext[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        get_static_ctx(vt, reactive_allowed=False)  # will raise an error if reactive
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncSet must have 0 or 1 argument.")

    return SyncSetStaticContext(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_reactivity=Reactivity.NON_REACTIVE,
        inner_type_adapter=inner_type_adapter,
    )


def _get_union_sctx(
    args: tuple[Any, ...],
    root_level: bool,
    reactive_allowed: bool,
) -> UnionStaticContext | None:
    if not args:
        raise ValueError("Union must have arguments.")

    contexts = [get_static_ctx(tp, root_level, reactive_allowed) for tp in args]
    inner_ctx_map = {ctx.tp: ctx for ctx in contexts if ctx is not None}

    if not inner_ctx_map:
        return None

    is_mixed = None in contexts
    return UnionStaticContext(
        inner_reactivity=Reactivity.MIXED if is_mixed else Reactivity.REACTIVE,
        inner_ctx_map=inner_ctx_map,
    )


def _get_inner_reactivity(context: StaticContext | None) -> Reactivity:
    if context is None:
        return Reactivity.NON_REACTIVE
    if context.inner_reactivity is Reactivity.MIXED:
        return Reactivity.MIXED
    return Reactivity.REACTIVE
