from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, Timer
from typing import Any, Callable, Final

from .watcher import watcher

JSONData = Any
DataProvider = Callable[[], JSONData]


class _IO:
    ENCODING: Final[str] = "utf-8"
    DEFAULT_JSON_CONTENT: Final[JSONData] = {}
    DUMPS_CONFIG: Final[dict[str, Any]] = {"indent": 2, "ensure_ascii": False}
    DEBOUNCE_WINDOW: Final[float] = 0.05

    def __init__(self) -> None:
        self._lock = Lock()
        self._debounce_timers: dict[Path, Timer] = {}
        self._pending_data_providers: dict[Path, DataProvider] = {}

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
            raise PermissionError(
                f"Permission denied to create directory at '{path}'."
            ) from e
        except OSError as e:
            raise OSError(f"Unable to create directory at '{path}'.") from e

    @staticmethod
    def create_file(path: Path) -> None:
        _IO.create_dir(path.parent)
        if path.exists() and not path.is_file():
            raise FileExistsError(f"Path '{path}' exists and is not a file.")
        try:
            path.touch(exist_ok=True)
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied to create file at '{path}'."
            ) from e
        except OSError as e:
            raise OSError(f"Unable to create file at '{path}'.") from e

    def json_dumps(self, data: JSONData) -> str:
        return json.dumps(data, **self.DUMPS_CONFIG)

    def init_json_file(self, path: Path) -> None:
        self.create_dir(path.parent)

        if path.is_dir():
            raise IsADirectoryError(f"Path '{path}' is a directory, not a file.")
        if not path.exists() or path.stat().st_size == 0:
            self._atomic_write(path, self.DEFAULT_JSON_CONTENT)
            return
        try:
            with path.open(encoding=self.ENCODING) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            raise OSError(f"File '{path}' exists but is not a valid JSON file.") from e

    def read_json(self, path: Path) -> JSONData:
        with self._lock:
            if path in self._pending_data_providers:
                return self._pending_data_providers[path]()
        with path.open(encoding=self.ENCODING) as f:
            return json.load(f)

    def write_json(self, path: Path, data_provider: DataProvider) -> None:
        with self._lock:
            if path in self._debounce_timers:
                self._debounce_timers[path].cancel()

            self._pending_data_providers[path] = data_provider

            timer = Timer(
                self.DEBOUNCE_WINDOW,
                self._scheduled_write,
                args=(path, data_provider),
            )
            self._debounce_timers[path] = timer
            timer.start()

    def _scheduled_write(self, path: Path, data_provider: DataProvider) -> None:
        with self._lock:
            self._debounce_timers.pop(path, None)
            self._pending_data_providers.pop(path, None)
        self._atomic_write(path, data_provider())

    def _atomic_write(self, path: Path, data: JSONData) -> None:
        fd, tmp_path = mkstemp(prefix=watcher.TMP_FILE_PREFIX, dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding=self.ENCODING) as tmp_file:
                json.dump(data, tmp_file, **self.DUMPS_CONFIG)
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
