from __future__ import annotations

import os
import sys
from pathlib import Path


def expand_path(path: str | Path) -> str:
    path_str = os.fspath(path)
    path_str = os.path.expandvars(path_str)
    path_str = os.path.expanduser(path_str)
    return path_str


def get_main_module_dir() -> Path | None:
    """Returns the directory of the main module."""
    main_module = sys.modules.get("__main__")
    if file_attr := getattr(main_module, "__file__", None):
        return Path(file_attr).parent
    return None
