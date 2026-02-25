from __future__ import annotations

from functools import partial
from pathlib import Path
from threading import Lock, Timer
from time import monotonic, sleep
from typing import Any, Callable, Final

from watchdog.events import (
    DirDeletedEvent,
    DirMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import ObservedWatch

DirPath = Path
FilePath = Path
Callback = Callable[..., None]


class _Watcher:
    TMP_FILE_PREFIX: Final[str] = ".tmp_"

    def __init__(self) -> None:
        self._lock = Lock()
        # dir_path -> (watch object, set of file_paths)
        self._watched_dirs: dict[DirPath, tuple[ObservedWatch, set[FilePath]]] = {}

        self._observer = Observer()
        self._event_handler = _EventHandler(self)
        self._observer.start()

    def watch(
        self,
        file_path: FilePath,
        callback: Callback,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if not file_path.is_file():
            raise ValueError(f"Path '{file_path}' is not a file.")

        dir_path = file_path.parent
        with self._lock:
            if dir_path not in self._watched_dirs:
                self._watched_dirs[dir_path] = (
                    self._observer.schedule(self._event_handler, str(dir_path)),
                    set(),
                )
            self._watched_dirs[dir_path][1].add(file_path)
        # set or reset the callback no matter what
        bound_callback = partial(callback, *args, **kwargs)
        self._event_handler.set_callback(file_path, bound_callback)

    def unwatch(self, file_path: FilePath) -> None:
        # remove the callback
        self._event_handler.unset_callback(file_path)

        dir_path = file_path.parent
        with self._lock:
            # if the directory is not being watched, do nothing
            if dir_path not in self._watched_dirs:
                return

            # if the file is in the set, remove it
            watch, watched_files = self._watched_dirs[dir_path]
            watched_files.discard(file_path)

            # if the set is empty, unschedule the directory
            if not watched_files:
                self._observer.unschedule(watch)
                del self._watched_dirs[dir_path]

    def mark_self_write(self, file_path: FilePath) -> None:
        self._event_handler.mark_self_write(file_path)

    def _get_watched_dirs(self) -> dict[DirPath, set[FilePath]]:
        with self._lock:
            return {
                dir_path: watched_files.copy()
                for dir_path, (_, watched_files) in self._watched_dirs.items()
            }

    def _restore_watched_dir(self, dir_path: DirPath) -> None:
        with self._lock:
            if dir_path not in self._watched_dirs:
                return
            dir_path.mkdir(parents=True, exist_ok=True)
            watch, watched_files = self._watched_dirs[dir_path]
            self._observer.unschedule(watch)
            self._watched_dirs[dir_path] = (
                self._observer.schedule(self._event_handler, str(dir_path)),
                watched_files,
            )


class _EventHandler(FileSystemEventHandler):
    DEBOUNCE_WINDOW: Final[float] = 0.05
    SELF_WRITE_WINDOW: Final[float] = 0.5

    def __init__(self, watcher: _Watcher) -> None:
        self._watcher = watcher
        self._lock = Lock()
        self._callbacks: dict[FilePath, Callback] = {}
        self._debounce_timers: dict[FilePath, Timer] = {}
        self._self_writes_ts: dict[FilePath, float] = {}

    def set_callback(self, file_path: FilePath, callback: Callback) -> None:
        with self._lock:
            self._callbacks[file_path] = callback

    def unset_callback(self, file_path: FilePath) -> None:
        with self._lock:
            self._callbacks.pop(file_path, None)

    def mark_self_write(self, file_path: FilePath) -> None:
        with self._lock:
            self._self_writes_ts[file_path] = monotonic()

    def _is_self_write(self, file_path: FilePath) -> bool:
        # not protected by the lock because it's only called internally
        ts = self._self_writes_ts.get(file_path)
        return ts is not None and monotonic() - ts < self.SELF_WRITE_WINDOW

    def dispatch(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            if not isinstance(event, (DirDeletedEvent, DirMovedEvent)):
                return
            watched_dirs = self._watcher._get_watched_dirs()
            for dir_path in self._paths_from_event(event):
                if dir_path in watched_dirs:
                    self._on_dir_deleted(dir_path, watched_dirs[dir_path])
        else:
            for file_path in self._paths_from_event(event):
                self._on_file_modified(file_path)

    def _on_dir_deleted(self, dir_path: DirPath, watched_files: set[FilePath]) -> None:
        sleep(self.DEBOUNCE_WINDOW)  # low-tech debounce
        self._watcher._restore_watched_dir(dir_path)
        for file_path in watched_files:
            self._on_file_modified(file_path)

    def _on_file_modified(self, file_path: FilePath) -> None:
        with self._lock:
            if file_path not in self._callbacks or self._is_self_write(file_path):
                return

            if timer := self._debounce_timers.get(file_path):
                timer.cancel()

            timer = Timer(
                self.DEBOUNCE_WINDOW,
                self._scheduled_callback,
                args=(file_path, self._callbacks[file_path]),
            )
            self._debounce_timers[file_path] = timer
            timer.start()

    def _scheduled_callback(self, file_path: FilePath, callback: Callback) -> None:
        with self._lock:
            self._debounce_timers.pop(file_path, None)
        callback()

    def _paths_from_event(self, event: FileSystemEvent) -> list[Path]:
        raw_paths = [event.src_path]
        if dest_path := getattr(event, "dest_path", None):
            raw_paths.append(dest_path)
        raw_paths_str = [p if isinstance(p, str) else p.decode() for p in raw_paths]
        return [
            Path(p).resolve()
            for p in raw_paths_str
            if p and self._watcher.TMP_FILE_PREFIX not in p
        ]


watcher = _Watcher()
