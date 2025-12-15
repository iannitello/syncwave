from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from inspect import isclass
from pathlib import Path
from threading import RLock
from typing import Annotated, Any, Union, get_args, get_origin, overload

from pydantic import PydanticSchemaGenerationError, TypeAdapter

from .io import io
from .reactive import Context, ContextMap, Reactive
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
from .sync_model import SyncModel, T
from .watcher import watcher

default_provider = {"array": list, "object": dict, "string": str, "null": lambda: None}
_UNINITIALIZED = object()


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
class Syncwave(MutableMapping[str, Any]):
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

    def __sync_registered(self, meta: _RMetadata) -> None:
        with contextlib.suppress(FileNotFoundError, ValueError):
            data = meta.type_adapter.validate_python(io.read_json(meta.path))
        with self.__lock:
            self.__data[meta.key] = (data, meta)
        io.write_json(meta.path, lambda: data)

    # repr to be implemented
    # str to be implemented

    @overload
    def register(
        self, *, name: str | None = None
    ) -> Callable[[type[T]], type[SyncModel[T]]]:
        """
        Decorator usage: @syncwave.register
        """
        ...

    @overload
    def register(self, type: Any, /, *, name: str) -> None:
        """
        Method usage: syncwave.register(type, ...)
        """
        ...

    def register(
        self, type: Any = None, /, *, name: str | None = None
    ) -> Callable[[type[T]], type[SyncModel[T]]] | None:
        if type is None:
            # decorator usage
            def decorator(cls: type[T]) -> type[SyncModel[T]]:
                # implementation
                return cls

            return decorator

        # method usage
        try:
            ta = TypeAdapter(type)
        except PydanticSchemaGenerationError as e:
            raise ValueError(f"Type '{type}' is not supported.") from e
        if name is None:
            raise ValueError("A 'name' is required.")
        name = str(name)
        path = self.stores_dir / f"{name}.json"
        meta = _RMetadata(name, path, ta)
        with self.__lock:
            if name in self:
                raise ValueError(f"The name '{name}' has already been registered.")
            default = default_provider.get(ta.json_schema()["type"])
            self.__data[name] = (default() if default else _UNINITIALIZED, meta)
            io.init_json(path, default)
            watcher.watch(path, self.__sync_registered, meta=meta)


def drill_tp(tp: Any, reactive_allowed: bool) -> Context | ContextMap | None:
    origin = get_origin(tp) or tp
    args = get_args(tp)

    if isclass(origin) and issubclass(origin, Reactive):
        if not reactive_allowed:
            raise TypeError("Cannot break the reactivity chain.")
        if issubclass(origin, SyncModel):
            ctx = getattr(origin, "__syncwave_ctx__", None)
            if ctx is None:
                raise TypeError(
                    f"'{origin.__name__}' is a SyncModel but has no context. "
                    f"Ensure dependent models are made reactive first."
                )
            return ctx
        if issubclass(origin, SyncDict):
            return _get_sync_dict_ctx(tp)
        if issubclass(origin, SyncList):
            return _get_sync_list_ctx(tp)
        if issubclass(origin, SyncSet):
            return _get_sync_set_ctx(tp)
        raise TypeError(f"Unknown reactive type: {origin.__name__}")  # shouldn't happen

    if origin is Annotated:
        if len(args) < 1:
            raise ValueError("Annotated must have arguments.")
        return drill_tp(args[0], reactive_allowed)

    if origin is Union or str(origin) == "typing.Union":
        return _get_union_ctx(args, reactive_allowed)

    for arg in args:
        drill_tp(arg, reactive_allowed=False)

    return None


def _get_sync_dict_ctx(tp: type[SyncDict[KT, VT]]) -> SyncDictCtx[KT, VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 2:
        vt = args[1]
        inner_ctx = drill_tp(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncDict must have 0 or 2 arguments.")

    return SyncDictCtx(
        tp=SyncDict,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_list_ctx(tp: type[SyncList[VT]]) -> SyncListCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        inner_ctx = drill_tp(vt, reactive_allowed=True)
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_ctx = None
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncList must have 0 or 1 argument.")

    return SyncListCtx(
        tp=SyncList,
        type_adapter=TypeAdapter(tp),
        inner_ctx=inner_ctx,
        inner_type_adapter=inner_type_adapter,
    )


def _get_sync_set_ctx(tp: type[SyncSet[VT]]) -> SyncSetCtx[VT]:
    args = get_args(tp)
    len_args = len(args)

    if len_args == 1:
        vt = args[0]
        drill_tp(vt, reactive_allowed=False)  # will raise if reactive
        inner_type_adapter = TypeAdapter(vt)
    elif len_args == 0:
        inner_type_adapter = TypeAdapter(Any)
    else:
        raise TypeError("SyncSet must have 0 or 1 argument.")

    return SyncSetCtx(
        tp=SyncSet,
        type_adapter=TypeAdapter(tp),
        inner_ctx=None,
        inner_type_adapter=inner_type_adapter,
    )


def _get_union_ctx(args: tuple[Any, ...], reactive_allowed: bool) -> ContextMap | None:
    if not args:
        raise ValueError("Union must have arguments.")

    contexts = [drill_tp(tp, reactive_allowed) for tp in args]
    ctx_map = {ctx.tp: ctx for ctx in contexts if ctx is not None}

    if not ctx_map:
        return None

    return ContextMap(ctx_map)
