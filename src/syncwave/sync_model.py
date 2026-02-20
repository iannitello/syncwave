from __future__ import annotations

import dataclasses as dc
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import TYPE_CHECKING, Any, Callable, Union
from typing_extensions import Self, TypeGuard

import pydantic.dataclasses as py_dc
from pydantic import BaseModel, TypeAdapter
from pydantic import GetCoreSchemaHandler as Handler
from pydantic_core import core_schema as cs

from .reactive import (
    Context,
    ContextMap,
    DeadReferenceError,
    Reactive,
    StoreRef,
    assert_never,
    mut_atomic,
)

if TYPE_CHECKING:
    from _typeshed import DataclassInstance as Dataclass

    # SyncModelSupported
    # A user-defined class that can be made reactive, either one of the following:
    #   1. a subclass of `pydantic.BaseModel`,
    #   2. a subclass of `pydantic.RootModel`,
    #   3. a class decorated with `@pydantic.dataclasses.dataclass`, or
    #   4. a class decorated with `@dataclasses.dataclass`.
    _SMS = Union[BaseModel, Dataclass]


def is_sync_model_supported(cls: type) -> TypeGuard[type[_SMS]]:
    if not isclass(cls):
        return False
    if issubclass(cls, SyncModel):
        return False
    # RootModel is a subclass of BaseModel, and a pydantic dataclass is a dataclass
    return issubclass(cls, BaseModel) or dc.is_dataclass(cls)


@dataclass(frozen=True)
class SyncModelCtx(Context):
    tp: type[SyncModel]
    type_adapter: TypeAdapter[SyncModel]
    fields_ctx: dict[str, Context | ContextMap]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(Reactive):
    __syncwave_ctx__: SyncModelCtx
    __syncwave_original_cls__: type[_SMS]

    @classmethod
    def __new(cls, instance: _SMS) -> Self:
        instance.__class__ = cls
        instance: Self = instance  # ty: ignore[invalid-assignment]
        return instance

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        original_cls = cls.__syncwave_original_cls__
        cls_schema = handler.generate_schema(original_cls)

        inst_schema = cs.is_instance_schema(cls)
        non_inst_schema = cs.no_info_after_validator_function(cls.__new, cls_schema)

        return cs.union_schema(
            [inst_schema, non_inst_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                lambda v, nxt: nxt(v),
                schema=cls_schema,
            ),
        )

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncModelCtx) -> None:
        object.__setattr__(self, "__syncwave_sref__", sref)
        object.__setattr__(self, "__syncwave_ctx__", ctx)
        object.__setattr__(self, "__syncwave_live__", True)

        for name, field_ctx in ctx.fields_ctx.items():
            value = getattr(self, name, None)
            # case 1: non-reactive content type
            # skipped since fields_ctx only contains reactive fields
            # case 2: fixed reactive content type
            if isinstance(field_ctx, Context):
                # if `field_ctx` is a Context, `value` can't be None
                value.__syncwave_init__(sref, field_ctx)  # ty: ignore[unresolved-attribute]
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                if isinstance(value, Reactive):
                    value.__syncwave_init__(sref, field_ctx[type(value)])
            else:
                assert_never()

    def __syncwave_kill__(self) -> None:
        # TODO doesn't really work, isn't really clean
        # e.g. can't call `repr` on the instance after killing it
        for name in self.__syncwave_ctx__.fields_ctx:
            value = getattr(self, name, None)
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        # see the comment in __getattr__ for why we pop the fields from __dict__
        for name in self.__syncwave_ctx__.fields_type_adapter:
            self.__dict__.pop(name, None)
        object.__setattr__(self, "__syncwave_live__", False)

    def __syncwave_update__(self, new: Self | Mapping[str, Any]) -> None:
        ctx = self.__syncwave_ctx__
        o_setattr = self.__syncwave_original_cls__.__setattr__

        new_ = ctx.type_adapter.validate_python(new)

        for name in ctx.fields_type_adapter:
            field_ctx = ctx.fields_ctx.get(name)
            new_value = getattr(new_, name, None)

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

    def __getattr__(self, name: str) -> Any:
        # __getattribute__ would always trigger (methods, internal properties, etc.).
        # Instead, we pop the fields from __dict__ when the instance is killed
        # so subsequent attribute access triggers __getattr__,
        # and we can raise DeadReferenceError.

        live = self.__dict__.get("__syncwave_live__")
        if live is not None:  # instance is initialized
            ctx = self.__syncwave_ctx__
            if name in ctx.fields_type_adapter:
                if live:
                    # __getattr__ should never be called for a tracked field
                    # if the instance is still alive
                    assert_never()
                raise DeadReferenceError(reference=self)

        # TODO: may not have a __getattr__
        o_getattr = self.__syncwave_original_cls__.__getattr__
        return o_getattr(self, name)

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


def create_sync_model(cls: type[_SMS], rename: bool | str = True) -> type[SyncModel]:
    cls_name = f"Sync{cls.__name__}" if rename is True else rename or cls.__name__
    Model = type(
        cls_name,
        (SyncModel, cls),
        {"__module__": cls.__module__, "__syncwave_original_cls__": cls},
    )
    if dc.is_dataclass(Model) and not py_dc.is_pydantic_dataclass(Model):
        return py_dc.dataclass(Model)
    return Model


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
