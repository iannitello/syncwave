from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from functools import partial, wraps
from keyword import iskeyword
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Literal, TypeVar
from typing_extensions import ParamSpec
from weakref import WeakSet

from pydantic import PydanticSchemaGenerationError, TypeAdapter

from .io import EmptyFile, EmptyFileType, io
from .reactive import Context, ContextMap, Reactive, StoreRef, assert_never
from .sync_collection import SyncDict, SyncList
from .sync_model import SyncModel, create_sync_model
from .tp_validation import collection_wrap, drill_tp, str_guard, sync_model_guard
from .watcher import watcher

if TYPE_CHECKING:
    from .sync_model import _SMS


@dataclass(frozen=True)
class StoreInfo:
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
            return func(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    return wrapper  # ty: ignore[invalid-return-type]


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
        self.__stores: dict[str, tuple[Any | EmptyFileType, StoreInfo]] = {}
        self.__models: WeakSet[type[_SMS]] = WeakSet()

    @property
    def stores_dir(self) -> Path:
        return self.__stores_dir

    @global_lock
    def __getitem__(self, key: str) -> Any:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")
        if (value := self.__stores[key][0]) is EmptyFile:
            raise ValueError(f"Store '{key}' has not been initialized.")
        return value

    @global_lock
    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self.__stores:
            raise KeyError(
                f"Store '{key}' does not exist. "
                "Use `syncwave.create_store(...)`, or `@syncwave.store(...)` first."
            )
        str_guard("key", key)
        self.__set_store(key, value)

    @global_lock
    def __delitem__(self, key: str) -> None:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")

        value, store_info = self.__stores[key]
        watcher.unwatch(store_info.path)
        with store_info.sref.lock:
            if isinstance(value, Reactive):
                value.__syncwave_kill__()
        del self.__stores[key]
        io.remove_file(store_info.path)

    @global_lock
    def __iter__(self) -> Iterator[str]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__stores.keys()))

    @global_lock
    def __len__(self) -> int:
        return len(self.__stores)

    def __str__(self) -> str:
        items = ", ".join(f"{k!r}: {v[0]}" for k, v in self.__stores.items())
        return "{" + items + "}"

    def __repr__(self) -> str:
        items = {k: v[0] for k, v in self.__stores.items()}
        return f"<Syncwave {items!r}>"

    @global_lock
    def make_reactive(
        self,
        cls: type[_SMS],
        /,
        *,
        cls_name: str | None = None,
    ) -> type[SyncModel]:
        sync_model_guard(cls, self.__models)

        if cls_name is not None:
            str_guard("cls_name", cls_name)
            if not cls_name.isidentifier() or iskeyword(cls_name):
                raise ValueError(f"'{cls_name}' is not a valid class name.")

        sync_model = create_sync_model(cls, rename=cls_name or True)
        self.__models.add(cls)
        return sync_model

    @global_lock
    def register(
        self,
        *,
        name: str,
        collection: type[SyncDict] | type[SyncList] | Literal["auto"] | None = "auto",
    ) -> Callable[[type[_SMS]], type[_SMS]]:
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")

        str_guard("name", name)
        io.file_name_guard(name)

        def decorator(cls: type[_SMS]) -> type[_SMS]:
            sync_model_guard(cls, self.__models)
            sync_model = create_sync_model(cls)
            store_tp = collection_wrap(cls, sync_model, collection)
            self.__create_store(store_tp, name=name)
            self.__models.add(cls)
            return cls

        return decorator

    @global_lock
    def create_store(self, tp: type, /, *, name: str, default: Any = EmptyFile) -> Any:
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")

        str_guard("name", name)
        io.file_name_guard(name)
        self.__create_store(tp, name)

        if (value := self.__stores[name][0]) is EmptyFile:
            if default is not EmptyFile:
                self.__set_store(name, default)
                return default
            raise ValueError(f"Unable to create store '{name}' without default value.")
        return value

    def __create_store(self, tp: type, name: str) -> None:
        try:
            type_adapter = TypeAdapter(tp)
        except PydanticSchemaGenerationError as e:
            raise TypeError(f"Type `{tp}` is not supported by Pydantic.") from e

        path = self.__stores_dir / f"{name}.json"
        sref = StoreRef(lock=RLock(), on_change=partial(self.__on_store_change, name))
        ctx = drill_tp(tp)
        store_info = StoreInfo(name, path, type_adapter, sref, ctx)

        value = io.init_json(path, type_adapter)
        if isinstance(value, Reactive):
            if ctx is None:
                assert_never()
            elif isinstance(ctx, Context):
                value.__syncwave_init__(sref, ctx)
            elif isinstance(ctx, ContextMap):
                value.__syncwave_init__(sref, ctx[type(value)])
            else:
                assert_never()
        self.__stores[name] = (value, store_info)

        watcher.watch(path, self.__on_file_change, store_info)

    @global_lock
    def __on_store_change(self, name: str) -> None:
        value, store_info = self.__stores[name]
        io.write_json(store_info.path, value, store_info.type_adapter)

    def __on_file_change(self, store_info: StoreInfo) -> None:
        try:
            new_value = io.read_json(store_info.path, store_info.type_adapter)
        except (FileNotFoundError, ValueError):
            with self.__syncwave_lock__:
                old_value = self.__stores[store_info.name][0]
            io.write_json(store_info.path, old_value, store_info.type_adapter)
            return

        with self.__syncwave_lock__:
            if store_info.name not in self.__stores:
                return  # Store was deleted, ignore this event
            self.__set_store(store_info.name, new_value)

    def __set_store(self, key: str, value: Any) -> None:
        # always called from within the global lock context
        old_value, store_info = self.__stores[key]
        new_value = store_info.type_adapter.validate_python(value)
        ctx, sref = store_info.ctx, store_info.sref

        with sref.lock:
            # case 1: non-reactive content type
            if ctx is None:
                self.__stores[key] = (new_value, store_info)
            # case 2: fixed reactive content type
            elif isinstance(ctx, Context):
                if not isinstance(old_value, EmptyFileType):
                    old_value.__syncwave_update__(new_value)
                else:
                    new_value.__syncwave_init__(sref, ctx)
                    self.__stores[key] = (new_value, store_info)
            # case 3: union content type
            elif isinstance(ctx, ContextMap):
                old_is_reactive = isinstance(old_value, Reactive)
                new_is_reactive = isinstance(new_value, Reactive)
                same_type = type(old_value) is (new_type := type(new_value))

                if old_is_reactive and new_is_reactive and same_type:
                    # `old_is_reactive` means `old_value` is not EmptyFile,
                    # but aliased conditional expressions are not supported by ty
                    old_value.__syncwave_update__(new_value)  # ty: ignore[unresolved-attribute]
                else:
                    if old_is_reactive:
                        # same reason as above, `old_value` can't be EmptyFile
                        old_value.__syncwave_kill__()  # ty: ignore[unresolved-attribute]
                    if new_is_reactive:
                        new_value.__syncwave_init__(sref, ctx[new_type])
                    self.__stores[key] = (new_value, store_info)
            else:
                assert_never()

        sref.on_change()
