from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import Any, Generic, NoReturn, TypeVar, final

from pydantic import BaseModel, RootModel, TypeAdapter, create_model
from pydantic import dataclasses as pdc

from .context import get_ctx
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


class SyncModel(Generic[T], Reactive):
    __syncwave_ctx__: SyncModelCtx[T]

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncModelCtx[T]) -> None:
        self.__syncwave_sref__ = sref
        self.__syncwave_ctx__ = ctx
        self.__syncwave_live__ = True


T_BM = TypeVar("T_BM", bound=BaseModel)
T_RM = TypeVar("T_RM", bound=RootModel)
T_DC = TypeVar("T_DC")  # dataclass


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
    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}
    for name, field in cls.model_fields.items():
        ctx = get_ctx(field.annotation, reactive_allowed=True)
        if ctx is not None:
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            fields_ctx[name] = ctx
            fields_type_adapter[name] = TypeAdapter(field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_BM],
        new: SyncModel[T_BM] | T_BM | Mapping[str, Any],
    ) -> None:
        ctx = self.__syncwave_ctx__
        new = ctx.type_adapter.validate_python(new)

        for name in cls.model_fields:
            new_value = getattr(new, name, None)

            if name not in ctx.fields_type_adapter:
                original_setattr(self, name, new_value)
                continue

            old_value = getattr(self, name, None)

            old_is_reactive = isinstance(old_value, Reactive)
            safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

            if safe_to_update:
                old_value.__syncwave_update__(new_value)
                original_setattr(self, name, old_value)
            else:
                if old_is_reactive:
                    old_value.__syncwave_live__ = False
                if isinstance(new_value, Reactive):
                    new_value.__syncwave_init__(
                        self.__syncwave_sref__,
                        ctx.fields_ctx[name],
                    )
                original_setattr(self, name, new_value)

    @atomic
    def new_setattr(self: SyncModel[T_BM], name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__
        old_value = getattr(self, name, None)

        if name not in ctx.fields_type_adapter:
            original_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        new_value = ctx.fields_type_adapter[name].validate_python(new_value)

        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, name, old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            if isinstance(new_value, Reactive):
                new_value.__syncwave_init__(
                    self.__syncwave_sref__,
                    ctx.fields_ctx[name],
                )
            original_setattr(self, name, new_value)
        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_BM], name: str) -> None:
        old_value = getattr(self, name, None)

        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)

    ctx = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )
    new_cls.__syncwave_ctx__ = ctx

    return new_cls


def _create_root_model(cls: type[T_RM], cls_name: str) -> type[SyncModel[T_RM]]:
    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}
    root_field = cls.model_fields["root"]
    ctx = get_ctx(root_field.annotation, reactive_allowed=True)
    if ctx is not None:
        if root_field.frozen:
            raise TypeError("Field 'root': frozen fields cannot be reactive.")
        fields_ctx["root"] = ctx
        fields_type_adapter["root"] = TypeAdapter(root_field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_RM],
        new: SyncModel[T_RM] | T_RM | Any,
    ) -> None:
        ctx = self.__syncwave_ctx__
        new = ctx.type_adapter.validate_python(new)
        new_value = new.root

        if not ctx.fields_type_adapter:
            original_setattr(self, "root", new_value)
            return

        old_value = self.root

        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, "root", old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            if isinstance(new_value, Reactive):
                new_value.__syncwave_init__(
                    self.__syncwave_sref__,
                    ctx.fields_ctx["root"],
                )
            original_setattr(self, "root", new_value)

    @atomic
    def new_setattr(self: SyncModel[T_RM], name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__
        if name != "root" or not ctx.fields_type_adapter:
            original_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        old_value = self.root
        new_value = ctx.fields_type_adapter["root"].validate_python(new_value)

        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, "root", old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            if isinstance(new_value, Reactive):
                new_value.__syncwave_init__(
                    self.__syncwave_sref__,
                    ctx.fields_ctx["root"],
                )
            original_setattr(self, "root", new_value)
        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_RM], name: str) -> None:
        old_value = getattr(self, name, None)

        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)

    ctx = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )
    new_cls.__syncwave_ctx__ = ctx

    return new_cls


def _create_dataclass(cls: type[T_DC], cls_name: str) -> type[SyncModel[T_DC]]:
    if cls.__dataclass_params__.frozen:
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    if not pdc.is_pydantic_dataclass(cls):
        cls = pdc.dataclass(cls)

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}
    for name, field in cls.__pydantic_fields__.items():
        ctx = get_ctx(field.annotation, reactive_allowed=True)
        if ctx is not None:
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            fields_ctx[name] = ctx
            fields_type_adapter[name] = TypeAdapter(field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def syncwave_update(
        self: SyncModel[T_DC],
        new: SyncModel[T_DC] | T_DC | Mapping[str, Any],
    ) -> None:
        ctx = self.__syncwave_ctx__
        new = ctx.type_adapter.validate_python(new)

        for name in cls.__pydantic_fields__:
            new_value = getattr(new, name, None)

            if name not in ctx.fields_type_adapter:
                original_setattr(self, name, new_value)
                continue

            old_value = getattr(self, name, None)

            old_is_reactive = isinstance(old_value, Reactive)
            safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

            if safe_to_update:
                old_value.__syncwave_update__(new_value)
                original_setattr(self, name, old_value)
            else:
                if old_is_reactive:
                    old_value.__syncwave_live__ = False
                if isinstance(new_value, Reactive):
                    new_value.__syncwave_init__(
                        self.__syncwave_sref__,
                        ctx.fields_ctx[name],
                    )
                original_setattr(self, name, new_value)

    @atomic
    def new_setattr(self: SyncModel[T_DC], name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__
        old_value = getattr(self, name, None)

        if name not in ctx.fields_type_adapter:
            original_setattr(self, name, new_value)
            self.__syncwave_sref__.on_change()
            return

        new_value = ctx.fields_type_adapter[name].validate_python(new_value)

        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))

        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, name, old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            if isinstance(new_value, Reactive):
                new_value.__syncwave_init__(
                    self.__syncwave_sref__,
                    ctx.fields_ctx[name],
                )
            original_setattr(self, name, new_value)
        self.__syncwave_sref__.on_change()

    @atomic
    def new_delattr(self: SyncModel[T_DC], name: str) -> None:
        old_value = getattr(self, name, None)

        if isinstance(old_value, Reactive):
            old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_sref__.on_change()

    new_cls_dict = {
        "__syncwave_update__": syncwave_update,
        "__setattr__": new_setattr,
        "__delattr__": new_delattr,
        "__module__": cls.__module__,
    }

    new_cls = type(cls_name, (cls, SyncModel), new_cls_dict)
    new_cls = pdc.dataclass(new_cls)

    ctx = SyncModelCtx(
        tp=new_cls,
        type_adapter=TypeAdapter(new_cls),
        fields_ctx=fields_ctx,
        fields_type_adapter=fields_type_adapter,
    )
    new_cls.__syncwave_ctx__ = ctx

    return new_cls


@dataclass(frozen=True)
class SyncModelCtx(Generic[T], Context):
    tp: type[SyncModel]
    type_adapter: TypeAdapter[SyncModel[T]]
    fields_ctx: dict[str, Context | ContextMap]
    fields_type_adapter: dict[str, TypeAdapter[Any]]
