from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from inspect import isclass
from typing import TYPE_CHECKING, Any, Final, TypeGuard
from typing_extensions import Self

from pydantic import BaseModel, RootModel, TypeAdapter
from pydantic import GetCoreSchemaHandler as Handler
from pydantic.dataclasses import is_pydantic_dataclass
from pydantic_core import core_schema as cs

from .errors import DeadReferenceError, unreachable
from .ownership import detach, ingest
from .reactive import (
    Context,
    Reactive,
    State,
    StoreRef,
    UnionCtx,
    is_reactive,
    mut_reactive_op,
    ser_factory,
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
    SMS = BaseModel | RootModel | PydanticDataclass

__all__ = ["SyncModel", "is_sync_model_supported"]


_MISSING: Final = object()


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
        [is_sync_model_supported](https://syncwave.dev/usage/syncwave/)

    Args:
        cls: Object to test.

    Returns:
        `True` if `cls` can be passed to [Syncwave.make_reactive](https://syncwave.dev/api/syncwave/#syncwave.Syncwave.make_reactive)
            or used with [Syncwave.register](https://syncwave.dev/api/syncwave/#syncwave.Syncwave.register),
            and False otherwise.

    """
    if not isclass(cls):
        return False
    if issubclass(cls, SyncModel):
        return False
    # RootModel is a subclass of BaseModel
    return issubclass(cls, BaseModel) or is_pydantic_dataclass(cls)


def _og_setattr(self: SyncModel, name: str, value: Any) -> None:
    self.__syncwave_original_cls__.__setattr__(self, name, value)


def _og_delattr(self: SyncModel, name: str) -> None:
    self.__syncwave_original_cls__.__delattr__(self, name)


@dataclass(frozen=True)
class SyncModelCtx(Context):
    tp: type[SyncModel]
    fields_ctx: dict[str, Context | UnionCtx]
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
        [SyncModel](https://syncwave.dev/usage/syncwave/)

    """

    __syncwave_ctx__: SyncModelCtx
    __syncwave_original_cls__: type[SMS]

    @classmethod
    def __new(cls, instance: SMS) -> Self:
        instance = copy(instance)  # shallow copy to avoid mutating the original
        instance.__class__ = cls  # swap the class which makes it a SyncModel
        return instance  # ty: ignore[invalid-return-type]

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        cls_schema = handler.generate_schema(cls.__syncwave_original_cls__)

        inst_schema = cs.is_instance_schema(cls)
        non_inst_schema = cs.no_info_after_validator_function(cls.__new, cls_schema)

        return cs.union_schema(
            [inst_schema, non_inst_schema],
            serialization=cs.wrap_serializer_function_ser_schema(
                ser_factory(),
                schema=cls_schema,
            ),
        )

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncModelCtx) -> None:
        object.__setattr__(self, "__syncwave_state__", State.LIVE)
        object.__setattr__(self, "__syncwave_sref__", sref)
        object.__setattr__(self, "__syncwave_ctx__", ctx)

        for name, field_ctx in ctx.fields_ctx.items():
            value = self.__dict__.get(name)
            # case 1: non-reactive content type
            # skipped since fields_ctx only contains reactive fields
            # case 2: fixed reactive content type
            if isinstance(field_ctx, Context):
                # if `field_ctx` is a Context, `value` can't be None
                value.__syncwave_init__(sref, field_ctx)  # ty: ignore[unresolved-attribute]
            # case 3: union content type
            elif isinstance(field_ctx, UnionCtx):
                if is_reactive(value):
                    value.__syncwave_init__(sref, field_ctx[type(value)])
            else:
                unreachable()

    def __syncwave_kill__(self) -> None:
        for name in self.__syncwave_ctx__.fields_ctx:
            value = self.__dict__.get(name)
            if is_reactive(value):
                value.__syncwave_kill__()
        object.__setattr__(self, "__syncwave_state__", State.DEAD)

    def __syncwave_update__(self, new: Self) -> None:
        ctx = self.__syncwave_ctx__

        for name in ctx.fields_type_adapter:
            field_ctx = ctx.fields_ctx.get(name)
            new_value = new.__dict__.get(name)

            # case 1: non-reactive content type
            if field_ctx is None:
                _og_setattr(self, name, new_value)
            # case 2: fixed reactive content type
            elif isinstance(field_ctx, Context):
                old_value = self.__dict__[name]  # can't be None
                old_value.__syncwave_update__(new_value)
                _og_setattr(self, name, old_value)  # in case there's a hook to trigger
            # case 3: union content type
            elif isinstance(field_ctx, UnionCtx):
                old_value = self.__dict__.get(name)
                self.__setattr_union(name, old_value, new_value, field_ctx)
            else:
                unreachable()

    def __getattribute__(self, name: str) -> Any:
        __dict__ = object.__getattribute__(self, "__dict__")
        ctx: SyncModelCtx | None = __dict__.get("__syncwave_ctx__")
        if ctx is not None:
            field_ta = ctx.fields_type_adapter.get(name)
            if field_ta is not None:
                if __dict__["__syncwave_state__"] is State.DEAD:
                    raise DeadReferenceError(reference=self)
                value = __dict__.get(name, _MISSING)
                if value is not _MISSING:
                    return detach(value, field_ta)
        return object.__getattribute__(self, name)

    @mut_reactive_op(inert_fn=_og_setattr)
    def __setattr__(self, name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__

        field_ta = ctx.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            # will still trigger `on_change` even though the field is not tracked
            _og_setattr(self, name, new_value)
            return

        field_ctx = ctx.fields_ctx.get(name)
        new_value = ingest(new_value, field_ta)

        # case 1: non-reactive content type
        if field_ctx is None:
            _og_setattr(self, name, new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = self.__dict__[name]  # can't be None
            old_value.__syncwave_update__(new_value)
            _og_setattr(self, name, old_value)
        # case 3: union content type
        elif isinstance(field_ctx, UnionCtx):
            old_value = self.__dict__.get(name)
            self.__setattr_union(name, old_value, new_value, field_ctx)
        else:
            unreachable()

    @mut_reactive_op(inert_fn=_og_delattr)
    def __delattr__(self, name: str) -> None:
        ctx = self.__syncwave_ctx__
        if name in ctx.fields_type_adapter:
            raise AttributeError(
                f"Cannot delete tracked field `{name}` to keep the model in sync. "
                "Set it to `None` instead (if the field type allows it)."
            )
        _og_delattr(self, name)

    def __str__(self) -> str:
        return self.__syncwave_original_cls__.__str__(self)  # ty: ignore[invalid-argument-type]

    def __repr__(self) -> str:
        state = self.__syncwave_state__.value
        return f"<{self.__syncwave_original_cls__.__repr__(self)} ({state})>"  # ty: ignore[invalid-argument-type]

    def __setattr_union(self, field: str, old: Any, new: Any, u_ctx: UnionCtx) -> None:
        old_is_reactive = is_reactive(old)
        new_is_reactive = is_reactive(new)
        same_type = type(old) is (new_type := type(new))

        if old_is_reactive and new_is_reactive and same_type:
            old.__syncwave_update__(new)
            _og_setattr(self, field, old)  # in case there's a hook to trigger
        else:
            if old_is_reactive:
                old.__syncwave_kill__()
            if new_is_reactive:
                new.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            _og_setattr(self, field, new)


def create_sync_model(cls: type[SMS], *, rename: bool | str = True) -> type[SyncModel]:
    cls_name = f"Sync{cls.__name__}" if rename is True else rename or cls.__name__
    return type(
        cls_name,
        (SyncModel, cls),
        {"__module__": cls.__module__, "__syncwave_original_cls__": cls},
    )
