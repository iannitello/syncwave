from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from inspect import isclass
from typing import TYPE_CHECKING, Any, ClassVar, Final, Generic, TypeVar, overload
from typing_extensions import Self, TypeIs, dataclass_transform

from pydantic import BaseModel, GetCoreSchemaHandler as Handler, RootModel, TypeAdapter
from pydantic.dataclasses import dataclass as py_dataclass, is_pydantic_dataclass
from pydantic.fields import Field, PrivateAttr
from pydantic.root_model import RootModelRootType
from pydantic_core import core_schema as cs

from .errors import DeadReferenceError, unreachable
from .ownership import detach, ingest
from .reactive import (
    Context,
    Reactive,
    ReactiveProtocol,
    StoreRef,
    SyncState,
    UnionCtx,
    dead_guard,
    is_reactive,
    is_reactive_cls,
    mut_reactive_op,
    reactive_op,
)

__all__ = ["SyncModel", "SyncRoot", "is_sync_dataclass", "sync_dataclass"]


@dataclass(frozen=True)
class SyncModelCtx(Context):
    tp: type[SyncModel | SyncDataclass]
    fields_ctx: dict[str, Context | UnionCtx]
    fields_type_adapter: dict[str, TypeAdapter[Any]]


class SyncModel(BaseModel, Reactive[SyncModelCtx], _syncwave=True):
    """Base class for reactive Pydantic models.

    Subclass `SyncModel` instead of `pydantic.BaseModel` to define a reactive model.
    Fields, validators, `model_config`, and everything else work as with a regular
    Pydantic model. A reactive model has three states (see `SyncState`):

    - An instance created directly (`Settings(volume=8)`) is inert: it behaves like a
      regular Pydantic instance and is not connected to any store.
    - An instance read from a store is live: assigning a field validates the value and
      writes it to the JSON file, and changes to the file are applied to the instance.
    - An instance removed from its store is dead: any operation raises
      `DeadReferenceError`.

    A value enters a store as a copy, so the instance you pass stays inert and the
    store hands you a live one. Reactive fields (`SyncList[str]`, another reactive
    model, ...) are live as well when the model is live. Subclassing a reactive model
    gives another reactive model, and so does `pydantic.create_model` with
    `__base__=SyncModel`. For a single-value model, see `SyncRoot`.

    Restrictions: a reactive model cannot be frozen and cannot have frozen fields, and
    field defaults must be valid for their type (checked at class definition).

    Example:
    ```python
    from syncwave import SyncList, SyncModel, Syncwave

    syncwave = Syncwave()


    class Settings(SyncModel):
        volume: int = 8
        recent_files: SyncList[str] = []


    settings = syncwave.create_store(Settings, name="settings")
    settings.volume = 7  # written to settings.json
    settings.recent_files.append("notes.txt")  # written too
    print(settings.recent_files)  # ['notes.txt']
    ```

    ---

    Abstract: Usage Documentation
        [SyncModel](https://syncwave.dev/usage/models/)

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
        _init(self, sref, ctx)

    def __syncwave_kill__(self) -> None:
        _kill(self)

    def __syncwave_update__(self, new: Self) -> None:
        _update(self, new, BaseModel.__setattr__)

    def __getattribute__(self, name: str) -> Any:
        return _getattribute(self, name)

    @mut_reactive_op(inert_fn=BaseModel.__setattr__)
    def __setattr__(self, name: str, value: Any) -> None:
        _setattr(self, name, value, BaseModel.__setattr__)

    @mut_reactive_op(inert_fn=BaseModel.__delattr__)
    def __delattr__(self, name: str) -> None:
        _delattr(self, name, BaseModel.__delattr__)

    def __repr__(self) -> str:
        return f"<{BaseModel.__repr__(self)} ({self.__syncwave_state__.value})>"

    @reactive_op()
    def __copy__(self) -> Self:
        shallow = BaseModel.__copy__(self)
        for name in _LIVE_ATTRS:
            shallow.__dict__.pop(name, None)
        _detach_fields(self, shallow)
        return shallow

    @reactive_op()
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        shallow = BaseModel.__copy__(self)
        for name in _LIVE_ATTRS:
            shallow.__dict__.pop(name, None)
        return BaseModel.__deepcopy__(shallow, memo)

    __hash__ = None


class SyncRoot(SyncModel, RootModel, Generic[RootModelRootType], _syncwave=True):
    """Base class for reactive root models.

    Shorthand for `class Locale(SyncModel, RootModel[str])`: a reactive model holding a
    single value under the `root` field. Everything from `SyncModel` applies. The JSON
    file holds the bare value, not an object around it.

    Example:
    ```python
    from syncwave import SyncRoot, Syncwave

    syncwave = Syncwave()


    class Locale(SyncRoot[str]): ...


    locale = syncwave.create_store(Locale, name="locale")
    locale.root = "en-US"  # written to locale.json as "en-US"
    ```

    ---

    Abstract: Usage Documentation
        [SyncRoot](https://syncwave.dev/usage/models/)

    """


if TYPE_CHECKING:
    from collections.abc import Callable as F
    from typing import Literal, Protocol

    from pydantic import ConfigDict
    from pydantic.dataclasses import PydanticDataclass

    class SyncDataclass(PydanticDataclass, ReactiveProtocol[SyncModelCtx], Protocol):
        __syncwave_base_setattr__: ClassVar[FSet]
        __syncwave_base_delattr__: ClassVar[FDel]

    RM = SyncModel | SyncDataclass
    RM_T = TypeVar("RM_T", bound=RM)

    FSet = F[[RM, str, Any], None]
    FDel = F[[RM, str], None]


def is_pydantic_model(cls: type[Any]) -> TypeIs[type[BaseModel | PydanticDataclass]]:
    # assumes `cls` is a class (called from trusted code)
    return hasattr(cls, "__pydantic_fields__")


_T = TypeVar("_T")


@overload
def sync_dataclass(
    *,
    init: Literal[False] = False,
    repr: bool = True,
    eq: bool = True,
    order: bool = False,
    unsafe_hash: Literal[False] = False,
    frozen: Literal[False] = False,
    config: ConfigDict | type[object] | None = None,
    kw_only: bool = False,
    slots: Literal[False] = False,
) -> F[[type[_T]], type[SyncDataclass]]: ...
@overload
def sync_dataclass(
    _cls: type[_T],
    *,
    init: Literal[False] = False,
    repr: bool = True,
    eq: bool = True,
    order: bool = False,
    unsafe_hash: Literal[False] = False,
    frozen: Literal[False] = False,
    config: ConfigDict | type[object] | None = None,
    kw_only: bool = False,
    slots: Literal[False] = False,
) -> type[SyncDataclass]: ...
@dataclass_transform(field_specifiers=(field, Field, PrivateAttr))
def sync_dataclass(
    _cls: type[_T] | None = None,
    *,
    init: Literal[False] = False,
    repr: bool = True,  # ruff: ignore[builtin-argument-shadowing]
    eq: bool = True,
    order: bool = False,
    unsafe_hash: Literal[False] = False,
    frozen: Literal[False] = False,
    config: ConfigDict | type[object] | None = None,
    kw_only: bool = False,
    slots: Literal[False] = False,
) -> F[[type[_T]], type[SyncDataclass]] | type[SyncDataclass]:
    """Make a reactive Pydantic dataclass.

    Use it like `pydantic.dataclasses.dataclass`, which it applies: the class becomes a
    regular Pydantic dataclass, validated the same way, with the reactive protocol
    installed on top. Instances behave like `SyncModel` instances: created directly
    they are inert, read from a store they are live, removed from a store they are dead
    (see `SyncState`).

    The class keeps its own MRO, so `isinstance(point, Reactive)` is `False`: use
    `is_sync_dataclass` to check for a reactive dataclass. Type checkers don't see
    `sync_state` and `sync_live` on the class; narrow with `is_sync_dataclass` first.

    Don't stack it with another dataclass decorator. To make a reactive version of an
    existing dataclass, subclass it: `@sync_dataclass class SyncPoint(Point): ...`.
    Mutable defaults need `field(default_factory=...)`, as with any dataclass.

    Example:
    ```python
    from dataclasses import field

    from syncwave import SyncList, Syncwave, sync_dataclass

    syncwave = Syncwave()


    @sync_dataclass
    class Point:
        x: int = 0
        y: int = 0
        tags: SyncList[str] = field(default_factory=SyncList)


    points = syncwave.create_store(SyncList[Point], name="points")
    points.append(Point(x=1, y=2))
    points[0].x = 3  # written to points.json
    ```

    ---

    Abstract: Usage Documentation
        [sync_dataclass](https://syncwave.dev/usage/models/)

    Args:
        _cls: The class to decorate. Omit it to pass options: `@sync_dataclass(...)`.
        init: Must be `False`, as for `pydantic.dataclasses.dataclass`.
        repr: Whether to generate a `__repr__`.
        eq: Whether to generate an `__eq__`.
        order: Whether to generate the comparison methods.
        unsafe_hash: Must be `False`: reactive objects are mutable, so never hashable.
        frozen: Must be `False`: a frozen dataclass cannot change, so it cannot sync.
        config: The Pydantic config, as for `pydantic.dataclasses.dataclass`.
        kw_only: Whether the `__init__` parameters are keyword-only.
        slots: Must be `False`: reactive objects need a `__dict__`.

    Returns:
        The decorated class, or a decorator if `_cls` is omitted.

    """
    if unsafe_hash:
        raise TypeError("`unsafe_hash=True` is not supported.")
    if frozen:
        raise TypeError("`frozen=True` is not supported.")
    if slots:
        raise TypeError("`slots=True` is not supported.")

    def decorate(cls: type[_T]) -> type[SyncDataclass]:
        if not isclass(cls):
            raise TypeError("`@sync_dataclass` must decorate a class.")
        if issubclass(cls, BaseModel):
            raise TypeError("`@sync_dataclass` cannot decorate a `BaseModel`.")
        if "__dataclass_fields__" in cls.__dict__:
            name = cls.__qualname__
            raise TypeError(
                f"`{name}` is already a dataclass. Remove the other decorator, or "
                f"subclass `{name}` to make a reactive version of it."
            )
        if "__slots__" in cls.__dict__:
            raise TypeError("`@sync_dataclass` cannot decorate a slotted class.")

        cs = SyncModel.__dict__["__get_pydantic_core_schema__"]
        cls.__get_pydantic_core_schema__ = cs  # ty: ignore[invalid-assignment]

        dc = py_dataclass(
            init=init,
            repr=repr,
            eq=eq,
            order=order,
            unsafe_hash=False,
            # frozen=False,  # may raise an unnecessary warning if set explicitly
            config=config,
            kw_only=kw_only,
            slots=False,
        )(cls)

        def get_base_fn(fn_name: str) -> F:
            if fn_name in dc.__dict__:
                return dc.__dict__[fn_name]
            sw_attr = f"__syncwave_base_{fn_name.strip('_')}__"
            if (fn := getattr(dc, sw_attr, None)) is not None:
                return fn
            return getattr(dc, fn_name)

        dc.__syncwave_base_setattr__ = (base_setattr := get_base_fn("__setattr__"))
        dc.__syncwave_base_delattr__ = (base_delattr := get_base_fn("__delattr__"))

        def update_(self: SyncDataclass, new: SyncDataclass) -> None:
            _update(self, new, base_setattr)

        @mut_reactive_op(inert_fn=base_setattr)
        def setattr_(self: SyncDataclass, name: str, value: Any) -> None:
            _setattr(self, name, value, base_setattr)

        @mut_reactive_op(inert_fn=base_delattr)
        def delattr_(self: SyncDataclass, name: str) -> None:
            _delattr(self, name, base_delattr)

        @reactive_op()
        def _copy(self: SyncDataclass) -> SyncDataclass:
            new = object.__new__(type(self))
            data = {k: v for k, v in self.__dict__.items() if k not in _LIVE_ATTRS}
            new.__dict__.update(data)
            _detach_fields(self, new)
            return new

        @reactive_op()
        def _deepcopy(self: SyncDataclass, memo: dict[int, Any]) -> SyncDataclass:
            new = object.__new__(type(self))
            data = {k: v for k, v in self.__dict__.items() if k not in _LIVE_ATTRS}
            new.__dict__.update(deepcopy(data, memo))
            return new

        base_repr = dc.__dict__["__repr__"]

        def _repr(self: SyncDataclass) -> str:
            return f"<{base_repr(self)} ({self.__syncwave_state__.value})>"

        dc.__syncwave_is_reactive__ = True
        dc.__syncwave_state__ = SyncState.INERT
        dc.sync_live = Reactive.__dict__["sync_live"]
        dc.sync_state = Reactive.__dict__["sync_state"]
        dc.__syncwave_init__ = _init
        dc.__syncwave_kill__ = _kill
        dc.__syncwave_update__ = update_
        dc.__getattribute__ = _getattribute
        dc.__setattr__ = setattr_
        dc.__delattr__ = delattr_
        dc.__copy__ = _copy
        dc.__deepcopy__ = _deepcopy
        if repr:
            dc.__repr__ = _repr
        dc.__hash__ = None

        # for unresolved forward refs, the checks run at `create_store` instead
        if dc.__pydantic_complete__:
            from .tp_validation import drill_tp

            drill_tp(dc)
        return dc

    return decorate if _cls is None else decorate(_cls)


def is_sync_dataclass(cls: type[Any], /) -> TypeIs[type[SyncDataclass]]:
    """Whether a class is a reactive dataclass, made with `sync_dataclass`.

    A reactive dataclass is also a Pydantic dataclass.

    Args:
        cls: The class.

    Returns:
        `True` if the class is a reactive dataclass, `False` otherwise.

    """
    return isclass(cls) and is_pydantic_dataclass(cls) and is_reactive_cls(cls)


_MISSING: Final = object()
_LIVE_ATTRS: Final = ("__syncwave_state__", "__syncwave_sref__", "__syncwave_ctx__")


def _init(self: RM, sref: StoreRef, ctx: SyncModelCtx) -> None:
    object.__setattr__(self, "__syncwave_state__", SyncState.LIVE)
    object.__setattr__(self, "__syncwave_sref__", sref)
    object.__setattr__(self, "__syncwave_ctx__", ctx)

    for name, field_ctx in ctx.fields_ctx.items():
        value = self.__dict__.get(name)

        # can be the case for a default value e.g. `SyncList[str] = []`
        if not is_reactive(value):
            ta = ctx.fields_type_adapter[name]
            value = self.__dict__[name] = ta.validate_python(value)

        # case 1: non-reactive content type
        # skipped since fields_ctx only contains reactive fields
        # case 2: fixed reactive content type
        if isinstance(field_ctx, Context):
            value.__syncwave_init__(sref, field_ctx)
        # case 3: union content type
        elif isinstance(field_ctx, UnionCtx):
            if is_reactive(value):
                value.__syncwave_init__(sref, field_ctx[type(value)])
        else:
            unreachable()


def _kill(self: RM) -> None:
    for name in self.__syncwave_ctx__.fields_ctx:
        value = self.__dict__.get(name)
        if is_reactive(value):
            value.__syncwave_kill__()
    object.__setattr__(self, "__syncwave_state__", SyncState.DEAD)


def _update(self: RM_T, new: RM_T, base_setattr: FSet) -> None:
    ctx = self.__syncwave_ctx__

    for name in ctx.fields_type_adapter:
        field_ctx = ctx.fields_ctx.get(name)
        new_value = new.__dict__.get(name)

        # can be the case for a default value e.g. `SyncList[str] = []`
        if field_ctx is not None and not is_reactive(new_value):
            ta = ctx.fields_type_adapter[name]
            new_value = ta.validate_python(new_value)

        # case 1: non-reactive content type
        if field_ctx is None:
            _base_write(self, name, new_value, base_setattr)
        # case 2: fixed reactive content type
        elif isinstance(field_ctx, Context):
            old_value = self.__dict__[name]  # can't be None
            old_value.__syncwave_update__(new_value)
            _base_write(self, name, old_value, base_setattr)
        # case 3: union content type
        elif isinstance(field_ctx, UnionCtx):
            old_value = self.__dict__.get(name)
            _setattr_union(self, name, old_value, new_value, field_ctx, base_setattr)
        else:
            unreachable()


def _getattribute(self: RM, name: str) -> Any:
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


def _setattr(self: RM, name: str, new_value: Any, base_setattr: FSet) -> None:
    ctx = self.__syncwave_ctx__

    field_ta = ctx.fields_type_adapter.get(name)
    # case for a non-model field
    if field_ta is None:
        # will still trigger `on_change` even though the field is not tracked
        _base_write(self, name, new_value, base_setattr)
        return

    field_ctx = ctx.fields_ctx.get(name)
    new_value = ingest(new_value, field_ta)

    # case 1: non-reactive content type
    if field_ctx is None:
        _base_write(self, name, new_value, base_setattr)
    # case 2: fixed reactive content type
    elif isinstance(field_ctx, Context):
        old_value = self.__dict__[name]  # can't be None
        old_value.__syncwave_update__(new_value)
        _base_write(self, name, old_value, base_setattr)
    # case 3: union content type
    elif isinstance(field_ctx, UnionCtx):
        old_value = self.__dict__.get(name)
        _setattr_union(self, name, old_value, new_value, field_ctx, base_setattr)
    else:
        unreachable()


def _delattr(self: RM, name: str, base_delattr: FDel) -> None:
    ctx = self.__syncwave_ctx__
    if name in ctx.fields_type_adapter:
        raise AttributeError(
            f"Cannot delete tracked field `{name}` to keep the model in sync. "
            "Set it to `None` instead (if the field type allows it)."
        )
    base_delattr(self, name)


def _setattr_union(
    self: RM, f_name: str, old: Any, new: Any, u_ctx: UnionCtx, base_setattr: FSet
) -> None:
    old_is_reactive = is_reactive(old)
    new_is_reactive = is_reactive(new)
    same_type = type(old) is (new_type := type(new))

    if old_is_reactive and new_is_reactive and same_type:
        old.__syncwave_update__(new)
        _base_write(self, f_name, old, base_setattr)
    else:
        if old_is_reactive:
            old.__syncwave_kill__()
        if new_is_reactive:
            new.__syncwave_init__(self.__syncwave_sref__, u_ctx[new_type])
        _base_write(self, f_name, new, base_setattr)


def _base_write(self: RM, name: str, value: Any, base_setattr: FSet) -> None:
    # TODO review this
    live = {k: self.__dict__[k] for k in _LIVE_ATTRS}
    base_setattr(self, name, value)
    # With `validate_assignment=True`, `base_setattr` validates `value` again, and a
    # reactive value comes back as an inert copy. On a dataclass, it also rebuilds the
    # instance `__dict__` with the fields only, dropping the `__syncwave_*` entries.
    self.__dict__.update(live)
    if is_reactive(value) and name in type(self).__pydantic_fields__:
        self.__dict__[name] = value


def _detach_fields(self: RM_T, target: RM_T) -> None:
    for name in type(self).__pydantic_fields__:
        if name in target.__dict__:
            target.__dict__[name] = getattr(self, name)
