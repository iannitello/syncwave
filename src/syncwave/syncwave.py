from __future__ import annotations

from collections.abc import Callable as F, Iterator, MutableMapping
from dataclasses import dataclass, is_dataclass
from functools import partial
from inspect import isclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeVar, cast
from typing_extensions import Never, TypeForm

from pydantic import BaseModel, PydanticSchemaGenerationError, TypeAdapter
from pydantic.dataclasses import is_pydantic_dataclass

from .errors import unreachable
from .io import io, void
from .ownership import detach, ingest
from .reactive import (
    Context,
    Reactive,
    StoreRef,
    SyncState,
    UnionCtx,
    is_reactive,
    union_key,
)
from .sync_collection import SyncDict, SyncList
from .sync_model import SyncModel, is_sync_dataclass, undecorated_guard
from .tp_validation import collection_wrap, drill_tp, str_guard, validate_default
from .watcher import watcher

__all__ = ["Syncwave"]


if TYPE_CHECKING:
    from _typeshed import DataclassInstance as Dataclass

    from .sync_model import SyncModelLike

    # A stdlib `Dataclass` isn't actually accepted at runtime, but type checkers think
    # `sync_dataclass` returns a normal `Dataclass` because of `dataclass_transform`.
    SyncModelLike_T = TypeVar("SyncModelLike_T", bound=SyncModelLike | Dataclass)


@dataclass(frozen=True)
class StoreInfo:
    name: str
    path: Path
    type_adapter: TypeAdapter
    sref: StoreRef
    ctx: Context | UnionCtx | None


T = TypeVar("T")


# Has to be thread-safe, this is a temporary solution just to start the implementation.
class Syncwave(MutableMapping[str, Any], Reactive, _syncwave=True):
    """The main entry point to Syncwave.

    Start by creating an instance to interact with the stores.

    Example:
    ```python
    from syncwave import Syncwave

    syncwave = Syncwave()
    ```

    ---

    Abstract: Usage Documentation
        [Syncwave](https://syncwave.dev/usage/syncwave/)

    """

    __syncwave_state__ = SyncState.LIVE

    def __init__(self, root_path: str | Path = "syncstores") -> None:
        """Create a `Syncwave` instance.

        Args:
            root_path: Directory holding the stores' JSON files. Created if it doesn't
                exist.

        """
        root = io.sanitize_path(root_path)
        io.create_dir(root)
        self.__root_path = root
        self.__stores: dict[str, tuple[Any, StoreInfo]] = {}

    @property
    def root_path(self) -> Path:
        """The absolute path to the directory holding the stores' JSON files.

        ---

        Abstract: Usage Documentation
            [The `syncwave` Instance](https://syncwave.dev/usage/syncwave/#the-syncwave-instance)

        """
        return self.__root_path

    def __getitem__(self, key: str) -> Any:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")

        value, store_info = self.__stores[key]
        with store_info.sref.lock:
            if value is void:
                unreachable()
            return detach(value, store_info.type_adapter)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self.__stores:
            raise KeyError(
                f"Store '{key}' does not exist. "
                "Use `syncwave.create_store(...)`, or `@syncwave.store(...)` first."
            )

        _, store_info = self.__stores[key]
        with store_info.sref.lock:
            self.__set_store(key, value)

    def __delitem__(self, key: str) -> None:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")

        value, store_info = self.__stores[key]
        watcher.unwatch(store_info.path)
        with store_info.sref.lock:
            if is_reactive(value):
                value.__syncwave_kill__()
        del self.__stores[key]
        io.remove_file(store_info.path)

    def __iter__(self) -> Iterator[str]:
        # first convert to a list so the iterator is over a frozen object
        return iter(list(self.__stores.keys()))

    def __len__(self) -> int:
        return len(self.__stores)

    def __str__(self) -> str:
        items = ", ".join(f"{k!r}: {v[0]}" for k, v in self.__stores.items())
        return "{" + items + "}"

    def __repr__(self) -> str:
        items = {k: v[0] for k, v in self.__stores.items()}
        return f"<Syncwave {items!r}>"

    def __copy__(self) -> NoReturn:
        raise TypeError("A `Syncwave` instance cannot be copied.")

    def __deepcopy__(self, memo: dict[int, Any]) -> NoReturn:
        raise TypeError("A `Syncwave` instance cannot be copied.")

    def __eq__(self, other: Any, /) -> bool:
        return True if other is self else NotImplemented

    __hash__ = None

    def create_store(self, tp: TypeForm[T], /, *, name: str, default: Any = void) -> T:
        """Create a store, persisted to `<root_path>/<name>.json` and two-way synced.

        Every change to the store, from Python or from the file, is validated against
        `tp`.

        Example:
        ```python
        from syncwave import SyncSet, Syncwave

        syncwave = Syncwave()

        tags = syncwave.create_store(SyncSet[str], name="tags")
        tags.add("python")
        ```

        ---

        Abstract: Usage Documentation
            [Create a Store](https://syncwave.dev/usage/syncwave/#create-a-store)

        Args:
            tp: Type of the store, e.g. `list[int]`, `SyncSet[str]`, or a `SyncModel`.
            name: Name of the store, used as its key and as its file name.
            default: Initial value, used if the file is missing or empty. Required when
                no empty value (`{}`, `[]`, `""`, or `None`) fits `tp`.

        Returns:
            The store's value: the live object for a reactive type, a copy otherwise.

        """
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")
        str_guard("name", name)
        io.file_name_guard(name)

        self.__create_store(tp, name, default)
        value, store_info = self.__stores[name]
        return detach(value, store_info.type_adapter)

    def store(
        self,
        *,
        name: str,
        collection: type[SyncDict[Any, Never] | SyncList[Never]]
        | Literal["auto"]
        | None = "auto",
        default: Any = void,
    ) -> F[[type[SyncModelLike_T]], type[SyncModelLike_T]]:
        """Class decorator creating a store from a reactive model.

        Like `create_store`, but the model is wrapped in a collection first (see
        `collection`). The class is returned unchanged.

        Example:
        ```python
        from syncwave import SyncModel, Syncwave

        syncwave = Syncwave()


        @syncwave.store(name="customers")
        class Customer(SyncModel):
            name: str
            age: int


        customers = syncwave["customers"]  # a SyncList[Customer]
        customers.append(Customer(name="Alice", age=30))
        ```

        ---

        Abstract: Usage Documentation
            [The `store` Decorator](https://syncwave.dev/usage/models/#the-store-decorator)

        Args:
            name: Name of the store, used as its key and as its file name.
            collection: `SyncList`, `SyncDict` (`str` keys) or `SyncDict[<key type>]`,
                or `None` for no wrapping. `"auto"` picks `None` for a `SyncRoot`, a
                `SyncDict` if the model has a `key` field, and `SyncList` otherwise.
            default: Initial value, used if the file is missing or empty. Pass plain
                data (e.g. a `dict` or a `list`), not an instance.

        Returns:
            A decorator that creates the store and returns the class unchanged.

        """
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")
        str_guard("name", name)
        io.file_name_guard(name)

        def decorator(cls: type[SyncModelLike_T]) -> type[SyncModelLike_T]:
            if not isclass(cls):
                raise TypeError(f"Expected a class, got `{type(cls).__qualname__}`.")

            if issubclass(cls, SyncModel) or is_sync_dataclass(cls):
                store_tp = collection_wrap(cls, collection)
                self.__create_store(store_tp, name, default)
                return cls

            err = "Use %s instead of a %s."
            if issubclass(cls, BaseModel):
                raise TypeError(err % ("a `SyncModel`", "`BaseModel`"))
            if is_pydantic_dataclass(cls):
                raise TypeError(err % ("`@sync_dataclass`", "Pydantic dataclass"))
            if is_dataclass(cls):
                undecorated_guard(cls)
                raise TypeError(err % ("`@sync_dataclass`", "standard dataclass"))
            raise TypeError(
                "Expected a `SyncModel` or a reactive dataclass, "
                f"but got `{cls.__qualname__}` instead."
            )

        return decorator

    def __create_store(self, tp: TypeForm[Any], name: str, default: Any) -> None:
        try:
            type_adapter = TypeAdapter(tp)
        except PydanticSchemaGenerationError as e:
            raise TypeError(f"Type `{tp}` is not supported by Pydantic.") from e

        ctx = drill_tp(tp)

        # default is checked whether it will be used or not
        if default is not void:
            default = validate_default(default, type_adapter, tp)

        path = self.__root_path / f"{name}.json"
        value = io.init_json(path, type_adapter, default)

        if value is void:
            raise ValueError(f"Unable to create store '{name}' without a default.")
        value = cast(Any, value)  # removes the EmptyFileType for type checking

        sref = StoreRef(lock=RLock(), on_change=partial(self.__on_store_change, name))
        store_info = StoreInfo(name, path, type_adapter, sref, ctx)

        if is_reactive(value):
            if ctx is None:
                unreachable()
            elif isinstance(ctx, Context):
                value.__syncwave_init__(sref, ctx)
            elif isinstance(ctx, UnionCtx):
                value.__syncwave_init__(sref, ctx[union_key(value)])
            else:
                unreachable()
        self.__stores[name] = (value, store_info)
        watcher.watch(path, self.__on_file_change, store_info)

    def __on_store_change(self, name: str) -> None:
        # always called from within the store lock context
        value, store_info = self.__stores[name]
        io.dump(store_info.path, value, store_info.type_adapter)

    def __on_file_change(self, store_info: StoreInfo) -> None:
        try:
            new_value = io.load(store_info.path, store_info.type_adapter)
        except (FileNotFoundError, ValueError):
            with store_info.sref.lock:
                old_value = self.__stores[store_info.name][0]
                io.dump(store_info.path, old_value, store_info.type_adapter)
                return

        if store_info.name not in self.__stores:
            return  # store was deleted, ignore this event
        with store_info.sref.lock:
            self.__set_store(store_info.name, new_value, from_json=True)

    def __set_store(self, key: str, value: Any, *, from_json: bool = False) -> None:
        # always called from within the store lock context
        old_value, store_info = self.__stores[key]
        # `io.load` already validated the JSON value with this adapter, and we own it.
        new_value = value if from_json else ingest(value, store_info.type_adapter)
        ctx, sref = store_info.ctx, store_info.sref

        # case 1: non-reactive content type
        if ctx is None:
            self.__stores[key] = (new_value, store_info)
        # case 2: fixed reactive content type
        elif isinstance(ctx, Context):
            old_value.__syncwave_update__(new_value)
        # case 3: union content type
        elif isinstance(ctx, UnionCtx):
            old_is_reactive = is_reactive(old_value)
            new_is_reactive = is_reactive(new_value)

            if (
                old_is_reactive
                and new_is_reactive
                and union_key(old_value) == union_key(new_value)
            ):
                old_value.__syncwave_update__(new_value)
            else:
                if old_is_reactive:
                    old_value.__syncwave_kill__()
                if new_is_reactive:
                    new_value.__syncwave_init__(sref, ctx[union_key(new_value)])
                self.__stores[key] = (new_value, store_info)
        else:
            unreachable()

        sref.on_change()
