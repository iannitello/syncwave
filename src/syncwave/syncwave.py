from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import TypeAdapter

from .io import io
from .reactive import Reactive
from .sync_model import SyncModel, SyncModelSupported

Key = str
Store = Any


# Has to be thread-safe, this is a temporary solution just to start the implementation.


class Syncwave(MutableMapping[Key, Store], Reactive):
    def __init__(self, stores_dir: str | Path = "") -> None:
        stores_dir = (
            io.get_root_dir() / "syncstores"
            if stores_dir == ""
            else io.sanitize_path(stores_dir)
        )
        io.create_dir(stores_dir)
        self.stores_dir = stores_dir

        self.__syncwave_lock__ = RLock()
        self.__syncwave_stores__: dict[Key, Store] = {}
        self.__syncwave_registered__: dict[Key, TypeAdapter] = {}

    def __getitem__(self, key: Key) -> Store:
        with self.__syncwave_lock__:
            return self.__syncwave_stores__[key]

    def __setitem__(self, key: Key, value: Store) -> None:
        with self.__syncwave_lock__:
            if key not in self.__syncwave_registered__:
                self.__syncwave_stores__[key] = value

    def __delitem__(self, key: Key) -> None:
        with self.__syncwave_lock__:
            del self.__syncwave_stores__[key]

    def __iter__(self) -> Iterator[Key]:
        with self.__syncwave_lock__:
            # first convert to a list so the iterator is over a frozen object
            return iter(list(self.__syncwave_stores__))

    def __len__(self) -> int:
        with self.__syncwave_lock__:
            return len(self.__syncwave_stores__)

    def __syncwave_abc_marker__(self) -> None:
        pass

    # repr to be implemented
    # str to be implemented

    def register(
        self,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
    ) -> None:
        pass

    def reactive(self, cls: type[SyncModelSupported]) -> type[SyncModel]:
        return SyncModel._reactive(self, cls)
