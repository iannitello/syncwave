from __future__ import annotations

from inspect import isclass
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import TypeAdapter

from .reactive import Context, ContextMap, Reactive
from .sync_collection import (
    KT,
    VT,
    SyncDict,
    SyncDictCtx,
    SyncList,
    SyncListCtx,
    SyncSet,
    SyncSetCtx,
)
from .sync_model import SyncModel, SyncModelCtx, SyncModelSupported


def str_guard(param: str, value: Any) -> None:
    if not isinstance(value, str):
        tp = type(value).__name__
        raise TypeError(f"Expected a `str` for param '{param}', got `{tp}`.")
    if not value.strip():
        raise ValueError(f"'{param}' cannot be empty or whitespace only.")


def sync_model_guard(cls: type[SyncModelSupported]) -> None:
    if not isclass(cls):
        raise TypeError(f"Expected a class, got `{type(cls).__name__}`.")
    if not issubclass(cls, SyncModelSupported):
        raise TypeError(f"Expected a SyncModelSupported, got `{cls.__name__}`.")
    _drill_model(cls, as_sync_model=True)


def drill_tp(tp: Any, _err_if_reactive: str = "") -> Context | ContextMap | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)
    name = getattr(origin, "__name__", repr(origin))

    if origin is Annotated:
        if len(args) < 1:
            raise TypeError("`Annotated` must have at least one type argument.")
        return drill_tp(args[0], _err_if_reactive)

    if origin is Union or str(origin) == "typing.Union":
        if not args:
            raise TypeError("`Union` must have at least one type argument.")
        ctxs = [drill_tp(arg, _err_if_reactive) for arg in args]
        ctx_map = {ctx.tp: ctx for ctx in ctxs if ctx is not None}
        return ContextMap(ctx_map) if ctx_map else None

    if isclass(origin):
        if issubclass(origin, Reactive):
            if _err_if_reactive:
                raise TypeError(f"`{name}` cannot be used here: {_err_if_reactive}")
            if issubclass(origin, SyncDict):
                return _get_sync_dict_ctx(tp)
            if issubclass(origin, SyncList):
                return _get_sync_list_ctx(tp)
            if issubclass(origin, SyncSet):
                return _get_sync_set_ctx(tp)
            if issubclass(origin, SyncModel):
                return _drill_model(origin)
            raise TypeError(f"`{name}` is not a recognized reactive type.")
        if issubclass(origin, SyncModelSupported):
            return _drill_model(origin)

    err = f"`{name}` is not a reactive container."
    for arg in args:
        drill_tp(arg, _err_if_reactive=err)

    return None


def _drill_model(cls: type, as_sync_model: bool = False) -> SyncModelCtx | None:
    is_sync_model = issubclass(cls, SyncModel)
    treat_as_sync_model = is_sync_model or as_sync_model

    model_is_frozen: bool = (
        getattr(cls, "model_config", {}).get("frozen", False)
        or getattr(cls, "__pydantic_config__", {}).get("frozen", False)
        or getattr(getattr(cls, "__dataclass_params__", None), "frozen", False)
    )
    if model_is_frozen and treat_as_sync_model:
        raise TypeError(f"`{cls.__name__}` is frozen and cannot be made reactive.")

    fields = getattr(cls, "__pydantic_fields__", None) or cls.__dataclass_fields__
    if not fields:
        raise TypeError(f"`{cls.__name__}` has no fields.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for name, field in fields.items():
        field_tp = getattr(field, "annotation", None) or field.type
        field_is_frozen: bool = getattr(field, "frozen", False)

        if not treat_as_sync_model:
            err = f"`{cls.__name__}` is not a `SyncModel` (for field `{name}`)."
        elif field_is_frozen:
            err = f"Field `{name}` in `{cls.__name__}` is frozen."
        else:
            err = ""

        field_ctx = drill_tp(field_tp, _err_if_reactive=err)
        if field_ctx is not None:
            fields_ctx[name] = field_ctx
        fields_type_adapter[name] = TypeAdapter(field_tp)

    if is_sync_model:
        return SyncModelCtx(
            tp=cls,
            type_adapter=TypeAdapter(cls),
            fields_ctx=fields_ctx,
            fields_type_adapter=fields_type_adapter,
        )

    return None


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)

    if len(args) == 2:
        inner_ctx = drill_tp(args[1])
        inner_type_adapter = TypeAdapter(args[1])
    elif len(args) == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncDict` requires 0 or 2 type arguments.")

    return SyncDictCtx(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_ctx(tp: type[SyncList[VT]]) -> SyncListCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        inner_ctx = drill_tp(args[0])
        inner_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncList` requires 0 or 1 type argument.")

    return SyncListCtx(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        drill_tp(args[0], _err_if_reactive="`SyncSet` cannot hold reactive items.")
        inner_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncSet` requires 0 or 1 type argument.")

    return SyncSetCtx(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )
