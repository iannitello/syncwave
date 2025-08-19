import json
import os
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, Timer
from typing import Any, Callable

from .watcher import watcher

JSONData = Any
DataProvider = Callable[[], JSONData]


class _IO:
    ENCODING = "utf-8"
    DEFAULT_JSON_CONTENT = {}
    DUMPS_CONFIG = {"indent": 2, "ensure_ascii": False}
    DEBOUNCE_WINDOW = 0.05

    def __init__(self) -> None:
        self._lock = Lock()
        self._debounce_timers: dict[Path, Timer] = {}
        self._pending_data_providers: dict[Path, DataProvider] = {}

    def json_dumps(self, data: JSONData) -> str:
        return json.dumps(data, **self.DUMPS_CONFIG)

    def init_json_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.is_dir():
            raise IsADirectoryError(f"Path '{path}' is a directory, not a file.")
        if not path.exists() or path.stat().st_size == 0:
            self._atomic_write(path, self.DEFAULT_JSON_CONTENT)
            return
        try:
            with path.open(encoding=self.ENCODING) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            raise IOError(f"File '{path}' exists but is not a valid JSON file.") from e

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

        except Exception as e:
            # Attempt to remove temporary file on error
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            # Raise a clear error message including context
            raise IOError(
                f"Failed to write JSON to '{path}'. "
                f"Temporary file '{tmp_path}' removed. Original error: {e}"
            ) from e


io = _IO()
