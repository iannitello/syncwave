from __future__ import annotations

from collections.abc import Iterator, Mapping
from inspect import isclass
from json import JSONDecodeError
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Union, get_args, overload

from pydantic import BaseModel, ValidationError, create_model, model_validator

from .io import io
from .watcher import watcher

# Only acceptable types for the key field to be used as a JSON key
JSONKey = Union[str, int, float, bool, None]


def _validate_key_type(cls: type[BaseModel], key: str) -> None:
    # This will work if the key field is a basic type (str, int, float, bool, or None),
    # but will fail for more complex types (Union[str, int], Optional[str], etc.).
    # In that case skip the validation.
    key_type = cls.model_fields[key].annotation
    if key_type in get_args(JSONKey):
        return
    raise TypeError(
        f"The key field '{key}' in class '{cls.__module__}.{cls.__qualname__}' will be "
        "used as a JSON key. It must be a basic type (str, int, float, bool, or None), "
        f"not {key_type.__name__ if hasattr(key_type, '__name__') else str(key_type)}."
    )


class SyncStore(Mapping[JSONKey, BaseModel]):
    def __init__(
        self,
        syncwave: Syncwave,
        cls: type[BaseModel],
        /,
        name: str | None = None,
        key: str | None = None,
        *,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
        new_cls_name: str | None = None,
        # in_memory: bool = True,  # TODO: implement in_memory
    ) -> None:
        if not isinstance(syncwave, Syncwave):
            raise TypeError("The first argument must be an instance of Syncwave.")
        if not (isclass(cls) and issubclass(cls, BaseModel)):
            raise TypeError("Only subclasses of Pydantic's BaseModel can be used.")
        # `store.cls` is created with `create_model`, with `cls` as its only base class
        # in other words, this checks that `cls` has not been registered already
        if any(store.cls.__bases__[0] is cls for store in syncwave.values()):
            raise ValueError(f"The class '{cls.__name__}' has already been registered.")
        # `name` defaults to the lowercased class name if not provided
        if (name_ := name or cls.__name__.lower()) in syncwave:
            raise ValueError(f"The name '{name_}' has already been registered.")
        if not cls.model_fields:
            raise ValueError(f"Class '{cls.__name__}' has no fields defined.")
        # `key` defaults to the first field in the model if not provided
        if (key_ := key or next(iter(cls.model_fields))) not in cls.model_fields:
            raise ValueError(f"'{cls.__name__}' does not have a field named '{key_}'.")
        if not skip_key_validation:
            _validate_key_type(cls, key_)

        self.syncwave = syncwave
        self.cls = self._patch_cls(cls, new_cls_name or f"Syncwave{cls.__name__}")
        self.name = name_
        self.key = key_
        self.path = self._get_path(file_name or name_, sub_dir, file_path)

        self._ssd: dict[JSONKey, BaseModel] = {}  # actual mapping, ssd = SyncStoreDict
        self._lock = RLock()
        io.init_json_file(self.path)
        self.load()
        watcher.watch(self.path, self.load)
        self.syncwave._swd[self.name] = self

    def __getitem__(self, key: JSONKey) -> BaseModel:
        with self._lock:
            return self._ssd[key]

    def __iter__(self) -> Iterator[JSONKey]:
        with self._lock:
            # first convert to a list so the iterator is over a frozen object
            return iter(list(self._ssd))

    def __len__(self) -> int:
        with self._lock:
            return len(self._ssd)

    def __repr__(self) -> str:
        with self._lock:
            return repr(self._ssd)

    def __str__(self) -> str:
        return io.json_dumps(self.to_dict())

    def to_dict(self) -> dict[JSONKey, dict[str, Any]]:
        with self._lock:
            return {key: instance.model_dump() for key, instance in self._ssd.items()}

    def delete(self, key: JSONKey) -> None:
        with self._lock:
            del self._ssd[key]
        io.write_json(self.path, self.to_dict)

    def load(self) -> None:
        try:
            tmp_ssd: dict[JSONKey, BaseModel] = {}
            for value in io.read_json(self.path).values():
                model = self.cls.model_validate(value)
                tmp_ssd[getattr(model, self.key)] = model
            with self._lock:
                # no JSONDecodeError or ValidationError at this point, safe to clear
                self._ssd.clear()
                self._ssd.update(tmp_ssd)
        except (FileNotFoundError, JSONDecodeError, ValidationError):
            pass
        finally:
            io.write_json(self.path, self.to_dict)

    def _get_path(
        self,
        file_name: str,
        sub_dir: Path | str | None,
        file_path: Path | str | None,
    ) -> Path:
        if file_path:
            return Path(file_path).resolve()
        file_name = file_name if file_name.endswith(".json") else f"{file_name}.json"
        if sub_dir:
            return (self.syncwave.data_dir / sub_dir / file_name).resolve()
        return (self.syncwave.data_dir / file_name).resolve()

    def _patch_cls(self, cls: type[BaseModel], new_cls_name: str) -> type[BaseModel]:
        # This is a validator that acts as a post-initialization hook.
        # Using a validator instead of `__init__` ensures every instance is tracked.
        # Libraries such as FastAPI may create instances indirectly with methods like
        # `validate_python` or `model_validate` and `__init__` may never be called.
        def init(self_model: BaseModel) -> BaseModel:
            key_value = getattr(self_model, self.key)
            with self._lock:
                if key_value in self._ssd and self._ssd[key_value] == self_model:
                    return self_model  # no changes, no need to write
                self._ssd[key_value] = self_model
            io.write_json(self.path, self.to_dict)
            return self_model

        new_cls = create_model(
            new_cls_name,
            __base__=cls,
            __validators__={"__syncwave_init__": model_validator(mode="after")(init)},
        )

        # preserve original methods
        original_setattr = cls.__setattr__
        original_delattr = cls.__delattr__

        def __setattr__(self_model: BaseModel, attr: str, value: Any) -> None:  # noqa: ANN401
            # TODO check for edge cases
            old_value = getattr(self_model, attr)
            original_setattr(self_model, attr, value)
            with self._lock:
                if attr == self.key:
                    del self._ssd[old_value]
                    self._ssd[value] = self_model
            io.write_json(self.path, self.to_dict)

        def __delattr__(self_model: BaseModel, attr: str) -> None:
            old_value = getattr(self_model, attr)
            original_delattr(self_model, attr)
            with self._lock:
                if attr == self.key:
                    del self._ssd[old_value]
            io.write_json(self.path, self.to_dict)

        # assign the new methods
        new_cls.__setattr__ = __setattr__
        new_cls.__delattr__ = __delattr__

        return new_cls


class Syncwave(Mapping[str, SyncStore]):
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self._swd: dict[str, SyncStore] = {}  # actual mapping, swd = SyncWaveDict

    def __getitem__(self, key: str) -> SyncStore:
        return self._swd[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._swd)

    def __len__(self) -> int:
        return len(self._swd)

    def __repr__(self) -> str:
        return repr(self._swd)

    def __str__(self) -> str:
        return io.json_dumps(self.to_dict())

    def to_dict(self) -> dict[str, dict[JSONKey, dict[str, Any]]]:
        return {name: syncstore.to_dict() for name, syncstore in self._swd.items()}

    def load(self) -> None:
        for syncstore in self._swd.values():
            syncstore.load()

    def stop(self) -> None:
        watcher.stop()

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
    ) -> Callable[[type[BaseModel]], type[BaseModel]]: ...

    def register(
        self,
        _cls: type[BaseModel] | None = None,
        /,
        *,
        name: str | None = None,
        key: str | None = None,
        skip_key_validation: bool = False,
        file_name: str | None = None,
        sub_dir: Path | str | None = None,
        file_path: Path | str | None = None,
        # in_memory: bool = True,  # TODO: implement in_memory
    ) -> type[BaseModel] | Callable[[type[BaseModel]], type[BaseModel]]:
        def decorator(cls: type[BaseModel]) -> type[BaseModel]:
            store = SyncStore(
                self,
                cls,
                name=name,
                key=key,
                skip_key_validation=skip_key_validation,
                file_name=file_name,
                sub_dir=sub_dir,
                file_path=file_path,
                new_cls_name=cls.__name__,
            )
            return store.cls

        if _cls is None:
            # called with parenthesis: @syncwave.register(...)
            return decorator
        # called without parenthesis: @syncwave.register
        return decorator(_cls)
