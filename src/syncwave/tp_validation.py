from __future__ import annotations

from collections.abc import Container
from dataclasses import is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from inspect import isclass
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path
from re import Pattern
from types import GenericAlias
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID

import pydantic.dataclasses as py_dc
from pydantic import ByteSize, RootModel, TypeAdapter

from .reactive import Context, ContextMap, Reactive, unreachable
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
from .sync_model import SyncModel, SyncModelCtx, is_sync_model_supported

if TYPE_CHECKING:
    from .sync_model import SMS


def str_guard(param: str, value: Any) -> None:
    if not isinstance(value, str):
        tp_name = type(value).__qualname__
        raise TypeError(f"Expected a `str` for param '{param}', got `{tp_name}`.")
    if not value.strip():
        raise ValueError(f"'{param}' cannot be empty or whitespace only.")


def sync_model_guard(cls: Any, known_models: Container[type[SMS]]) -> None:
    if cls in known_models:
        raise ValueError(f"Class '{cls.__qualname__}' has already been made reactive.")
    if not isclass(cls):
        raise TypeError(f"Expected a class, got `{type(cls).__qualname__}`.")
    if not is_sync_model_supported(cls):
        if is_dataclass(cls):
            raise TypeError(
                "Standard `dataclasses.dataclass` are not supported, "
                "use `pydantic.dataclasses.dataclass` instead."
            )
        raise TypeError(
            f"'{cls.__qualname__}' cannot be made reactive. The supported types are:\n"
            "  1. subclasses of `pydantic.BaseModel`,\n"
            "  2. subclasses of `pydantic.RootModel`,\n"
            "  3. classes decorated with `@pydantic.dataclasses.dataclass`."
        )
    _parse_model(cls, as_sync_model=True)


def collection_wrap(
    cls: type[SMS],
    sync_model: type[SyncModel],
    collection: type[SyncDict] | type[SyncList] | Literal["auto"] | None,
) -> type | GenericAlias:
    resolved_collection = collection  # non "auto" case
    if collection == "auto":
        if issubclass(cls, RootModel):
            resolved_collection = None
        elif "key" in cls.__pydantic_fields__:
            key_tp = cls.__pydantic_fields__["key"].annotation
            resolved_collection = GenericAlias(SyncDict, (key_tp,))
        else:
            resolved_collection = SyncList

    origin = get_origin(resolved_collection) or resolved_collection
    args = get_args(resolved_collection)
    tp_name = getattr(origin, "__qualname__", repr(origin))

    if (len_args := len(args)) > 0:
        if origin is not SyncDict:
            raise TypeError(f"`{tp_name}` does not support type arguments.")
        if origin is SyncDict and len_args > 1:
            raise TypeError("`SyncDict` supports only one type argument for the key.")
        _validate_key_tp(args[0])
        return GenericAlias(SyncDict, (args[0], sync_model))

    if origin is None:
        return sync_model
    if origin is SyncDict:
        return GenericAlias(SyncDict, (str, sync_model))
    if origin is SyncList:
        return GenericAlias(SyncList, (sync_model,))

    err = "`collection` must be one of: `SyncDict`, `SyncList`, `None`, or `'auto'`."
    if origin is SyncSet:
        err += " `SyncSet` cannot be used because it cannot contain reactive items."
    raise ValueError(err)


def drill_tp(tp: Any, _err_if_reactive: str = "") -> Context | ContextMap | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)
    tp_name = getattr(origin, "__qualname__", repr(origin))

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        return drill_tp(annotated_inner, _err_if_reactive)

    if (union_members := _handle_union(origin, args)) is not None:
        ctxs = [drill_tp(member, _err_if_reactive) for member in union_members]
        ctx_map = {ctx.tp: ctx for ctx in ctxs if isinstance(ctx, Context)}
        return ContextMap(ctx_map) if ctx_map else None

    _handle_literal(origin, args)  # nothing to do, just to check there are args

    if isclass(origin):
        if issubclass(origin, Reactive):
            if _err_if_reactive:
                raise TypeError(f"`{tp_name}` cannot be used here: {_err_if_reactive}")
            if issubclass(origin, SyncDict):
                return _get_sync_dict_ctx(tp)
            if issubclass(origin, SyncList):
                return _get_sync_list_ctx(tp)
            if issubclass(origin, SyncSet):
                return _get_sync_set_ctx(tp)
            if issubclass(origin, SyncModel):
                return _parse_model(origin)
            unreachable()
        if is_sync_model_supported(origin):
            return _parse_model(origin)
        if is_dataclass(origin):
            return _parse_model(py_dc.dataclass(origin))  # ty: ignore[invalid-argument-type]

        if issubclass(origin, dict) and args:
            _validate_key_tp(args[0])
        if issubclass(origin, (set, frozenset)) and args:
            arg_name = getattr(args[0], "__qualname__", repr(args[0]))
            err = f"`{tp_name}` must hold hashable elements, got `{arg_name}`."
            _validate_hashable(args[0], err)

    for arg in args:
        drill_tp(arg, _err_if_reactive=f"`{tp_name}` is not a reactive container.")

    return None


def _parse_model(cls: type[SMS], as_sync_model: bool = False) -> SyncModelCtx | None:
    is_sync_model = issubclass(cls, SyncModel)
    treat_as_sync_model = is_sync_model or as_sync_model

    config = getattr(cls, "model_config", {}) or getattr(cls, "__pydantic_config__", {})
    if treat_as_sync_model and config.get("frozen", False):
        raise TypeError(f"`{cls.__qualname__}` is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for field_name, field in cls.__pydantic_fields__.items():
        err = f"Field `{field_name}` in `{cls.__qualname__}` cannot be reactive because"
        if not treat_as_sync_model:
            err += " it is not contained in a `SyncModel` (breaks the reactive chain)."
        elif field.frozen:
            err += " it is frozen."
        else:
            err = ""

        field_ctx = drill_tp(field.annotation, _err_if_reactive=err)
        if field_ctx is not None:
            fields_ctx[field_name] = field_ctx
        fields_type_adapter[field_name] = TypeAdapter(field.annotation)

    if is_sync_model:
        # `is_sync_model` means `cls` is type[SyncModel],
        # but aliased conditional expressions are not supported by ty
        return SyncModelCtx(
            tp=cls,  # ty: ignore[invalid-argument-type]
            fields_ctx=fields_ctx,
            fields_type_adapter=fields_type_adapter,
        )

    return None


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)

    if len(args) == 2:
        _validate_key_tp(args[0])
        inner_ctx = drill_tp(args[1])
        inner_type_adapter = TypeAdapter(args[1])
    elif len(args) == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncDict` requires 0 or 2 type arguments.")

    return SyncDictCtx(
        tp=SyncDict,
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
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        tp_name = getattr(args[0], "__qualname__", repr(args[0]))
        err = f"`SyncSet` must hold hashable elements, got `{tp_name}`."
        _validate_hashable(args[0], err)
        drill_tp(args[0], _err_if_reactive="`SyncSet` cannot hold reactive items.")
        inner_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncSet` requires 0 or 1 type argument.")

    return SyncSetCtx(
        tp=SyncSet,
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )


def _validate_hashable(tp: Any, _err: str) -> None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        _validate_hashable(annotated_inner, _err)
        return
    if (union_members := _handle_union(origin, args)) is not None:
        [_validate_hashable(member, _err) for member in union_members]
        return
    if (literal_members := _handle_literal(origin, args)) is not None:
        [_validate_hashable(type(member), _err) for member in literal_members]
        return

    if isclass(origin):
        if getattr(origin, "__hash__", None) is None:
            raise TypeError(_err)
        # tuple and frozenset are hashable only if all elements are hashable
        if issubclass(origin, (tuple, frozenset)) and args:
            [_validate_hashable(arg, _err) for arg in args]
        # enums are hashable only if their members' values are hashable
        if issubclass(origin, Enum):
            [_validate_hashable(type(member.value), _err) for member in origin]
        return

    [_validate_hashable(arg, _err) for arg in args]


# Types that round-trip as dict keys through JSON (dump_json/validate_json).
# See: docs.pydantic.dev/latest/concepts/conversion_table/
_VALID_DICT_KEY_TYPES: list[type] = [
    str,
    int,
    float,
    bool,
    bytes,
    Decimal,
    Pattern,
    Path,
    date,
    datetime,
    time,
    timedelta,
    UUID,
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
    ByteSize,
]


_VALID_DICT_KEY_TYPES_STR = (
    ", ".join(
        f"`{tp.__qualname__}`"
        for tp in _VALID_DICT_KEY_TYPES
        if tp.__module__ == "builtins"
    )
    + ", `enum.Enum`, `typing.Literal`, "
    + ", ".join(
        f"`{tp.__module__}.{tp.__qualname__}`"
        for tp in _VALID_DICT_KEY_TYPES
        if tp.__module__ != "builtins"
    )
)


def _validate_key_tp(tp: Any) -> None:
    # JSON keys are strings, so the type must serialize to str and parseable back.
    origin = get_origin(tp) or tp
    args = get_args(tp)
    tp_name = getattr(origin, "__qualname__", repr(origin))

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        _validate_key_tp(annotated_inner)
        return
    if (union_members := _handle_union(origin, args)) is not None:
        [_validate_key_tp(member) for member in union_members]
        return
    if (literal_members := _handle_literal(origin, args)) is not None:
        [_validate_key_tp(type(member)) for member in literal_members]
        return

    if isclass(origin) and issubclass(origin, Enum):
        [_validate_key_tp(type(member.value)) for member in origin]
        return
    if origin in _VALID_DICT_KEY_TYPES:
        return

    raise TypeError(
        f"`{tp_name}` is not a valid dict key type. This is either because:\n"
        "  1. it cannot be serialized to `str` (JSON keys are always strings),\n"
        "  2. it cannot be deserialized from JSON back to the same type,\n"
        "  3. it is not hashable.\n\n"
        f"The key types currently supported are: {_VALID_DICT_KEY_TYPES_STR}."
    )


def _handle_annotated(origin: Any, args: tuple[Any, ...]) -> Any | None:
    if origin is not Annotated:
        return None
    if len(args) < 1:
        raise TypeError("`Annotated` must have at least one type argument.")
    return args[0]


def _handle_union(origin: Any, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
    if origin is not Union and str(origin) != "typing.Union":
        return None
    if not args:
        raise TypeError("`Union` must have at least one type argument.")
    return args


def _handle_literal(origin: Any, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
    if origin is not Literal and str(origin) != "typing.Literal":
        return None
    if not args:
        raise TypeError("`Literal` must have at least one type argument.")
    return args
