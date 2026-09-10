from __future__ import annotations

from collections.abc import Container
from dataclasses import is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, Flag
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
from types import GenericAlias, UnionType
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID

import pydantic.dataclasses as py_dc
from pydantic import ByteSize, Discriminator, RootModel, TypeAdapter
from pydantic_core import (
    PydanticSerializationError,
    PydanticUndefined,
    from_json,
    to_json,
)

from .errors import unreachable
from .ownership import ingest
from .reactive import Context, Reactive, UnionCtx
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
    from pydantic.fields import FieldInfo

    from .sync_model import SMS

__all__ = []


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
    collection: type[SyncDict | SyncList] | Literal["auto"] | None,
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
        _validate_dict_key_tp(args[0])
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


def drill_tp(tp: Any, _err_if_reactive: str = "") -> Context | UnionCtx | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)
    tp_name = getattr(origin, "__qualname__", repr(origin))

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        return drill_tp(annotated_inner, _err_if_reactive)

    if (union_members := _handle_union(origin, args)) is not None:
        ctxs = [drill_tp(member, _err_if_reactive) for member in union_members]
        ctx_map = {ctx.tp: ctx for ctx in ctxs if isinstance(ctx, Context)}
        return UnionCtx(ctx_map) if ctx_map else None

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

        if issubclass(origin, dict):
            if args:
                _validate_dict_key_tp(args[0])
            else:
                raise TypeError("Use `dict[str, Any]` instead of a bare `dict`.")
        if issubclass(origin, (set, frozenset)) and args:
            arg_name = getattr(args[0], "__qualname__", repr(args[0]))
            err = f"`{tp_name}` must hold hashable elements, got `{arg_name}`."
            _validate_hashable(args[0], err)

    for arg in args:
        drill_tp(arg, _err_if_reactive=f"`{tp_name}` is not a reactive container.")

    return None


def _parse_model(cls: type[SMS], *, as_sync_model: bool = False) -> SyncModelCtx | None:
    is_sync_model = issubclass(cls, SyncModel)
    treat_as_sync_model = is_sync_model or as_sync_model

    config = getattr(cls, "model_config", {}) or getattr(cls, "__pydantic_config__", {})
    if treat_as_sync_model and config.get("frozen", False):
        raise TypeError(f"`{cls.__qualname__}` is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | UnionCtx] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for field_name, field_info in cls.__pydantic_fields__.items():
        err = f"Field `{field_name}` in `{cls.__qualname__}` cannot be reactive because"
        if not treat_as_sync_model:
            err += " it is not contained in a `SyncModel` (breaks the reactive chain)."
        elif field_info.frozen:
            err += " it is frozen."
        else:
            err = ""

        annotation = _field_annotation(field_info)
        field_ctx = drill_tp(annotation, _err_if_reactive=err)
        if field_ctx is not None:
            fields_ctx[field_name] = field_ctx
        ta = fields_type_adapter[field_name] = TypeAdapter(annotation)

        # validates defaults, excluding factories
        if field_info.default is not PydanticUndefined:
            try:
                ingest(field_info.default, ta)
            # ValidationError and PydanticSerializationError inherit from ValueError
            except ValueError as e:
                raise ValueError(
                    f"Field `{field_name}` in `{cls.__qualname__}` "
                    "has an invalid default value."
                ) from e

    if is_sync_model:
        return SyncModelCtx(
            tp=cls,
            fields_ctx=fields_ctx,
            fields_type_adapter=fields_type_adapter,
        )

    return None


def _field_annotation(field_info: FieldInfo) -> Any:
    metadata = list(field_info.metadata)
    if (discriminator := field_info.discriminator) is not None:
        if not isinstance(discriminator, Discriminator):
            discriminator = Discriminator(discriminator)
        metadata.append(discriminator)
    if not metadata:
        return field_info.annotation
    return Annotated[(field_info.annotation, *metadata)]  # ty: ignore[invalid-type-form]


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)

    if len(args) == 2:
        _validate_dict_key_tp(args[0])
        inner_ctx = drill_tp(args[1])
        key_type_adapter = TypeAdapter(args[0])
        value_type_adapter = TypeAdapter(args[1])
    elif len(args) == 0:
        inner_ctx = None
        key_type_adapter = TypeAdapter(str)
        value_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncDict` requires 0 or 2 type arguments.")

    return SyncDictCtx(
        tp=SyncDict,
        inner_ctx=inner_ctx,
        key_type_adapter=key_type_adapter,
        value_type_adapter=value_type_adapter,
    )


def _get_sync_list_ctx(tp: type[SyncList[VT]]) -> SyncListCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        inner_ctx = drill_tp(args[0])
        item_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_ctx = None
        item_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncList` requires 0 or 1 type argument.")

    return SyncListCtx(
        tp=SyncList,
        inner_ctx=inner_ctx,
        item_type_adapter=item_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        tp_name = getattr(args[0], "__qualname__", repr(args[0]))
        err = f"`SyncSet` must hold hashable elements, got `{tp_name}`."
        _validate_hashable(args[0], err)
        drill_tp(args[0], _err_if_reactive="`SyncSet` cannot hold reactive items.")
        item_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        item_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncSet` requires 0 or 1 type argument.")

    return SyncSetCtx(
        tp=SyncSet,
        inner_ctx=None,
        item_type_adapter=item_type_adapter,
    )


# Whitelist: types that round-trip as dict keys through JSON (dump_json/validate_json).
# See: https://pydantic.dev/docs/validation/latest/concepts/conversion_table/
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

# NOTE: Dict keys must serialize to valid JSON object keys and parse back unambiguously.
# This can't be delegated to a TypeAdapter because Syncwave's requirements are stricter:
# e.g. `dict[int | str, int]` is accepted by Pydantic, but 1 and "1" collide on load.
#
# Also, Pydantic accepts more key types than it can round-trip (even before Syncwave's
# stricter requirements). E.g. `Literal[1, "a"]` can be dumped just fine as `{"1": 0}`,
# but `validate_json` then fails because the JSON string `"1"` is not converted to an
# integer and doesn't match the `Literal` members. A similar issue occurs with enums.
#
# Here are the overall requirements to use these types as dict keys: `Literal` members
# must all be strings (members of str-subclassed enums are allowed). `Enum` values must
# all be strings (members of str-subclassed enums are also allowed). Enums subclassing
# `str`, `int`, or `float` (e.g. `IntEnum`) are allowed. `Flag` are always rejected
# because composite members (`A | B`) pass write validation but can't be deserialized.


def _validate_dict_key_tp(tp: Any) -> None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if origin in _VALID_DICT_KEY_TYPES:
        return

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        _validate_dict_key_tp(annotated_inner)
        return

    tp_name = tp.__qualname__ if isclass(tp) and not args else str(tp)
    err = f"Invalid dict key type `{tp_name}`: "

    if _handle_union(origin, args) is not None:
        raise TypeError(
            err + "unions are always rejected when used as dict keys because a JSON "
            "object key (which is always a string) cannot be deserialized "
            "unambiguously back to one specific member of the union."
        )

    if (literal_members := _handle_literal(origin, args)) is not None:
        if all(isinstance(m, str) for m in literal_members):  # all-string members
            _check_collisions(literal_members, err)
            return
        raise TypeError(
            err + "`Literal` may only have string members when used as dict keys "
            "(members of a `StrEnum`, or of an `Enum` subclassing `str`, are "
            "allowed); other member types, or mixed types, are not supported (yet)."
        )

    if isclass(origin) and issubclass(origin, Enum):
        if issubclass(origin, Flag):
            raise TypeError(
                err + "flag enums are always rejected when used as dict keys because "
                "composite members (e.g. `A | B`) cannot be restored from a JSON "
                "object key."
            )
        if (
            issubclass(origin, (str, int, float))  # `class E(str, Enum):`, `IntEnum`
            or all(isinstance(m.value, str) for m in origin)  # all-string members
        ):
            _check_collisions(tuple(origin), err)
            return
        raise TypeError(
            err + "enums must subclass `str`, `int`, or `float` (like `IntEnum`) when "
            "used as dict keys. Plain enums may only be used if all member values "
            "are strings (values taken from a different `StrEnum`, or from an `Enum` "
            "subclassing `str`, count as strings)."
        )

    _validate_hashable(tp, err + "not hashable.")

    raise TypeError(
        err + "a valid key type must:\n"
        "  1. serialize to a valid JSON object key (JSON keys are always strings),\n"
        "  2. deserialize from JSON back to the same value,\n"
        "  3. be hashable.\n\n"
        f"The key types currently supported are: {_VALID_DICT_KEY_TYPES_STR}."
    )


def _check_collisions(keys: tuple[Any, ...], err: str) -> None:
    original_keys = keys
    keys = tuple(dict.fromkeys(keys))

    # Python keys collisions: `(1, 1.0, True)` all hash to `1`.
    if len(original_keys) > len(keys):
        pairs = [
            f"{original_key!r} and {key!r}"
            for original_key in original_keys
            for key in keys
            if original_key is not key and original_key == key
        ]
        raise TypeError(err + f"{', '.join(pairs)} collide as Python dict keys.")

    # JSON key collisions: `("a", <E.A: 'a'>)` both serialize to `"a"`.
    groups: dict[str, list[Any]] = {}
    for key in keys:
        try:
            json_key = next(iter(from_json(to_json({key: None}))))
        except PydanticSerializationError:
            err += f"{key!r} cannot be serialized as a JSON object key."
            raise TypeError(err) from None
        except ValueError:
            err += f"{key!r} cannot be deserialized from JSON."
            raise TypeError(err) from None
        groups.setdefault(json_key, []).append(key)

    collisions = {jk: ks for jk, ks in groups.items() if len(ks) > 1}
    if collisions:
        parts = [
            f"{', '.join(repr(k) for k in ks)} all serialize to {jk!r}"
            for jk, ks in collisions.items()
        ]
        raise TypeError(err + f"{'; '.join(parts)}.")


def _validate_hashable(tp: type, err: str) -> None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if (annotated_inner := _handle_annotated(origin, args)) is not None:
        _validate_hashable(annotated_inner, err)
        return
    if (union_members := _handle_union(origin, args)) is not None:
        [_validate_hashable(member, err) for member in union_members]
        return
    if (literal_members := _handle_literal(origin, args)) is not None:
        [_validate_hashable(_get_tp(member), err) for member in literal_members]
        return

    if isclass(origin):
        if getattr(origin, "__hash__", None) is None:
            raise TypeError(err)
        # tuple and frozenset are hashable only if all elements are hashable
        if issubclass(origin, (tuple, frozenset)) and args:
            [_validate_hashable(arg, err) for arg in args]
        # enums are hashable only if their members' values are hashable
        if issubclass(origin, Enum):
            [_validate_hashable(_get_tp(member.value), err) for member in origin]
        return

    [_validate_hashable(arg, err) for arg in args]


def _get_tp(v: Any) -> type:
    # Drills down to the innermost type of an enum member value.
    if isinstance(v, Enum):
        return _get_tp(v.value)
    return type(v)


def _handle_annotated(origin: Any, args: tuple[Any, ...]) -> Any | None:
    if origin is not Annotated:
        return None
    if len(args) < 1:
        raise TypeError("`Annotated` must have at least one type argument.")
    return args[0]


def _handle_union(origin: Any, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
    if origin is not Union and origin is not UnionType:
        return None
    if not args:
        raise TypeError("`Union` must have at least one type argument.")
    return args


def _handle_literal(origin: Any, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
    if origin is not Literal:
        return None
    if not args:
        raise TypeError("`Literal` must have at least one type argument.")
    return args
