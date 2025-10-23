from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar, overload

from pydantic import TypeAdapter

from .io import io
from .reactive import Reactive
from .sync_model import SyncModel, SyncModelSupported
from .watcher import watcher

T = TypeVar("T", bound=SyncModelSupported)


@dataclass
class _Metadata:
    key: str
    path: Path


class _UMetadata(_Metadata):
    # unregistered store metadata
    pass


class _RMetadata(_Metadata):
    # registered store metadata
    type_adapter: TypeAdapter


# Has to be thread-safe, this is a temporary solution just to start the implementation.
class Syncwave(MutableMapping[str, Any], Reactive):
    def __init__(self, stores_dir: str | Path = "") -> None:
        stores_dir = (
            io.get_root_dir() / "syncstores"
            if stores_dir == ""
            else io.sanitize_path(stores_dir)
        )
        io.create_dir(stores_dir)
        self.stores_dir = stores_dir

        self.__lock = RLock()
        self.__data: dict[str, tuple[Any, _Metadata]] = {}

    def __getitem__(self, key: str) -> Any:
        with self.__lock:
            return self.__data[key][0]

    def __setitem__(self, key: str, value: Any) -> None:
        key = str(key)
        with self.__lock:
            if key not in self.__data:
                path = self.stores_dir / f"{key}.json"
                meta = _UMetadata(key, path)
                data = deepcopy(value)
                self.__data[key] = (data, meta)
                io.create_file(path)
                io.write_json(path, lambda: data)
                watcher.watch(path, self.__sync_unregistered, meta=meta)
                return
            data, meta = self.__data[key]
            if isinstance(meta, _UMetadata):
                data = deepcopy(value)
                self.__data[key] = (data, meta)
                io.write_json(meta.path, lambda: data)
                return
            raise NotImplementedError("Registered store are not supported yet.")

    def __delitem__(self, key: str) -> None:
        with self.__lock:
            del self.__data[key]

    def __iter__(self) -> Iterator[str]:
        with self.__lock:
            # first convert to a list so the iterator is over a frozen object
            return iter(list(self.__data.keys()))

    def __len__(self) -> int:
        with self.__lock:
            return len(self.__data)

    def __syncwave_abc_marker__(self) -> None:
        pass

    def __sync_unregistered(self, meta: _UMetadata) -> None:
        with contextlib.suppress(FileNotFoundError, ValueError):
            data = deepcopy(io.read_json(meta.path))
        with self.__lock:
            self.__data[meta.key] = (data, None)
        io.write_json(meta.path, lambda: self.__data[meta.key][0])

    # repr to be implemented
    # str to be implemented
    @overload
    def register(
        self,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
    ) -> Callable[[type[T]], type[SyncModel[T]]]:
        """
        Decorator usage: @syncwave.register
        """
        ...

    @overload
    def register(
        self,
        type_: Any,
        /,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
    ) -> None:
        """
        Method usage: syncwave.register(type, ...)
        """
        ...

    def register(
        self,
        type_: Any = None,
        /,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
    ) -> Callable[[type[T]], type[SyncModel[T]]] | None:
        if type_ is None:
            # decorator usage
            def decorator(cls: type[T]) -> type[SyncModel[T]]:
                # implementation
                return cls

            return decorator
        # method usage
        # implementation
        pass
