from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, Timer
from typing import Any, Final

from pydantic import TypeAdapter
from pydantic_core import from_json, to_json

from .watcher import watcher

PyObj = Any  # any Python object that can be serialized to JSON by Pydantic


class _IO:
    ENCODING: Final[str] = "utf-8"
    DUMPS_CONFIG: Final[dict[str, Any]] = {"indent": 2}
    DEBOUNCE_WINDOW: Final[float] = 0.05

    def __init__(self) -> None:
        self._lock = Lock()
        self._debounce_timers: dict[Path, Timer] = {}
        self._pending_values: dict[Path, PyObj] = {}

    @staticmethod
    def sanitize_path(path: Path | str) -> Path:
        path_str = os.fspath(path)
        path_str = os.path.expandvars(path_str)
        path_str = os.path.expanduser(path_str)
        return Path(path_str).resolve()

    @staticmethod
    def get_root_dir() -> Path:
        main_module = sys.modules.get("__main__")
        if file_attr := getattr(main_module, "__file__", None):
            path = Path(file_attr).parent
        else:
            path = Path.cwd()
        return _IO.sanitize_path(path)

    @staticmethod
    def create_dir(path: Path) -> None:
        if path.exists() and not path.is_dir():
            raise FileExistsError(f"Path '{path}' exists and is not a directory.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"Permission denied to create dir '{path}'.") from e
        except OSError as e:
            raise OSError(f"Unable to create dir '{path}'.") from e

    @staticmethod
    def create_file(path: Path) -> None:
        _IO.create_dir(path.parent)
        if path.exists() and not path.is_file():
            raise FileExistsError(f"Path '{path}' exists and is not a file.")
        try:
            path.touch()
        except PermissionError as e:
            raise PermissionError(f"Permission denied to create file '{path}'.") from e
        except OSError as e:
            raise OSError(f"Unable to create file '{path}'.") from e

    def init_json(self, path: Path, default_value: PyObj = None) -> None:
        self.create_file(path)
        if path.stat().st_size == 0:
            if default_value is not None:
                self._atomic_write(path, default_value)
            return
        try:
            from_json(path.read_text(encoding=self.ENCODING))
        except ValueError as e:
            raise OSError(f"File '{path}' exists but is not a valid JSON file.") from e

    def json_dumps(self, value: PyObj) -> str:
        return to_json(value, **self.DUMPS_CONFIG).decode(self.ENCODING)

    def read_json(self, path: Path, type_adapter: TypeAdapter | None = None) -> PyObj:
        p_data: str | None = None  # pending data
        with self._lock:
            if path in self._pending_values:
                p_data = self.json_dumps(self._pending_values[path])
        data = path.read_text(encoding=self.ENCODING) if p_data is None else p_data
        if type_adapter is None:
            return from_json(data)
        return type_adapter.validate_json(data)

    def write_json(self, path: Path, value: PyObj) -> None:
        with self._lock:
            if path in self._debounce_timers:
                self._debounce_timers[path].cancel()

            self._pending_values[path] = value

            timer = Timer(
                self.DEBOUNCE_WINDOW,
                self._scheduled_write,
                args=(path, value),
            )
            self._debounce_timers[path] = timer
            timer.start()

    def _scheduled_write(self, path: Path, value: PyObj) -> None:
        with self._lock:
            self._debounce_timers.pop(path, None)
            self._pending_values.pop(path, None)
        self._atomic_write(path, value)

    def _atomic_write(self, path: Path, value: PyObj) -> None:
        fd, tmp_path = mkstemp(prefix=watcher.TMP_FILE_PREFIX, dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding=self.ENCODING) as tmp_file:
                tmp_file.write(self.json_dumps(value))
                tmp_file.write("\n")
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            # Atomically replace the target file
            watcher.mark_self_write(path)
            os.replace(tmp_path, path)
            # print(f"Wrote JSON to {path}")

        except Exception as e:
            # Attempt to remove temporary file on error
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            # Raise a clear error message including context
            raise OSError(
                f"Failed to write JSON to '{path}'. "
                f"Temporary file '{tmp_path}' removed. Original error: {e}"
            ) from e


io = _IO()
