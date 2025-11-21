from __future__ import annotations

import dataclasses as dc
from abc import ABCMeta, abstractmethod
from collections.abc import Mapping
from inspect import isclass
from typing import Annotated, Any, Generic, TypeVar, Union, final, get_args, get_origin
from typing_extensions import TypeGuard

from pydantic import BaseModel, RootModel, TypeAdapter, create_model
from pydantic import dataclasses as pdc

from .reactive import Reactive, atomic
from .sync_collection import SyncCollection, SyncDict, SyncList, SyncSet


class SyncModelSupportedMeta(ABCMeta):
    def __subclasscheck__(self, subclass: type[Any]) -> bool:
        if not isclass(subclass):
            return False
        is_model = any(base in (BaseModel, RootModel) for base in subclass.__mro__)
        is_dc = dc.is_dataclass(subclass)
        return is_model or is_dc

    def __instancecheck__(self, instance: Any) -> bool:
        return self.__subclasscheck__(type(instance))

    def register(self, subclass: type[Any]) -> type[Any]:
        raise TypeError("SyncModelSupported does not support class registration.")


@final
class SyncModelSupported(metaclass=SyncModelSupportedMeta):
    def __init_subclass__(cls: type[SyncModelSupported], /, **kwargs: Any) -> None:
        raise TypeError("SyncModelSupported cannot be subclassed.")

    @abstractmethod
    def __syncwave_abc_marker__(self) -> None:
        raise NotImplementedError


T = TypeVar("T", bound=SyncModelSupported)
T_BM = TypeVar("T_BM", bound=BaseModel)
T_RM = TypeVar("T_RM", bound=RootModel)
T_DC = TypeVar("T_DC")  # dataclass


class SyncModel(Generic[T], Reactive):
    __type_adapter: TypeAdapter[SyncModel[T]]
    __children_type_adapter: dict[str, TypeAdapter[Any]]
    __syncwave_on_create__: Any  # Callable[[], None] | None = None

    def __syncwave_init__(self) -> None:
        # TODO: Implement this for real
        self.__type_adapter = TypeAdapter(...)
        self.__children_type_adapter = {}


def reactive(cls: type[T], /, *, cls_name: str | None = None) -> type[SyncModel[T]]:
    if not isclass(cls):
        raise TypeError(f"{cls} is not a valid type.")

    is_base_model = any(base is BaseModel for base in cls.__mro__)
    is_root_model = any(base is RootModel for base in cls.__mro__)
    is_dataclass = dc.is_dataclass(cls)

    cls_name = cls_name or f"Sync{cls.__name__}"
    if is_base_model:
        return patch_base_model(cls, cls_name)
    elif is_root_model:
        return patch_root_model(cls, cls_name)
    elif is_dataclass:
        return patch_dataclass(cls, cls_name)
    else:
        raise TypeError(f"Class '{cls.__name__}' is not a valid SyncModelSupported.")


def patch_base_model(cls: type[T_BM], cls_name: str) -> type[SyncModel[T_BM]]:
    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    children_type_adapter = {}
    for name, field in cls.model_fields.items():
        if drill_field(name, field.annotation, reactive_allowed=True):
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            children_type_adapter[name] = TypeAdapter(field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def __syncwave_update__(
        self: SyncModel[T_BM],
        new: SyncModel[T_BM] | T_BM | Mapping[str, Any],
    ) -> None:
        new = self.__type_adapter.validate_python(new)

        for name in cls.model_fields:
            new_value = getattr(new, name, None)

            if name not in children_type_adapter:
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
                original_setattr(self, name, new_value)

    @atomic
    def __setattr__(self: SyncModel[T_BM], name: str, new_value: Any) -> None:
        old_value = getattr(self, name, None)

        if name not in children_type_adapter:
            original_setattr(self, name, new_value)
            self.__syncwave_on_change__()
            return

        new_value = children_type_adapter[name].validate_python(new_value)
        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))
        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, name, old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            original_setattr(self, name, new_value)
        self.__syncwave_on_change__()

    @atomic
    def __delattr__(self: SyncModel[T_BM], name: str) -> None:
        old_value = getattr(self, name, None)

        if name in children_type_adapter:
            old_is_reactive = isinstance(old_value, Reactive)
            if old_is_reactive:
                old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_on_change__()

    def __syncwave_abc_marker__(self: SyncModel[T_BM]) -> None:
        pass

    new_cls_dict = {
        "__syncwave_update__": __syncwave_update__,
        "__setattr__": __setattr__,
        "__delattr__": __delattr__,
        "__syncwave_abc_marker__": __syncwave_abc_marker__,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)
    return new_cls


def patch_root_model(cls: type[T_RM], cls_name: str) -> type[SyncModel[T_RM]]:
    if cls.model_config.get("frozen"):
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    root_type_adapter: TypeAdapter | None = None
    root_field = cls.model_fields["root"]
    if drill_field("root", root_field.annotation, reactive_allowed=True):
        if root_field.frozen:
            raise TypeError("Field 'root': frozen fields cannot be reactive.")
        root_type_adapter = TypeAdapter(root_field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def __syncwave_update__(self: SyncModel[T_RM], new: Any) -> None:
        new = self.__type_adapter.validate_python(new)
        new_value = new.root

        if root_type_adapter is None:
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
            original_setattr(self, "root", new_value)

    @atomic
    def __setattr__(self: SyncModel[T_RM], name: str, new_value: Any) -> None:
        if name != "root" or root_type_adapter is None:
            original_setattr(self, name, new_value)
            self.__syncwave_on_change__()
            return

        old_value = self.root
        new_value = root_type_adapter.validate_python(new_value)

        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))
        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, "root", old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            original_setattr(self, "root", new_value)
        self.__syncwave_on_change__()

    @atomic
    def __delattr__(self: SyncModel[T_RM], name: str) -> None:
        if name != "root" or root_type_adapter is None:
            original_delattr(self, name)
            self.__syncwave_on_change__()
            return

        old_value = self.root
        old_is_reactive = isinstance(old_value, Reactive)
        if old_is_reactive:
            old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_on_change__()

    def __syncwave_abc_marker__(self: SyncModel[T_RM]) -> None:
        pass

    new_cls_dict = {
        "__syncwave_update__": __syncwave_update__,
        "__setattr__": __setattr__,
        "__delattr__": __delattr__,
        "__syncwave_abc_marker__": __syncwave_abc_marker__,
        "__module__": cls.__module__,
    }

    new_cls = create_model(cls_name, __base__=(cls, SyncModel), **new_cls_dict)
    return new_cls


def patch_dataclass(cls: type[T_DC], cls_name: str) -> type[SyncModel[T_DC]]:
    if cls.__dataclass_params__.frozen:
        raise ValueError(f"'{cls.__name__}' is frozen and cannot be made reactive.")

    if not pdc.is_pydantic_dataclass(cls):
        cls = pdc.dataclass(cls)

    children_type_adapter = {}
    for name, field in cls.__pydantic_fields__.items():
        if drill_field(name, field.annotation, reactive_allowed=True):
            if field.frozen:
                raise TypeError(f"Field '{name}': frozen fields cannot be reactive.")
            children_type_adapter[name] = TypeAdapter(field.annotation)

    original_setattr = cls.__setattr__
    original_delattr = cls.__delattr__

    def __syncwave_update__(
        self: SyncModel[T_DC],
        new: SyncModel[T_DC] | T_DC | Mapping[str, Any],
    ) -> None:
        new = self.__type_adapter.validate_python(new)

        for name in cls.__pydantic_fields__:
            new_value = getattr(new, name, None)

            if name not in children_type_adapter:
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
                original_setattr(self, name, new_value)

    @atomic
    def __setattr__(self: SyncModel[T_DC], name: str, new_value: Any) -> None:
        old_value = getattr(self, name, None)

        if name not in children_type_adapter:
            original_setattr(self, name, new_value)
            self.__syncwave_on_change__()
            return

        new_value = children_type_adapter[name].validate_python(new_value)
        old_is_reactive = isinstance(old_value, Reactive)
        safe_to_update = old_is_reactive and isinstance(new_value, type(old_value))
        if safe_to_update:
            old_value.__syncwave_update__(new_value)
            original_setattr(self, name, old_value)
        else:
            if old_is_reactive:
                old_value.__syncwave_live__ = False
            original_setattr(self, name, new_value)
        self.__syncwave_on_change__()

    @atomic
    def __delattr__(self: SyncModel[T_DC], name: str) -> None:
        old_value = getattr(self, name, None)

        if name in children_type_adapter:
            old_is_reactive = isinstance(old_value, Reactive)
            if old_is_reactive:
                old_value.__syncwave_live__ = False
        original_delattr(self, name)
        self.__syncwave_on_change__()

    def __syncwave_abc_marker__(self: SyncModel[T_DC]) -> None:
        pass

    new_cls_dict = {
        "__syncwave_update__": __syncwave_update__,
        "__setattr__": __setattr__,
        "__delattr__": __delattr__,
        "__syncwave_abc_marker__": __syncwave_abc_marker__,
        "__module__": cls.__module__,
    }

    new_cls = type(cls_name, (cls, SyncModel), new_cls_dict)

    return pdc.dataclass(new_cls)


def drill_field(name: str, tp: Any, reactive_allowed: bool) -> TypeGuard[Reactive]:
    # Drills down a type on a model field into its components.
    # Returns True if the field is a Reactive type, False otherwise.
    # Raises an error if a SyncModel is present at any level,
    # or if a Reactive type is nested in a non-reactive container.
    origin = get_origin(tp) or tp
    args = get_args(tp)
    len_args = len(args)

    if isclass(origin):
        if issubclass(origin, SyncModel):
            raise TypeError(f"Field '{name}': SyncModel types cannot be nested.")

        if issubclass(origin, SyncCollection):
            # if not SyncModel, the only Reactive types left are SyncCollection types
            if not reactive_allowed:
                raise TypeError(f"Field '{name}': Cannot break the reactivity chain.")

            if issubclass(origin, SyncDict):
                if len_args == 2:
                    drill_field(name, args[1], reactive_allowed=True)
                elif len_args != 0:
                    raise TypeError(f"Field '{name}': 0 or 2 arguments allowed.")
            elif issubclass(origin, (SyncList, SyncSet)):
                if len_args == 1:
                    drill_field(name, args[0], reactive_allowed=True)
                elif len_args != 0:
                    raise TypeError(f"Field '{name}': 0 or 1 arguments allowed.")
            return True

    if origin is Annotated:
        if len_args == 0:
            raise ValueError(f"Field '{name}': Annotated must have arguments.")
        return drill_field(name, args[0], reactive_allowed)

    if origin is Union or str(origin) == "typing.Union":
        if len_args == 0:
            raise ValueError(f"Field '{name}': Union must have arguments.")
        results = [drill_field(name, tp, reactive_allowed) for tp in args]
        return any(results)

    for arg in args:
        drill_field(name, arg, reactive_allowed=False)

    return False
