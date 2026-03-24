from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from functools import partial
from keyword import iskeyword
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Literal
from weakref import WeakSet

from pydantic import PydanticSchemaGenerationError, TypeAdapter

from .io import EmptyFile, EmptyFileType, io
from .reactive import Context, ContextMap, Reactive, StoreRef, unreachable
from .sync_collection import SyncDict, SyncList
from .sync_model import SyncModel, create_sync_model
from .tp_validation import collection_wrap, drill_tp, str_guard, sync_model_guard
from .watcher import watcher

if TYPE_CHECKING:
    from types import GenericAlias

    from .sync_model import SMS

__all__ = ["Syncwave"]


@dataclass(frozen=True)
class StoreInfo:
    name: str
    path: Path
    type_adapter: TypeAdapter
    sref: StoreRef
    ctx: Context | ContextMap | None


# Has to be thread-safe, this is a temporary solution just to start the implementation.
class Syncwave(MutableMapping[str, Any]):
    """The main entry point to Syncwave.

    Start by creating an instance to interact with the stores.

    Example:
    ```python
    from syncwave import Syncwave

    syncwave = Syncwave()
    ```

    ---

    Abstract: Usage Documentation
        [Syncwave](https://placeholder.dev/usage/syncwave/)

    """

    def __init__(self, root_path: str | Path = "syncstores") -> None:
        """Initialize a new `Syncwave` instance.

        Args:
            root_path: Directory holding the JSON files associated with the stores. The
                given value will be normalized (if relative, it will be made absolute
                relative to the current working directory, shell variables will be
                expanded, etc.). The directory will be created if it doesn't already
                exist.

        """
        root = io.sanitize_path(root_path)
        io.create_dir(root)
        self.__root_path = root
        self.__stores: dict[str, tuple[Any | EmptyFileType, StoreInfo]] = {}
        self.__models: WeakSet[type[SMS]] = WeakSet()

    @property
    def root_path(self) -> Path:
        """The normalized path to the directory holding the stores' JSON files."""
        return self.__root_path

    def __getitem__(self, key: str) -> Any:
        if key not in self.__stores:
            raise KeyError(f"Store '{key}' does not exist.")

        value, store_info = self.__stores[key]
        with store_info.sref.lock:
            if value is EmptyFile:
                raise ValueError(f"Store '{key}' has not been initialized.")
            return value

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self.__stores:
            raise KeyError(
                f"Store '{key}' does not exist. "
                "Use `syncwave.create_store(...)`, or `@syncwave.register(...)` first."
            )
        str_guard("key", key)

        _, store_info = self.__stores[key]
        with store_info.sref.lock:
            self.__set_store(key, value)

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

    def make_reactive(
        self,
        cls: type[SMS],
        /,
        *,
        cls_name: str | None = None,
    ) -> type[SyncModel]:
        """Make a new reactive model from a regular model.

        The returned class can be used when defining a store and its instances will be
        reactive. This method offers more flexibility than [Syncwave.register](https://placeholder.dev/api/syncwave/#syncwave.Syncwave.register),
        which is the higher-level alternative.

        The returned class inherits from both SyncModel and the original class `cls`.
        The original class is not mutated by this method.

        Example:
        ```python
        from pydantic import BaseModel
        from syncwave import SyncList, Syncwave

        syncwave = Syncwave()


        class Customer(BaseModel):
            name: str
            age: int


        SyncCustomer = syncwave.make_reactive(Customer)
        customers = syncwave.create_store(SyncList[SyncCustomer], name="customers")
        customers.append(Customer(name="Alice", age=30))
        ```

        ---

        Abstract: Usage Documentation
            [Syncwave](https://placeholder.dev/usage/syncwave/)

        Args:
            cls: Base class for the new reactive model.
            cls_name: Custom name for the new class. Defaults to `"Sync" + cls.__name__`
                if `None` is given.

        Returns:
            The new reactive model class.

        """
        sync_model_guard(cls, self.__models)

        if cls_name is not None:
            str_guard("cls_name", cls_name)
            if not cls_name.isidentifier() or iskeyword(cls_name):
                raise ValueError(f"'{cls_name}' is not a valid class name.")

        sync_model = create_sync_model(cls, rename=cls_name or True)
        self.__models.add(cls)
        return sync_model

    def create_store(self, tp: type, /, *, name: str, default: Any = EmptyFile) -> Any:
        """Create a store persisted to a JSON file and two-way synced with it.

        A store is an item (key/value pair) on a `Syncwave` instance, and each store has
        a corresponding JSON file at `<root_path>/<name>.json` where the value is
        persisted. Changes to the store occurring in Python are reflected in the JSON,
        and changes occurring in the JSON file are reflected in the store.

        Before any changes are made to the store, the value is validated against the
        type `tp`.

        Example:
        ```python
        from syncwave import SyncSet, Syncwave

        syncwave = Syncwave()

        tags = syncwave.create_store(SyncSet[str], name="tags")
        tags.add("python")
        ```

        ---

        Abstract: Usage Documentation
            [Syncwave](https://placeholder.dev/usage/syncwave/)

        Args:
            tp: Type of the store, e.g. `list[int]`, `SyncSet[str]`, `typing.Any`, etc.
            name: Name of the store. This is also the key used to access the store
                (`syncwave[name]`), and the name of the corresponding JSON file
                (`<root_path>/<name>.json`).
            default: Default fallback value if no reasonable default can be inferred for
                the type (and if the file doesn't exist already or is empty). Usually
                only needed if the store is a single value (i.e. not a collection). This
                parameter is ignored if a default can be inferred.

        Returns:
            The current store value, loaded from disk or the initial value.

        """
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")

        str_guard("name", name)
        io.file_name_guard(name)
        self.__create_store(tp, name)

        value, store_info = self.__stores[name]
        with store_info.sref.lock:
            if value is EmptyFile:
                if default is not EmptyFile:
                    self.__set_store(name, default)
                    return self.__stores[name][0]
                # cleanup before raising
                watcher.unwatch(store_info.path)
                del self.__stores[name]
                raise ValueError(f"Unable to create store '{name}' without a default.")
            return value

    def register(
        self,
        *,
        name: str,
        collection: type[SyncDict | SyncList] | Literal["auto"] | None = "auto",
    ) -> Callable[[type[SMS]], type[SMS]]:
        """Register a model as a store with a class decorator.

        This is a convenience method that can be thought of as combining [Syncwave.make_reactive](https://placeholder.dev/api/syncwave/#syncwave.Syncwave.make_reactive)
        and [Syncwave.create_store](https://placeholder.dev/api/syncwave/#syncwave.Syncwave.create_store)
        in one step.

        The decorated class is left unchanged, but Syncwave uses it to create a new
        reactive class that is then wrapped in a collection (controlled by the
        `collection` parameter). The resulting type is used to create a new store.

        Example:
        ```python
        from pydantic import BaseModel
        from syncwave import Syncwave

        syncwave = Syncwave()


        @syncwave.register(name="customers")
        class Customer(BaseModel):
            name: str
            age: int


        customers = syncwave["customers"]
        customers.append(Customer(name="Alice", age=30))


        # equivalent to...

        # from syncwave import SyncList


        # class Customer(BaseModel):
        #     name: str
        #     age: int


        # SyncCustomer = syncwave.make_reactive(Customer)
        # customers = syncwave.create_store(SyncList[SyncCustomer], name="customers")
        # customers.append(Customer(name="Alice", age=30))
        ```

        ---

        Abstract: Usage Documentation
            [Syncwave](https://placeholder.dev/usage/syncwave/)

        Args:
            name: Name of the store. This is also the key used to access the store
                (`syncwave[name]`), and the name of the corresponding JSON file
                (`<root_path>/<name>.json`).
            collection: Controls how the model is wrapped in a collection. See
                [usage](https://placeholder.dev/usage/syncwave/) for more details on the
                available options.

        Returns:
            A decorator that accepts a class as an argument to create a new reactive
                store and returns the original class unchanged.

        """
        if name in self.__stores:
            raise ValueError(f"Store '{name}' already exists.")

        str_guard("name", name)
        io.file_name_guard(name)

        def decorator(cls: type[SMS]) -> type[SMS]:
            sync_model_guard(cls, self.__models)
            sync_model = create_sync_model(cls)
            store_tp = collection_wrap(cls, sync_model, collection)
            self.__create_store(store_tp, name=name)
            self.__models.add(cls)
            return cls

        return decorator

    def __create_store(self, tp: type | GenericAlias, name: str) -> None:
        try:
            type_adapter = TypeAdapter(tp)
        except PydanticSchemaGenerationError as e:
            raise TypeError(f"Type `{tp}` is not supported by Pydantic.") from e

        path = self.__root_path / f"{name}.json"
        sref = StoreRef(lock=RLock(), on_change=partial(self.__on_store_change, name))
        ctx = drill_tp(tp)
        store_info = StoreInfo(name, path, type_adapter, sref, ctx)

        value = io.init_json(path, type_adapter)
        if isinstance(value, Reactive):
            if ctx is None:
                unreachable()
            elif isinstance(ctx, Context):
                value.__syncwave_init__(sref, ctx)
            elif isinstance(ctx, ContextMap):
                value.__syncwave_init__(sref, ctx[type(value)])
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
            self.__set_store(store_info.name, new_value)

    def __set_store(self, key: str, value: Any) -> None:
        # always called from within the store lock context
        old_value, store_info = self.__stores[key]
        new_value = store_info.type_adapter.validate_python(value)
        ctx, sref = store_info.ctx, store_info.sref

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
            unreachable()

        sref.on_change()

    def read_store_json(self, name: str) -> str:
        """Safely read the content of the JSON file associated with a store.

        Reading directly from the file is prone to race conditions, since writes are
        debounced and the file may lag behind the in-memory state. Use this method
        instead to always get the most up-to-date content.

        Example:
        ```python
        from pathlib import Path
        from time import sleep

        from syncwave import SyncList, Syncwave

        syncwave = Syncwave()

        name = "numbers"
        path = Path(syncwave.root_path / f"{name}.json")

        numbers = syncwave.create_store(SyncList[int], name=name)
        numbers.extend([1, 2, 3])

        print("read_store_json: ", syncwave.read_store_json(name))  # shows "[1, 2, 3]"
        print("read_text: ", path.read_text())  # still shows "[]"

        sleep(0.5)
        print("read_text after delay: ", path.read_text())  # now shows "[1, 2, 3]"
        ```

        ---

        Abstract: Usage Documentation
            [Syncwave](https://placeholder.dev/usage/syncwave/)

        Args:
            name: Name of the store.

        Returns:
            The content of the JSON file associated with the store.

        """
        if name not in self.__stores:
            raise KeyError(f"Store '{name}' does not exist.")
        store_info = self.__stores[name][1]
        return io.read_json(store_info.path)

    def write_store_json(self, name: str, text: str) -> None:
        """Safely write to the JSON file associated with a store.

        Writing directly to the file is prone to race conditions, since file system
        events are debounced and the store may lag behind the new file content. Use this
        method instead to ensure the store is updated immediately after the write.

        Example:
        ```python
        from pathlib import Path
        from time import sleep

        from syncwave import SyncList, Syncwave

        syncwave = Syncwave()

        name = "numbers"
        path = Path(syncwave.root_path / f"{name}.json")

        numbers = syncwave.create_store(SyncList[int], name=name)
        sleep(0.5)  # to avoid race a condition when creating the file

        path.write_text("[1, 2, 3]")
        print("Content: ", numbers)  # still shows "[]"
        sleep(0.5)
        print("Content after delay: ", numbers)  # now shows "[1, 2, 3]"

        numbers.clear()
        syncwave.write_store_json(name, "[9, 8, 7]")
        print("Content after write_store_json: ", numbers)  # shows "[9, 8, 7]"
        ```

        ---

        Abstract: Usage Documentation
            [Syncwave](https://placeholder.dev/usage/syncwave/)

        Args:
            name: Name of the store.
            text: Text to write to the JSON file.

        """
        if name not in self.__stores:
            raise KeyError(f"Store '{name}' does not exist.")
        store_info = self.__stores[name][1]
        io.write_json(store_info.path, text)
        self.__on_file_change(store_info)


Reactive.register(Syncwave)
