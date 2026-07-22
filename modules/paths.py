"""Application paths for private, non-versioned runtime data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    # A PyInstaller executable is extracted to a temporary directory. Runtime
    # data must instead live beside the executable (or its release folder),
    # never in that temporary extraction directory.
    _EXECUTABLE_DIR = Path(sys.executable).resolve().parent
    PROJECT_DIR = (
        _EXECUTABLE_DIR.parent
        if (_EXECUTABLE_DIR.parent / "private-data").exists()
        else _EXECUTABLE_DIR
    )
else:
    PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRIVATE_DATA_DIR = PROJECT_DIR / "private-data"


def get_private_data_dir() -> Path:
    """Return the runtime-data directory and create it when needed.

    ``CS2_RENTAL_DATA_DIR`` can point to a cloud-synchronised private folder on
    another machine. Without it, the portable ``private-data`` folder beside
    the application is used. Both locations are excluded from Git.
    """
    configured = os.environ.get("CS2_RENTAL_DATA_DIR", "").strip()
    data_dir = Path(configured).expanduser() if configured else DEFAULT_PRIVATE_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_private_path(*parts: str) -> Path:
    """Build a path below the configured private-data directory."""
    return get_private_data_dir().joinpath(*parts)
