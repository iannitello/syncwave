from __future__ import annotations

import contextlib
from collections.abc import Iterator, MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Union

from pydantic import TypeAdapter

from .io import io
from .reactive import Reactive
from .sync_model import SyncModel, SyncModelSupported
from .watcher import watcher


@dataclass
class _UMeta:
    # unregistered store metadata
    key: str
    path: Path


@dataclass
class _RMeta:
    # registered store metadata
    key: str
    path: Path
    type_adapter: TypeAdapter


_Meta = Union[_UMeta, _RMeta]


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
        self.__data: dict[str, tuple[Any, _Meta]] = {}

    def __getitem__(self, key: str) -> Any:
        with self.__lock:
            return self.__data[key][0]

    def __setitem__(self, key: str, value: Any) -> None:
        key = str(key)
        with self.__lock:
            if key not in self.__data:
                path = self.stores_dir / f"{key}.json"
                meta = _UMeta(key, path)
                data = deepcopy(value)
                self.__data[key] = (data, meta)
                io.create_file(path)
                io.write_json(path, lambda: data)
                watcher.watch(path, self.__sync_unregistered, meta=meta)
                return
            if isinstance(self.__data[key][1], _UMeta):
                self.__data[key][0] = deepcopy(value)
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

    def __sync_unregistered(self, meta: _UMeta) -> None:
        context = contextlib.suppress(FileNotFoundError, ValueError)
        with context:
            data = io.read_json(meta.path)
            self.__data[meta.key] = (deepcopy(data), meta)
        io.write_json(meta.path, lambda: self.__data[meta.key][0])

    # repr to be implemented
    # str to be implemented

    def register(
        self,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
    ) -> None:
        pass

    def reactive(self, cls: type[SyncModelSupported]) -> type[SyncModel]:
        return SyncModel._reactive(self, cls)
