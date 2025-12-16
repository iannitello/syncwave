from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import Any, Callable, Generic, NoReturn, TypeVar, final

from pydantic import BaseModel, RootModel, TypeAdapter, create_model
from pydantic import dataclasses as pdc

from .reactive import Context, ContextMap, Reactive, StoreRef, atomic


class SyncModelSupportedMeta(ABCMeta):
    def __subclasscheck__(self, subclass: type[Any]) -> bool:
        if not isclass(subclass):
            return False
        is_model = any(base in (BaseModel, RootModel) for base in subclass.__mro__)
        is_dc = dc.is_dataclass(subclass)
        return is_model or is_dc

    def __instancecheck__(self, instance: Any) -> bool:
        return self.__subclasscheck__(type(instance))

    def register(self, subclass: type[Any]) -> NoReturn:
        """SyncModelSupported does not support class registration."""
        raise TypeError("SyncModelSupported does not support class registration.")


@final
class SyncModelSupported(metaclass=SyncModelSupportedMeta):
    def __init_subclass__(cls: type[SyncModelSupported], /, **kwargs: Any) -> None:
        raise TypeError("SyncModelSupported cannot be subclassed.")


T = TypeVar("T", bound=SyncModelSupported)
T_BM = TypeVar("T_BM", bound=BaseModel)
T_RM = TypeVar("T_RM", bound=RootModel)
T_DC = TypeVar("T_DC")  # dataclass


@dataclass(frozen=True)
class SyncModelCtx(Generic[T], Context):
    tp: type[SyncModel]
    type_adapter: TypeAdapter[SyncModel[T]]
    fields_ctx: dict[str, Context | ContextMap]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(Generic[T], Reactive):
    __syncwave_ctx__: SyncModelCtx[T]

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncModelCtx[T]) -> None:
        if self.__class__.__syncwave_ctx__ is not ctx:
            raise TypeError("Internal Error: Invalid syncwave context.")

        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        self.__syncwave_live__ = True


def create_sync_model(cls: type[T], cls_name: str | None = None) -> type[SyncModel[T]]:
    if not isclass(cls):
        raise TypeError(f"'{cls}' is not a valid type.")

    is_base_model = any(base is BaseModel for base in cls.__mro__)
    is_root_model = any(base is RootModel for base in cls.__mro__)
    is_dataclass = dc.is_dataclass(cls)
    if not (is_base_model or is_root_model or is_dataclass):
        raise TypeError(f"Class '{cls.__name__}' is not a SyncModelSupported type.")

    cls_name = cls_name or f"Sync{cls.__name__}"
    if is_base_model:
        return _create_base_model(cls, cls_name)
    if is_root_model:
        return _create_root_model(cls, cls_name)
    if is_dataclass:
        return _create_dataclass(cls, cls_name)


def _create_base_model(cls: type[T_BM], cls_name: str) -> type[SyncModel[T_BM]]:
    from .syncwave import drill_tp

    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for name, field in cls.model_fields.items():
        field_ctx = drill_tp(field.annotation, reactive_allowed=True)
        if field_ctx is not None:
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            fields_ctx[name] = field_ctx
        fields_type_adapter[name] = TypeAdapter(field.annotation)

    o_setattr = cls.__setattr__
    o_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_BM],
        new: SyncModel[T_BM] | T_BM | Mapping[str, Any],
    ) -> None:
        new = self.__syncwave_ctx__.type_adapter.validate_python(new)

        for name in cls.model_fields:
            field_ctx = self.__syncwave_ctx__.fields_ctx.get(name)
            new_value = getattr(new, name, None)

            # case 1: non-reactive content type
            if field_ctx is None:
                o_setattr(self, name, new_value)
            # case 2: fixed reactive content type
            elif isinstance(field_ctx, Context):
                old_value = getattr(self, name)  # can't be None
                old_value.__syncwave_update__(new_value)
                o_setattr(self, name, old_value)
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                old_value = getattr(self, name, None)
                _setattr_union(self, name, old_value, new_value, field_ctx, o_setattr)
            else:
                raise TypeError("Internal Error: Invalid syncwave context.")

    @atomic
    def new_setattr(self: SyncModel[T_BM], name: str, new_value: Any) -> None:
        field_ta = self.__syncwave_ctx__.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            o_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        field_ctx = self.__syncwave_ctx__.fields_ctx.get(name)
        new_value = field_ta.validate_python(new_value)

        # case 1: non-reactive content type
        if field_ctx is None:
            o_setattr(self, name, new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = getattr(self, name)  # can't be None
            old_value.__syncwave_update__(new_value)
            o_setattr(self, name, old_value)
        # case 3: union content type
        elif isinstance(field_ctx, ContextMap):
            old_value = getattr(self, name, None)
            _setattr_union(self, name, old_value, new_value, field_ctx, o_setattr)
        else:
            raise TypeError("Internal Error: Invalid syncwave context.")

        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_BM], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        o_delattr(self, name)

        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)

    new_cls.__syncwave_ctx__ = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )

    return new_cls


def _create_root_model(cls: type[T_RM], cls_name: str) -> type[SyncModel[T_RM]]:
    from .syncwave import drill_tp

    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    root_field = cls.model_fields["root"]
    field_ctx = drill_tp(root_field.annotation, reactive_allowed=True)
    if field_ctx is not None:
        if root_field.frozen:
            raise TypeError("Field 'root': frozen fields cannot be reactive.")
        fields_ctx["root"] = field_ctx
    fields_type_adapter["root"] = TypeAdapter(root_field.annotation)

    o_setattr = cls.__setattr__
    o_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_RM],
        new: SyncModel[T_RM] | T_RM | Any,
    ) -> None:
        new = self.__syncwave_ctx__.type_adapter.validate_python(new)

        field_ctx = self.__syncwave_ctx__.fields_ctx.get("root")
        new_value = new.root

        # case 1: non-reactive content type
        if field_ctx is None:
            o_setattr(self, "root", new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = self.root  # can't be None
            old_value.__syncwave_update__(new_value)
            o_setattr(self, "root", old_value)
        # case 3: union content type
        elif isinstance(field_ctx, ContextMap):
            old_value = getattr(self, "root", None)
            _setattr_union(self, "root", old_value, new_value, field_ctx, o_setattr)
        else:
            raise TypeError("Internal Error: Invalid syncwave context.")

    @atomic
    def new_setattr(self: SyncModel[T_RM], name: str, new_value: Any) -> None:
        if name != "root":
            o_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        field_ctx = self.__syncwave_ctx__.fields_ctx.get("root")
        root_ta = self.__syncwave_ctx__.fields_type_adapter["root"]
        new_value = root_ta.validate_python(new_value)

        # case 1: non-reactive content type
        if field_ctx is None:
            o_setattr(self, "root", new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = self.root  # can't be None
            old_value.__syncwave_update__(new_value)
            o_setattr(self, "root", old_value)
        # case 3: union content type
        elif isinstance(field_ctx, ContextMap):
            old_value = getattr(self, "root", None)
            _setattr_union(self, "root", old_value, new_value, field_ctx, o_setattr)
        else:
            raise TypeError("Internal Error: Invalid syncwave context.")

        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_RM], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        o_delattr(self, name)

        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)

    new_cls.__syncwave_ctx__ = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )

    return new_cls


def _create_dataclass(cls: type[T_DC], cls_name: str) -> type[SyncModel[T_DC]]:
    from .syncwave import drill_tp

    if cls.__dataclass_params__.frozen:
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    if not pdc.is_pydantic_dataclass(cls):
        cls = pdc.dataclass(cls)

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for name, field in cls.__pydantic_fields__.items():
        field_ctx = drill_tp(field.annotation, reactive_allowed=True)
        if field_ctx is not None:
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            fields_ctx[name] = field_ctx
        fields_type_adapter[name] = TypeAdapter(field.annotation)

    o_setattr = cls.__setattr__
    o_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_DC],
        new: SyncModel[T_DC] | T_DC | Mapping[str, Any],
    ) -> None:
        new = self.__syncwave_ctx__.type_adapter.validate_python(new)

        for name in cls.__pydantic_fields__:
            field_ctx = self.__syncwave_ctx__.fields_ctx.get(name)
            new_value = getattr(new, name, None)

            # case 1: non-reactive content type
            if field_ctx is None:
                o_setattr(self, name, new_value)
            # case 2: fixed reactive content type
            elif isinstance(field_ctx, Context):
                old_value = getattr(self, name)  # can't be None
                old_value.__syncwave_update__(new_value)
                o_setattr(self, name, old_value)
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                old_value = getattr(self, name, None)
                _setattr_union(self, name, old_value, new_value, field_ctx, o_setattr)
            else:
                raise TypeError("Internal Error: Invalid syncwave context.")

    @atomic
    def new_setattr(self: SyncModel[T_DC], name: str, new_value: Any) -> None:
        field_ta = self.__syncwave_ctx__.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            o_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        field_ctx = self.__syncwave_ctx__.fields_ctx.get(name)
        new_value = field_ta.validate_python(new_value)

        # case 1: non-reactive content type
        if field_ctx is None:
            o_setattr(self, name, new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = getattr(self, name)  # can't be None
            old_value.__syncwave_update__(new_value)
            o_setattr(self, name, old_value)
        # case 3: union content type
        elif isinstance(field_ctx, ContextMap):
            old_value = getattr(self, name, None)
            _setattr_union(self, name, old_value, new_value, field_ctx, o_setattr)
        else:
            raise TypeError("Internal Error: Invalid syncwave context.")

        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_DC], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        o_delattr(self, name)

        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = type(cls_name, (cls, SyncModel), new_cls_dict)
    new_cls = pdc.dataclass(new_cls)

    new_cls.__syncwave_ctx__ = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )

    return new_cls


def _setattr_union(
    self: SyncModel[T],
    field_name: str,
    old_value: Any,
    new_value: Any,
    u_ctx: ContextMap,
    original_setattr: Callable[[Any, Any, Any], None],
) -> None:
    old_is_reactive = isinstance(old_value, Reactive)
    new_is_reactive = isinstance(new_value, Reactive)
    same_type = type(old_value) is (new_type := type(new_value))

    if old_is_reactive and new_is_reactive and same_type:
        old_value.__syncwave_update__(new_value)
        original_setattr(self, field_name, old_value)
    else:
        if old_is_reactive:
            old_value.__syncwave_live__ = False
        if new_is_reactive:
            if new_type not in u_ctx:
                raise TypeError("Internal Error: Invalid syncwave context.")
            new_value.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
        original_setattr(self, field_name, new_value)
