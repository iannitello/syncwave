from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import Any, Callable, Generic, NoReturn, TypeVar, final
from typing_extensions import Self

from pydantic import BaseModel, TypeAdapter
from pydantic import GetCoreSchemaHandler as Handler
from pydantic_core import core_schema as cs

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
    def __init_subclass__(cls, /, **kwargs: Any) -> NoReturn:
        raise TypeError("SyncModelSupported cannot be subclassed.")


T = TypeVar("T")


@dataclass(frozen=True)
class SyncModelCtx(Generic[T], Context):
    tp: type[T]
    type_adapter: TypeAdapter[T]
    fields_ctx: dict[str, Context | ContextMap]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(Reactive):
    __syncwave_ctx__: SyncModelCtx[T]
    __syncwave_original_cls__: type[T]

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError(f"{cls.__name__} cannot be instantiated directly.")

    @classmethod
    def __syncwave_new__(cls, self: T) -> Self:
        self.__class__ = cls
        return self

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

    def __syncwave_update__(self, new: Self | Mapping[str, Any]) -> None:
        ctx = self.__syncwave_ctx__
        o_setattr = self.__syncwave_original_cls__.__setattr__

        new = ctx.type_adapter.validate_python(new)

        for name in ctx.fields_type_adapter:
            field_ctx = ctx.fields_ctx.get(name)
            new_value = getattr(new, name, None)

            # case 1: non-reactive content type
            if field_ctx is None:
                o_setattr(self, name, new_value)
            # case 2: fixed reactive content type
            elif isinstance(field_ctx, Context):
                old_value = getattr(self, name)  # can't be None
                old_value.__syncwave_update__(new_value)
                o_setattr(self, name, old_value)  # TODO: necessary?
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                old_value = getattr(self, name, None)
                _setattr_union(self, name, old_value, new_value, field_ctx, o_setattr)
            else:
                assert_never()

    @mut_atomic
    def __setattr__(self, name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__
        o_setattr = self.__syncwave_original_cls__.__setattr__

        field_ta = ctx.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            o_setattr(self, name, new_value)
            return

        field_ctx = ctx.fields_ctx.get(name)
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
    def __delattr__(self, name: str) -> None:
        ctx = self.__syncwave_ctx__
        if name in ctx.fields_type_adapter:
            raise AttributeError(
                f"Cannot delete tracked field `{name}` to keep the model in sync. "
                "Set it to `None` instead (if the field type allows it)."
            )
        o_delattr = self.__syncwave_original_cls__.__delattr__
        o_delattr(self, name)

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        original_cls = cls.__syncwave_original_cls__
        original_schema = handler.generate_schema(original_cls)

        non_instance_schema = cs.no_info_after_validator_function(
            cls.__syncwave_new__, original_schema
        )
        instance_schema = cs.is_instance_schema(cls)
        return cs.union_schema(
            [instance_schema, non_instance_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                lambda v, nxt: nxt(v),
                schema=original_schema,
            ),
        )


def create_sync_model(cls: type[T], rename: bool | str = True) -> type[T]:
    cls_name = f"Sync{cls.__name__}" if rename is True else rename or cls.__name__
    return type(
        cls_name,
        (SyncModel, cls),
        {"__module__": cls.__module__, "__syncwave_original_cls__": cls},
    )


def _setattr_union(
    self: SyncModel,
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
        original_setattr(self, field_name, old_value)  # TODO: necessary?
    else:
        if old_is_reactive:
            old_value.__syncwave_kill__()
        if new_is_reactive:
            new_value.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
        original_setattr(self, field_name, new_value)
