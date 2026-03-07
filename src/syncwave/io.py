from __future__ import annotations

import contextlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from threading import Lock, Timer
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError

from .watcher import watcher

__all__ = []


PendingWrite = tuple[Any, TypeAdapter, Timer]


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

    def file_name_guard(self, name: str) -> None:
        # assumes name is already validated to be a non-empty string
        if os.sep in name or (os.altsep is not None and os.altsep in name):
            sep = repr(os.sep) + (f" or {os.altsep!r}" if os.altsep is not None else "")
            raise ValueError(f"File name cannot contain path separators ({sep}).")
        if Path(name).is_absolute():
            raise ValueError("File name cannot be an absolute path.")
        try:
            with TemporaryDirectory() as td:
                (Path(td) / name).mkdir()
        except OSError as e:
            raise ValueError(f"File name {name!r} is not valid: {e.strerror}") from None

    def sanitize_path(self, path: Path | str) -> Path:
        path_str = os.fspath(path)
        path_str = os.path.expandvars(path_str)
        path_str = os.path.expanduser(path_str)
        return Path(path_str).resolve()

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
            raise OSError(f"Path '{path}' exists but is not a regular file.")
        path.unlink(missing_ok=True)

    def init_json(self, path: Path, ta: TypeAdapter = _any_ta) -> Any | EmptyFileType:
        self.create_file(path)
        content = path.read_text(encoding=self.ENCODING).strip()
        if content == "":
            default = self._get_default(ta)
            if default is not EmptyFile:
                self._atomic_write(path, self._serialize(default, ta))
            return default
        init_value = self._deserialize(content, ta, path)
        self._atomic_write(path, self._serialize(init_value, ta))
        return init_value

    def read_json(self, path: Path, ta: TypeAdapter = _any_ta) -> Any:
        # never returns EmptyFile, it throws an error if the file is empty
        with self._lock:
            if path in self._pending_writes:
                value, previous_ta, _ = self._pending_writes[path]
                if previous_ta is ta:
                    return value
                text = self._serialize(value, previous_ta)
                return self._deserialize(text, ta, path)
        text = path.read_text(encoding=self.ENCODING).strip()
        return self._deserialize(text, ta, path)

    def write_json(self, path: Path, value: Any, ta: TypeAdapter = _any_ta) -> None:
        with self._lock:
            if path in self._pending_writes:
                self._pending_writes[path][2].cancel()
            timer = Timer(self.DEBOUNCE_WINDOW, self._scheduled_write, args=(path,))
            self._pending_writes[path] = (value, ta, timer)
            timer.start()

    def _scheduled_write(self, path: Path) -> None:
        with self._lock:
            if path not in self._pending_writes:
                return  # defensive check, should never happen
            value, ta, _ = self._pending_writes.pop(path)
        self._atomic_write(path, self._serialize(value, ta))

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
            raise OSError(
                f"Failed to write JSON to '{path}'. "
                f"Temporary file '{tmp_path}' removed. Original error: {e}"
            ) from e

    def _serialize(self, value: Any, ta: TypeAdapter) -> str:
        return ta.dump_json(value, **self.DUMPS_CONFIG).decode(self.ENCODING)

    def _deserialize(self, text: str, ta: TypeAdapter, path: Path) -> Any:
        try:
            return ta.validate_json(text)
        except ValidationError as e:
            try:
                self._any_ta.validate_json(text)
            except ValidationError:
                raise ValueError(f"File '{path}' contains malformed JSON.") from None
            raise ValueError(f"File '{path}' contains unexpected data type.") from e

    def _get_default(self, ta: TypeAdapter) -> Any | EmptyFileType:
        defaults = [{}, [], "", None]
        for default in defaults:
            try:
                return ta.validate_python(default)
            except ValidationError:
                continue
        return EmptyFile


io = _IO()
