from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, Timer
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError

from .watcher import watcher

PyObj = Any  # any Python object that can be serialized to JSON by Pydantic
PendingWrite = tuple[PyObj, TypeAdapter, Timer]


class EmptyFileType: ...


EmptyFile: Final = EmptyFileType()


class _IO:
    ENCODING: Final[str] = "utf-8"
    DUMPS_CONFIG: Final[dict[str, Any]] = {"indent": 2, "warnings": "error"}
    DEBOUNCE_WINDOW: Final[float] = 0.05

    _any_ta = TypeAdapter(Any)

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending_writes: dict[Path, PendingWrite] = {}

    def sanitize_path(self, path: Path | str) -> Path:
        path_str = os.fspath(path)
        path_str = os.path.expandvars(path_str)
        path_str = os.path.expanduser(path_str)
        return Path(path_str).resolve()

    def get_root_dir(self) -> Path:
        main_module = sys.modules.get("__main__")
        if file_attr := getattr(main_module, "__file__", None):
            path = Path(file_attr).parent
        else:
            path = Path.cwd()
        return self.sanitize_path(path)

    def create_dir(self, path: Path) -> None:
        if path.exists() and not path.is_dir():
            raise FileExistsError(f"Path '{path}' exists and is not a directory.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"Permission denied to create dir '{path}'.") from e
        except OSError as e:
            raise OSError(f"Unable to create dir '{path}'.") from e

    def create_file(self, path: Path) -> None:
        self.create_dir(path.parent)
        if path.exists() and not path.is_file():
            raise FileExistsError(f"Path '{path}' exists and is not a file.")
        try:
            path.touch()
        except PermissionError as e:
            raise PermissionError(f"Permission denied to create file '{path}'.") from e
        except OSError as e:
            raise OSError(f"Unable to create file '{path}'.") from e

    def remove_file(self, path: Path) -> None:
        if path.exists() and not path.is_file():
            raise FileNotFoundError(f"Path '{path}' is not a file.")
        path.unlink(missing_ok=True)

    def init_json(self, path: Path, default: PyObj | EmptyFileType = EmptyFile) -> None:
        self.create_file(path)
        content = path.read_text(encoding=self.ENCODING).strip()
        if content == "":
            if default is not EmptyFile:
                self._atomic_write(path, self._dump(default, self._any_ta))
            return
        try:
            self._any_ta.validate_json(content)
        except ValidationError as e:
            raise ValueError(f"File '{path}' contains invalid JSON.") from e

    def _dump(self, value: PyObj, ta: TypeAdapter) -> str:
        return ta.dump_json(value, **self.DUMPS_CONFIG).decode(self.ENCODING)

    def read_json(self, path: Path, ta: TypeAdapter = _any_ta) -> PyObj:
        with self._lock:
            if path in self._pending_writes:
                value, previous_ta, _ = self._pending_writes[path]
                if previous_ta is ta:
                    return value
                text = self._dump(value, previous_ta)
                return ta.validate_json(text)
        text = path.read_text(encoding=self.ENCODING)
        return ta.validate_json(text)

    def write_json(self, path: Path, value: PyObj, ta: TypeAdapter = _any_ta) -> None:
        with self._lock:
            if path in self._pending_writes:
                self._pending_writes[path][2].cancel()
            timer = Timer(self.DEBOUNCE_WINDOW, self._scheduled_write, args=(path,))
            self._pending_writes[path] = (value, ta, timer)
            timer.start()

    def _scheduled_write(self, path: Path) -> None:
        with self._lock:
            if path not in self._pending_writes:
                return  # is this possible? should it be an error?
            value, ta, _ = self._pending_writes.pop(path)
        self._atomic_write(path, self._dump(value, ta))

    def _atomic_write(self, path: Path, text: str) -> None:
        fd, tmp_path = mkstemp(prefix=watcher.TMP_FILE_PREFIX, dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding=self.ENCODING) as tmp_file:
                tmp_file.write(text)
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
