from __future__ import annotations

from dataclasses import dataclass
from inspect import isclass
from typing import TYPE_CHECKING, Any, Union
from typing_extensions import Self, TypeGuard

from pydantic import BaseModel, RootModel, TypeAdapter
from pydantic import GetCoreSchemaHandler as Handler
from pydantic.dataclasses import is_pydantic_dataclass
from pydantic_core import core_schema as cs

from .reactive import (
    Context,
    ContextMap,
    DeadReferenceError,
    Reactive,
    StoreRef,
    mut_atomic,
    unreachable,
)

if TYPE_CHECKING:
    from typing import ClassVar, Protocol

    from _typeshed import DataclassInstance as StandardDataclass
    from pydantic import ConfigDict
    from pydantic.fields import FieldInfo

    class PydanticDataclass(StandardDataclass, Protocol):
        __pydantic_config__: ClassVar[ConfigDict]
        __pydantic_fields__: ClassVar[dict[str, FieldInfo]]

    # SyncModelSupported
    # A user-defined class that can be made reactive. The supported types are:
    #   1. subclasses of `pydantic.BaseModel`,
    #   2. subclasses of `pydantic.RootModel`,
    #   3. classes decorated with `@pydantic.dataclasses.dataclass`.
    SMS = Union[BaseModel, RootModel, PydanticDataclass]

__all__ = ["SyncModel", "is_sync_model_supported"]


def is_sync_model_supported(cls: Any) -> TypeGuard[type[SMS]]:
    """Return whether `cls` can be made into a `SyncModel`.

    Supported classes are:

    - subclasses of `pydantic.BaseModel`
    - subclasses of `pydantic.RootModel`
    - classes decorated with `pydantic.dataclasses.dataclass`

    Standard-library dataclasses or plain Python classes are not supported.

    Example:
    ```python
    from pydantic import BaseModel
    from syncwave import is_sync_model_supported


    class Customer(BaseModel):
        name: str


    is_sync_model_supported(Customer)  # True
    is_sync_model_supported(dict)  # False
    ```

    ---

    Abstract: Usage Documentation
        [is_sync_model_supported](https://placeholder.dev/usage/syncwave/)

    Args:
        cls: Object to test.

    Returns:
        `True` if `cls` can be passed to [Syncwave.make_reactive](https://placeholder.dev/api/syncwave/#syncwave.Syncwave.make_reactive)
            or used with [Syncwave.register](https://placeholder.dev/api/syncwave/#syncwave.Syncwave.register),
            and False otherwise.

    """
    if not isclass(cls):
        return False
    if issubclass(cls, SyncModel):
        return False
    # RootModel is a subclass of BaseModel
    return issubclass(cls, BaseModel) or is_pydantic_dataclass(cls)


@dataclass(frozen=True)
class SyncModelCtx(Context):
    tp: type[SyncModel]
    fields_ctx: dict[str, Context | ContextMap]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(Reactive):
    """Base class for reactive models.

    Instances of `SyncModel` behave just like the original model: field access and
    assignment work exactly the same way. The difference is that every field assignment
    triggers a write to the backing JSON file, and external changes to the file are
    reflected in the same object.

    You will rarely interact with `SyncModel` directly. Instances appear when you access
    reactive model values from a store, and `isinstance(value, SyncModel)` is the main
    way to check for them.

    Example:
    ```python
    from pydantic import BaseModel
    from syncwave import SyncModel, Syncwave

    syncwave = Syncwave()


    @syncwave.register(name="customers")
    class Customer(BaseModel):
        name: str
        age: int


    customers = syncwave["customers"]
    customers.append({"name": "Alice", "age": 30})
    alice = customers[0]
    isinstance(alice, SyncModel)  # True
    alice.age = 31  # writes to customers.json immediately
    print(alice)  # name='Alice' age=31
    ```

    ---

    Abstract: Usage Documentation
        [SyncModel](https://placeholder.dev/usage/syncwave/)

    """

    __syncwave_ctx__: SyncModelCtx
    __syncwave_original_cls__: type[SMS]

    @classmethod
    def __new(cls, instance: SMS) -> Self:
        instance.__class__ = cls
        instance: Self = instance  # ty: ignore[invalid-assignment]
        return instance

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        cls_schema = handler.generate_schema(cls.__syncwave_original_cls__)

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
                unreachable()

    def __syncwave_kill__(self) -> None:
        for name in self.__syncwave_ctx__.fields_ctx:
            value = getattr(self, name, None)
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        # see the comment in __getattr__ for why we pop the fields from __dict__
        for name in self.__syncwave_ctx__.fields_type_adapter:
            self.__dict__.pop(name, None)
        object.__setattr__(self, "__syncwave_live__", False)

    def __syncwave_update__(self, new: Self) -> None:
        ctx = self.__syncwave_ctx__
        o_setattr = self.__syncwave_original_cls__.__setattr__

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
                o_setattr(self, name, old_value)  # in case there's a hook to trigger
            # case 3: union content type
            elif isinstance(field_ctx, ContextMap):
                old_value = getattr(self, name, None)
                self.__setattr_union(name, old_value, new_value, field_ctx)
            else:
                unreachable()

    def __getattr__(self, name: str) -> Any:
        # __getattribute__ would always trigger (methods, internal properties, etc.).
        # Instead, we pop the fields from __dict__ when the instance is killed
        # so subsequent attribute access triggers __getattr__,
        # and we can raise DeadReferenceError.
        ctx = self.__syncwave_ctx__
        if name in ctx.fields_type_adapter:
            if not self.__syncwave_live__:
                raise DeadReferenceError(reference=self)
            # __getattr__ shouldn't be called for a tracked field on a live instance
            unreachable()

        if issubclass(self.__syncwave_original_cls__, BaseModel):
            o_getattr = self.__syncwave_original_cls__.__getattr__  # ty: ignore[unresolved-attribute]
            return o_getattr(self, name)
        cls_name = type(self).__name__
        raise AttributeError(f"{cls_name!r} object has no attribute {name!r}")

    @mut_atomic
    def __setattr__(self, name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__
        o_setattr = self.__syncwave_original_cls__.__setattr__

        field_ta = ctx.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            # will still trigger `on_change` even though the field is not tracked
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
            self.__setattr_union(name, old_value, new_value, field_ctx)
        else:
            unreachable()

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

    def __str__(self) -> str:
        if not self.__syncwave_live__:
            return f"{type(self).__qualname__}()"
        return self.__syncwave_original_cls__.__str__(self)  # ty: ignore[invalid-argument-type]

    def __repr__(self) -> str:
        if not self.__syncwave_live__:
            return f"{type(self).__qualname__}()"
        return self.__syncwave_original_cls__.__repr__(self)  # ty: ignore[invalid-argument-type]

    def __setattr_union(self, f: str, o: Any, n: Any, u_ctx: ContextMap) -> None:
        o_setattr = self.__syncwave_original_cls__.__setattr__

        old_is_reactive = isinstance(o, Reactive)
        new_is_reactive = isinstance(n, Reactive)
        same_type = type(o) is (new_type := type(n))

        if old_is_reactive and new_is_reactive and same_type:
            o.__syncwave_update__(n)
            o_setattr(self, f, o)  # in case there's a hook to trigger
        else:
            if old_is_reactive:
                o.__syncwave_kill__()
            if new_is_reactive:
                n.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            o_setattr(self, f, n)


def create_sync_model(cls: type[SMS], *, rename: bool | str = True) -> type[SyncModel]:
    cls_name = f"Sync{cls.__name__}" if rename is True else rename or cls.__name__
    return type(
        cls_name,
        (SyncModel, cls),
        {"__module__": cls.__module__, "__syncwave_original_cls__": cls},
    )
