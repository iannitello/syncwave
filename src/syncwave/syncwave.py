from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from functools import partial, wraps
from inspect import isclass
from pathlib import Path
from threading import RLock
from typing import (
    Annotated,
    Any,
    Callable,
    TypeVar,
    Union,
    get_args,
    get_origin,
    overload,
)
from typing_extensions import ParamSpec

from pydantic import PydanticSchemaGenerationError, TypeAdapter

from .io import EmptyFile, EmptyFileType, io
from .reactive import Context, ContextMap, Reactive, StoreRef
from .sync_collection import (
    KT,
    VT,
    SyncDict,
    SyncDictCtx,
    SyncList,
    SyncListCtx,
    SyncSet,
    SyncSetCtx,
)
from .sync_model import (
    SyncModel,
    SyncModelCtx,
    SyncModelSupported,
    T,
    create_sync_model,
)
from .watcher import watcher


@dataclass(frozen=True)
class Metadata:
    name: str
    path: Path
    type_adapter: TypeAdapter
    sref: StoreRef
    ctx: Context | ContextMap | None


P = ParamSpec("P")
R = TypeVar("R")


def global_lock(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(self: Syncwave, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.__syncwave_lock__:
            return func(self, *args, **kwargs)

    return wrapper


# Has to be thread-safe, this is a temporary solution just to start the implementation.
class Syncwave(MutableMapping[str, Any]):
    def __init__(self, stores_dir: str | Path = "") -> None:
        stores_dir = (
            io.get_root_dir() / "syncstores"
            if stores_dir == ""
            else io.sanitize_path(stores_dir)
        )
        io.create_dir(stores_dir)
        self.__syncwave_lock__ = RLock()
        self.__stores_dir = stores_dir
        self.__stores: dict[str, tuple[Any | EmptyFileType, Metadata]] = {}

    @global_lock
    def __getitem__(self, key: str) -> Any:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")
        if (value := self.__stores[key][0]) is EmptyFile:
            raise ValueError(f"Store '{key}' has not been initialized.")
        return value

    @global_lock
    def __setitem__(self, key: str, value: Any) -> None:
        if not isinstance(key, str):
            raise TypeError(f"Store key must be a string, got `{type(key).__name__}`.")
        if key not in self.__stores:
            raise KeyError(
                f"Store '{key}' does not exist. "
                "Use `syncwave.store()` to create one first."
            )

        self.__set_store(key, value)

    @global_lock
    def __delitem__(self, key: str) -> None:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")

        value, metadata = self.__stores[key]
        watcher.unwatch(metadata.path)
        with metadata.sref.lock:
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        del self.__stores[key]
        io.remove_file(metadata.path)

    @global_lock
    def __iter__(self) -> Iterator[str]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__stores.keys()))

    @global_lock
    def __len__(self) -> int:
        return len(self.__stores)

    @property
    def stores_dir(self) -> Path:
        return self.__stores_dir

    def __str__(self) -> str:
        items = ", ".join(f"{k!r}: {v[0]}" for k, v in self.__stores.items())
        return "{" + items + "}"

    def __repr__(self) -> str:
        return f"<Syncwave stores={list(self.__stores.keys())!r}>"

    def reactive(self, cls: type[T], cls_name: str | None = None) -> type[SyncModel[T]]:
        if not isclass(cls):
            raise TypeError(f"Expected a class, got `{type(cls).__name__}`.")
        if not issubclass(cls, SyncModelSupported):
            raise TypeError(f"Expected a SyncModelSupported, got `{cls.__name__}`.")
        drill_model(cls, as_sync_model=True)
        return create_sync_model(cls, rename=cls_name or True)

    @overload
    def register(
        self,
        *,
        name: str | None = None,
        path: Path | None = None,
    ) -> Callable[[type[T]], type[SyncModel[T]]]: ...

    @global_lock
    def register(self, _cls=None, /, *, name=None, path=None):
        def decorator(cls: type[T]) -> type[SyncModel[T]]:
            sync_cls = create_sync_model(cls, rename=False)
            self.store(SyncDict[str, sync_cls], name=name or cls.__name__, path=path)
            return sync_cls

        # Case 1: called with parentheses, e.g. `@syncwave.register(**kwargs)`
        if _cls is None:
            return decorator

        # Case 2: called without parentheses, e.g. `@syncwave.register`
        return decorator(_cls)

    @global_lock
    def store(self, tp: type, /, *, name: str, path: str | Path | None = None) -> None:
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")

        if path is None:
            path = self.__stores_dir / f"{name.lower()}.json"
        path = io.sanitize_path(path)

        if path in (store[1].path for store in self.__stores.values()):
            raise ValueError(f"Path '{path}' is already in use by another store.")

        try:
            type_adapter = TypeAdapter(tp)
        except PydanticSchemaGenerationError as e:
            raise TypeError(f"Type `{tp}` is not supported by Pydantic.") from e

        sref = StoreRef(lock=RLock(), on_change=partial(self.__on_store_change, name))
        ctx = drill_tp(tp)
        metadata = Metadata(name, path, type_adapter, sref, ctx)

        value = io.init_json(path, type_adapter)
        if isinstance(value, Reactive):
            value.__syncwave_init__(sref, ctx)
        self.__stores[name] = (value, metadata)
        watcher.watch(path, self.__on_file_change, metadata)

    @global_lock
    def __on_store_change(self, name: str) -> None:
        value, metadata = self.__stores[name]
        io.write_json(metadata.path, value, metadata.type_adapter)

    def __on_file_change(self, metadata: Metadata) -> None:
        try:
            new_value = io.read_json(metadata.path, metadata.type_adapter)
        except (FileNotFoundError, ValueError):
            with self.__syncwave_lock__:
                old_value = self.__stores[metadata.name][0]
            io.write_json(metadata.path, old_value, metadata.type_adapter)
            return

        with self.__syncwave_lock__:
            if metadata.name not in self.__stores:
                return  # Store was deleted, ignore this event
            self.__set_store(metadata.name, new_value)

    def __set_store(self, key: str, value: Any) -> None:
        # always called from within the global lock context
        old_value, metadata = self.__stores[key]
        new_value = metadata.type_adapter.validate_python(value)
        ctx, sref = metadata.ctx, metadata.sref

        with sref.lock:
            # case 1: non-reactive content type
            if ctx is None:
                self.__stores[key] = (new_value, metadata)
            # case 2: fixed reactive content type
            elif isinstance(ctx, Context):
                if old_value is not EmptyFile:
                    old_value.__syncwave_update__(new_value)
                else:
                    new_value.__syncwave_init__(sref, ctx)
                    self.__stores[key] = (new_value, metadata)
            # case 3: union content type
            elif isinstance(ctx, ContextMap):
                old_is_reactive = isinstance(old_value, Reactive)
                new_is_reactive = isinstance(new_value, Reactive)
                same_type = type(old_value) is (new_type := type(new_value))

                if old_is_reactive and new_is_reactive and same_type:
                    old_value.__syncwave_update__(new_value)
                else:
                    if old_is_reactive:
                        old_value.__syncwave_kill__()
                    if new_is_reactive:
                        if new_type not in ctx:
                            raise TypeError("Internal error: context missing for type.")
                        new_value.__syncwave_init__(sref, ctx[new_type])
                    self.__stores[key] = (new_value, metadata)
            else:
                raise TypeError("Internal error: unexpected context type.")

        sref.on_change()


def drill_tp(tp: Any, _err_if_reactive: str = "") -> Context | ContextMap | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)
    name = getattr(origin, "__name__", repr(origin))

    if origin is Annotated:
        if len(args) < 1:
            raise TypeError("`Annotated` must have at least one type argument.")
        return drill_tp(args[0], _err_if_reactive)

    if origin is Union or str(origin) == "typing.Union":
        if not args:
            raise TypeError("`Union` must have at least one type argument.")
        ctxs = [drill_tp(arg, _err_if_reactive) for arg in args]
        ctx_map = {ctx.tp: ctx for ctx in ctxs if ctx is not None}
        return ContextMap(ctx_map) if ctx_map else None

    if isclass(origin):
        if issubclass(origin, Reactive):
            if _err_if_reactive:
                raise TypeError(f"`{name}` cannot be used here: {_err_if_reactive}")
            if issubclass(origin, SyncDict):
                return _get_sync_dict_ctx(tp)
            if issubclass(origin, SyncList):
                return _get_sync_list_ctx(tp)
            if issubclass(origin, SyncSet):
                return _get_sync_set_ctx(tp)
            if issubclass(origin, SyncModel):
                return drill_model(origin)
            raise TypeError(f"`{name}` is not a recognized reactive type.")
        if issubclass(origin, SyncModelSupported):
            return drill_model(origin)

    err = f"`{name}` is not a reactive container."
    for arg in args:
        drill_tp(arg, _err_if_reactive=err)

    return None


def drill_model(cls: type, as_sync_model: bool = False) -> SyncModelCtx | None:
    is_sync_model = issubclass(cls, SyncModel)
    treat_as_sync_model = is_sync_model or as_sync_model

    model_is_frozen: bool = (
        getattr(cls, "model_config", {}).get("frozen", False)
        or getattr(cls, "__pydantic_config__", {}).get("frozen", False)
        or getattr(getattr(cls, "__dataclass_params__", None), "frozen", False)
    )
    if model_is_frozen and treat_as_sync_model:
        raise TypeError(f"`{cls.__name__}` is frozen and cannot be made reactive.")

    fields = getattr(cls, "__pydantic_fields__", None) or cls.__dataclass_fields__
    if not fields:
        raise TypeError(f"`{cls.__name__}` has no fields.")

    fields_ctx: dict[str, Context | ContextMap] = {}
    fields_type_adapter: dict[str, TypeAdapter[Any]] = {}

    for name, field in fields.items():
        field_tp = getattr(field, "annotation", None) or field.type
        field_is_frozen: bool = getattr(field, "frozen", False)

        if not treat_as_sync_model:
            err = f"`{cls.__name__}` is not a `SyncModel` (for field `{name}`)."
        elif field_is_frozen:
            err = f"Field `{name}` in `{cls.__name__}` is frozen."
        else:
            err = ""

        field_ctx = drill_tp(field_tp, _err_if_reactive=err)
        if field_ctx is not None:
            fields_ctx[name] = field_ctx
        fields_type_adapter[name] = TypeAdapter(field_tp)

    if is_sync_model:
        return SyncModelCtx(
            tp=cls,
            type_adapter=TypeAdapter(cls),
            fields_ctx=fields_ctx,
            fields_type_adapter=fields_type_adapter,
        )

    return None


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)

    if len(args) == 2:
        inner_ctx = drill_tp(args[1])
        inner_type_adapter = TypeAdapter(args[1])
    elif len(args) == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncDict` requires 0 or 2 type arguments.")

    return SyncDictCtx(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_ctx(tp: type[SyncList[VT]]) -> SyncListCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        inner_ctx = drill_tp(args[0])
        inner_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncList` requires 0 or 1 type argument.")

    return SyncListCtx(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)

    if len(args) == 1:
        drill_tp(args[0], _err_if_reactive="`SyncSet` cannot hold reactive items.")
        inner_type_adapter = TypeAdapter(args[0])
    elif len(args) == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("`SyncSet` requires 0 or 1 type argument.")

    return SyncSetCtx(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )
