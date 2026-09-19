from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeGuard
from typing_extensions import Self

from pydantic import BaseModel, RootModel, TypeAdapter
from pydantic import GetCoreSchemaHandler as Handler
from pydantic_core import core_schema as cs

from .errors import DeadReferenceError, unreachable
from .ownership import detach, ingest
from .reactive import (
    Context,
    Reactive,
    StoreRef,
    SyncState,
    UnionCtx,
    dead_guard,
    mut_reactive_op,
)

__all__ = ["SyncModel", "SyncRoot"]


_MISSING: Final = object()


@dataclass(frozen=True)
class SyncModelCtx(Context):
    tp: type[RM]
    fields_ctx: dict[str, Context | UnionCtx]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(BaseModel, Reactive, _syncwave_root=True):
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

    @classmethod
    def __get_pydantic_core_schema__(cls, src: Any, handler: Handler) -> cs.CoreSchema:
        return cs.no_info_after_validator_function(dead_guard, handler(src))

    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        if cls.__module__ == __name__:
            return
        from .tp_validation import drill_tp

        drill_tp(cls)
        super().__pydantic_on_complete__()

    def __syncwave_init__(self, sref: StoreRef, ctx: SyncModelCtx) -> None:
        object.__setattr__(self, "__syncwave_state__", SyncState.LIVE)
        object.__setattr__(self, "__syncwave_sref__", sref)
        object.__setattr__(self, "__syncwave_ctx__", ctx)

        for name, field_ctx in ctx.fields_ctx.items():
            value = self.__dict__.get(name)

            # can be the case for a default value e.g. `SyncList[str] = []`
            if not isinstance(value, Reactive):
                ta = ctx.fields_type_adapter[name]
                value = self.__dict__[name] = ta.validate_python(value)

            # case 1: non-reactive content type
            # skipped since fields_ctx only contains reactive fields
            # case 2: fixed reactive content type
            if isinstance(field_ctx, Context):
                value.__syncwave_init__(sref, field_ctx)
            # case 3: union content type
            elif isinstance(field_ctx, UnionCtx):
                if isinstance(value, Reactive):
                    value.__syncwave_init__(sref, field_ctx[type(value)])
            else:
                unreachable()

    def __syncwave_kill__(self) -> None:
        for name in self.__syncwave_ctx__.fields_ctx:
            value = self.__dict__.get(name)
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        object.__setattr__(self, "__syncwave_state__", SyncState.DEAD)

    def __syncwave_update__(self, new: Self) -> None:
        ctx = self.__syncwave_ctx__

        for name in ctx.fields_type_adapter:
            field_ctx = ctx.fields_ctx.get(name)
            new_value = new.__dict__.get(name)

            # can be the case for a default value e.g. `SyncList[str] = []`
            if field_ctx is not None and not isinstance(new_value, Reactive):
                ta = ctx.fields_type_adapter[name]
                new_value = ta.validate_python(new_value)

            # case 1: non-reactive content type
            if field_ctx is None:
                BaseModel.__setattr__(self, name, new_value)
            # case 2: fixed reactive content type
            elif isinstance(field_ctx, Context):
                old_value = self.__dict__[name]  # can't be None
                old_value.__syncwave_update__(new_value)
                BaseModel.__setattr__(self, name, old_value)  # if hook to trigger
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
                if __dict__["__syncwave_state__"] is SyncState.DEAD:
                    raise DeadReferenceError(reference=self)
                value = __dict__.get(name, _MISSING)
                if value is not _MISSING:
                    return detach(value, field_ta)
        return object.__getattribute__(self, name)

    @mut_reactive_op(inert_fn=BaseModel.__setattr__)
    def __setattr__(self, name: str, new_value: Any) -> None:
        ctx = self.__syncwave_ctx__

        field_ta = ctx.fields_type_adapter.get(name)
        # case for a non-model field
        if field_ta is None:
            # will still trigger `on_change` even though the field is not tracked
            BaseModel.__setattr__(self, name, new_value)
            return

        field_ctx = ctx.fields_ctx.get(name)
        new_value = ingest(new_value, field_ta)

        # case 1: non-reactive content type
        if field_ctx is None:
            BaseModel.__setattr__(self, name, new_value)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = self.__dict__[name]  # can't be None
            old_value.__syncwave_update__(new_value)
            BaseModel.__setattr__(self, name, old_value)
        # case 3: union content type
        elif isinstance(field_ctx, UnionCtx):
            old_value = self.__dict__.get(name)
            self.__setattr_union(name, old_value, new_value, field_ctx)
        else:
            unreachable()

    @mut_reactive_op(inert_fn=BaseModel.__delattr__)
    def __delattr__(self, name: str) -> None:
        ctx = self.__syncwave_ctx__
        if name in ctx.fields_type_adapter:
            raise AttributeError(
                f"Cannot delete tracked field `{name}` to keep the model in sync. "
                "Set it to `None` instead (if the field type allows it)."
            )
        BaseModel.__delattr__(self, name)

    def __repr__(self) -> str:
        return f"<{super().__repr__()} ({self.__syncwave_state__.value})>"

    def __setattr_union(self, f_name: str, old: Any, new: Any, u_ctx: UnionCtx) -> None:
        old_is_reactive = isinstance(old, Reactive)
        new_is_reactive = isinstance(new, Reactive)
        same_type = type(old) is (new_type := type(new))

        if old_is_reactive and new_is_reactive and same_type:
            old.__syncwave_update__(new)
            BaseModel.__setattr__(self, f_name, old)  # if hook to trigger
        else:
            if old_is_reactive:
                old.__syncwave_kill__()
            if new_is_reactive:
                new.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
            BaseModel.__setattr__(self, f_name, new)


class SyncRoot(RootModel, SyncModel, _syncwave_root=True): ...  # ruff: ignore[undocumented-public-class]


if TYPE_CHECKING:
    from dataclasses import field
    from typing import ClassVar, Protocol
    from typing_extensions import dataclass_transform

    from _typeshed import DataclassInstance as StandardDataclass
    from pydantic import ConfigDict, Field
    from pydantic.fields import FieldInfo

    class PydanticDataclass(StandardDataclass, Protocol):
        __pydantic_config__: ClassVar[ConfigDict]
        __pydantic_fields__: ClassVar[dict[str, FieldInfo]]

    @dataclass_transform(field_specifiers=(field, Field))
    class SyncDataclass(Reactive, _syncwave_root=True):
        __pydantic_config__: ClassVar[ConfigDict]
        __pydantic_fields__: ClassVar[dict[str, FieldInfo]]

    PM = BaseModel | PydanticDataclass  # Pydantic Model
    RM = SyncModel | SyncDataclass  # Reactive Model


def is_pydantic_model(cls: type[Any]) -> TypeGuard[type[PM]]:
    # assumes `cls` is a class (called from trusted code)
    return hasattr(cls, "__pydantic_fields__")
