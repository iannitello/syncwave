from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import Any, Callable, Generic, NoReturn, TypeVar, final

from pydantic import BaseModel, RootModel, TypeAdapter, create_model
from pydantic import dataclasses as pdc

from .reactive import Context, ContextMap, Reactive, StoreRef, assert_never, mut_atomic


class SyncModelSupportedMeta(ABCMeta):
    def __subclasscheck__(self, subclass: type[Any]) -> bool:
        if not isclass(subclass):
            return False
        # RootModel is a subclass of BaseModel, and a pydantic dataclass is a dataclass
        return issubclass(subclass, BaseModel) or dc.is_dataclass(subclass)

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
        object.__setattr__(self, "__syncwave_sref__", sref)
        object.__setattr__(self, "__syncwave_ctx__", ctx)
        object.__setattr__(self, "__syncwave_live__", True)

        for name, field_ctx in ctx.fields_ctx.items():
            value = getattr(self, name, None)
            # case 1: non-reactive content type
            # skipped since fields_ctx only contains reactive fields
            # case 2: fixed reactive content type
            if isinstance(field_ctx, Context):
                value.__syncwave_init__(sref, field_ctx)
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                if isinstance(value, Reactive):
                    value.__syncwave_init__(sref, field_ctx[type(value)])
            else:
                assert_never()

    def __syncwave_kill__(self) -> None:
        for name in self.__syncwave_ctx__.fields_ctx:
            value = getattr(self, name, None)
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        object.__setattr__(self, "__syncwave_live__", False)


def create_sync_model(cls: type[T], rename: bool | str = True) -> type[SyncModel[T]]:
    cls_ = cls  # just to prevent the type checker from flagging code as unreachable

    cls_name = f"Sync{cls.__name__}" if rename is True else rename or cls.__name__

    if issubclass(cls_, BaseModel):
        if issubclass(cls_, RootModel):
            return _create_root_model(cls, cls_name)
        return _create_base_model(cls, cls_name)
    return _create_dataclass(cls, cls_name)


def _create_base_model(cls: type[T_BM], cls_name: str) -> type[SyncModel[T_BM]]:
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
                assert_never()

    @mut_atomic
    def new_setattr(self: SyncModel[T_BM], name: str, new_value: Any) -> None:
        field_ta = self.__syncwave_ctx__.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            o_setattr(self, name, new_value)
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
            assert_never()

    @mut_atomic
    def new_delattr(self: SyncModel[T_BM], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_kill__()
        o_delattr(self, name)

    new_cls = create_model(cls_name, __base__=(cls, SyncModel))
    new_cls.__syncwave_update__ = syncwave_update
    new_cls.__setattr__ = new_setattr
    new_cls.__delattr__ = new_delattr
    new_cls.__module__ = cls.__module__
    new_cls.__abstractmethods__ = frozenset()
    return new_cls


def _create_root_model(cls: type[T_RM], cls_name: str) -> type[SyncModel[T_RM]]:
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
            assert_never()

    @mut_atomic
    def new_setattr(self: SyncModel[T_RM], name: str, new_value: Any) -> None:
        if name != "root":
            o_setattr(self, name, new_value)
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
            assert_never()

    @mut_atomic
    def new_delattr(self: SyncModel[T_RM], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_kill__()
        o_delattr(self, name)

    new_cls = create_model(cls_name, __base__=(cls, SyncModel))
    new_cls.__syncwave_update__ = syncwave_update
    new_cls.__setattr__ = new_setattr
    new_cls.__delattr__ = new_delattr
    new_cls.__module__ = cls.__module__
    new_cls.__abstractmethods__ = frozenset()
    return new_cls


def _create_dataclass(cls: type[T_DC], cls_name: str) -> type[SyncModel[T_DC]]:
    if not pdc.is_pydantic_dataclass(cls):
        cls = pdc.dataclass(cls)

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
                assert_never()

    @mut_atomic
    def new_setattr(self: SyncModel[T_DC], name: str, new_value: Any) -> None:
        field_ta = self.__syncwave_ctx__.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            o_setattr(self, name, new_value)
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
            assert_never()

    @mut_atomic
    def new_delattr(self: SyncModel[T_DC], name: str) -> None:
        old_value = getattr(self, name, None)
        if isinstance(old_value, Reactive):
            old_value.__syncwave_kill__()
        o_delattr(self, name)

    new_cls = type(cls_name, (cls, SyncModel), {"__module__": cls.__module__})
    new_cls.__syncwave_update__ = syncwave_update
    new_cls.__setattr__ = new_setattr
    new_cls.__delattr__ = new_delattr
    new_cls.__abstractmethods__ = frozenset()
    return pdc.dataclass(new_cls)


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
            old_value.__syncwave_kill__()
        if new_is_reactive:
            new_value.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
        original_setattr(self, field_name, new_value)
