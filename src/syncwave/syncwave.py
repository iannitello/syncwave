from collections.abc import Mapping
from inspect import isclass
from json import JSONDecodeError
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator, Optional, Type, Union, get_args

from pydantic import BaseModel, ValidationError

from .io import io
from .watcher import watcher

# Only acceptable types for the key field to be used as a JSON key
JSONKey = Union[str, int, float, bool, None]


def _validate_key_type(cls: Type[BaseModel], key: str) -> None:
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
        syncwave: "Syncwave",
        cls: Type[BaseModel],
        *,
        name: Optional[str] = None,
        key: str,
        skip_key_validation: bool = False,
        file_name: Optional[str] = None,
        sub_dir: Optional[Union[Path, str]] = None,
        file_path: Optional[Union[Path, str]] = None,
        # in_memory: bool = True,  # TODO: implement in_memory
    ) -> None:
        if not isinstance(syncwave, Syncwave):
            raise ValueError("The syncwave argument must be an instance of Syncwave.")
        if not (isclass(cls) and issubclass(cls, BaseModel)):
            raise ValueError("Only subclasses of BaseModel can be used.")
        if (name_ := name or cls.__name__.lower()) in syncwave:
            raise ValueError(f"The name '{name_}' is already registered.")
        if cls in [store.cls for store in syncwave.values()]:
            raise ValueError(f"The class '{cls.__name__}' is already registered.")
        if key not in cls.model_fields:
            raise ValueError(f"'{cls.__name__}' does not have a field '{key}'.")
        if not skip_key_validation:
            _validate_key_type(cls, key)

        self.syncwave = syncwave
        self.cls = cls
        self.name = name_
        self.key = key
        self.path = self._get_path(file_name or name_, sub_dir, file_path)

        self._ssd: dict[JSONKey, BaseModel] = {}  # actual mapping, ssd = SyncStoreDict
        self._lock = RLock()
        self._patch_cls()
        io.init_json_file(self.path)
        self.load()
        watcher.watch(self.path, self.load)
        self.syncwave._swd[self.name] = self

    def __getitem__(self, key: JSONKey) -> BaseModel:
        with self._lock:
            return self._ssd[key]

    def __iter__(self) -> Iterator[JSONKey]:
        with self._lock:
            # first, convert to a list so the iterator is over a frozen object
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
            return {
                key: model_instance.model_dump()
                for key, model_instance in self._ssd.items()
            }

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
        sub_dir: Union[Path, str, None],
        file_path: Union[Path, str, None],
    ) -> Path:
        if file_path:
            return Path(file_path).resolve()
        file_name = file_name if file_name.endswith(".json") else f"{file_name}.json"
        if sub_dir:
            return (self.syncwave.data_dir / sub_dir / file_name).resolve()
        return (self.syncwave.data_dir / file_name).resolve()

    def _patch_cls(self) -> None:
        # preserve original methods
        original_init = self.cls.__init__
        original_setattr = self.cls.__setattr__
        original_delattr = self.cls.__delattr__

        def __init__(model_instance: BaseModel, *args: Any, **kwargs: Any) -> None:
            original_init(model_instance, *args, **kwargs)
            key_value = getattr(model_instance, self.key)
            with self._lock:
                self._ssd[key_value] = model_instance
            io.write_json(self.path, self.to_dict)

        def __setattr__(model_instance: BaseModel, attr: str, value: Any) -> None:
            # TODO check for edge cases
            old_value = getattr(model_instance, attr)
            original_setattr(model_instance, attr, value)
            with self._lock:
                if attr == self.key:
                    del self._ssd[old_value]
                    self._ssd[value] = model_instance
            io.write_json(self.path, self.to_dict)

        def __delattr__(model_instance: BaseModel, attr: str) -> None:
            old_value = getattr(model_instance, attr)
            original_delattr(model_instance, attr)
            with self._lock:
                if attr == self.key:
                    del self._ssd[old_value]
            io.write_json(self.path, self.to_dict)

        # override original methods
        self.cls.__init__ = __init__
        self.cls.__setattr__ = __setattr__
        self.cls.__delattr__ = __delattr__


class Syncwave(Mapping[str, SyncStore]):
    def __init__(self, data_dir: Union[str, Path]) -> None:
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

    def register(
        self,
        *,
        name: Optional[str] = None,
        key: str,
        skip_key_validation: bool = False,
        file_name: Optional[str] = None,
        sub_dir: Optional[Union[Path, str]] = None,
        file_path: Optional[Union[Path, str]] = None,
        # in_memory: bool = True,  # TODO: implement in_memory
    ) -> Callable[[Type[BaseModel]], Type[BaseModel]]:
        def decorator(cls: Type[BaseModel]) -> Type[BaseModel]:
            SyncStore(
                self,
                cls,
                name=name,
                key=key,
                skip_key_validation=skip_key_validation,
                file_name=file_name,
                sub_dir=sub_dir,
                file_path=file_path,
            )
            return cls

        return decorator
