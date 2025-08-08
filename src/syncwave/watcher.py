from pathlib import Path
from threading import Lock, Timer
from time import monotonic
from typing import Callable, Union

from watchdog.events import (
    DirModifiedEvent,
    FileModifiedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import ObservedWatch

DirPath = Path
FilePath = Path
Callback = Callable[[], None]


class _Watcher:
    SELF_WRITE_WINDOW = 0.5

    def __init__(self) -> None:
        self._lock = Lock()
        # mapping dir_path -> (watch object, set of file_paths)
        self._watched_dirs: dict[DirPath, tuple[ObservedWatch, set[FilePath]]] = {}
        self._self_writes_ts: dict[FilePath, float] = {}
        self._callbacks: dict[FilePath, Callback] = {}

        self._observer = Observer()
        self._event_handler = _EventHandler(self)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def watch(self, file_path: FilePath, callback: Callback) -> None:
        dir_path = file_path.parent

        if not file_path.is_file():
            raise ValueError(f"Path {file_path} is not a file.")

        with self._lock:
            # if the directory is already being watched...
            if dir_path in self._watched_dirs:
                _, watched_files = self._watched_dirs[dir_path]

                # ...but the file is not in the set, add it
                if file_path not in watched_files:
                    watched_files.add(file_path)

                # and in any case, update the callback before returning
                self._callbacks[file_path] = callback
                return

            # otherwise, schedule the directory and register the callback
            self._watched_dirs[dir_path] = (
                self._observer.schedule(self._event_handler, str(dir_path)),
                {file_path},
            )
            self._callbacks[file_path] = callback

    def unwatch(self, file_path: FilePath) -> None:
        dir_path = file_path.parent

        with self._lock:
            # remove the callback
            self._callbacks.pop(file_path, None)

            # if the directory is not being watched, do nothing
            if dir_path not in self._watched_dirs:
                return

            watch, watched_files = self._watched_dirs[dir_path]

            # if the file is in the set, remove it
            if file_path in watched_files:
                watched_files.remove(file_path)

            # if the set is empty, unschedule the directory
            if not watched_files:
                self._observer.unschedule(watch)
                del self._watched_dirs[dir_path]

    def mark_self_write(self, file_path: FilePath) -> None:
        with self._lock:
            self._self_writes_ts[file_path] = monotonic()

    def _is_self_write(self, file_path: FilePath) -> bool:
        with self._lock:
            ts = self._self_writes_ts.get(file_path)
            if ts is not None and monotonic() - ts < self.SELF_WRITE_WINDOW:
                return True
        return False


class _EventHandler(FileSystemEventHandler):
    DEBOUNCE_WINDOW = 0.05

    def __init__(self, watcher: _Watcher) -> None:
        self.lock = Lock()
        self.watcher = watcher
        self.debounce_timers: dict[FileSystemEvent, Timer] = {}

    def on_modified(self, event: Union[FileModifiedEvent, DirModifiedEvent]) -> None:
        if event.is_directory or isinstance(event, DirModifiedEvent):
            return

        file_path = Path(event.src_path).resolve()

        if self.watcher._is_self_write(file_path):
            return

        callback = self.watcher._callbacks.get(file_path)
        if callback is None:
            return

        with self.lock:
            if event in self.debounce_timers:
                self.debounce_timers[event].cancel()

            timer = Timer(
                self.DEBOUNCE_WINDOW,
                self.schedule_callback,
                args=(event, callback),
            )
            self.debounce_timers[event] = timer
            timer.start()

    def schedule_callback(self, event: FileSystemEvent, callback: Callback) -> None:
        try:
            callback()
        finally:
            with self.lock:
                self.debounce_timers.pop(event, None)


watcher = _Watcher()
