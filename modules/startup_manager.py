"""Manage per-user Windows startup registration for the application."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

try:
    import winreg as _winreg
except ImportError:  # pragma: no cover - exercised on non-Windows hosts
    _winreg = None


STARTUP_VALUE_NAME = "CS2 Rental Manager"
STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


class StartupNotSupportedError(RuntimeError):
    """Raised when Windows startup registration is unavailable."""


def _require_winreg() -> ModuleType:
    if sys.platform != "win32" or _winreg is None:
        raise StartupNotSupportedError("开机自启动仅支持 Windows 系统")
    return _winreg


def _source_entrypoint() -> Path:
    return Path(__file__).resolve().parent.parent / "main.py"


def _startup_command() -> str:
    """Return the command stored in the registry for this runtime mode."""
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        arguments = [str(executable)]
    else:
        pythonw = executable if executable.name.lower() == "pythonw.exe" else executable.with_name("pythonw.exe")
        arguments = [str(pythonw), str(_source_entrypoint().resolve())]
    return subprocess.list2cmdline(arguments)


def is_startup_enabled() -> bool:
    """Return whether the current application command is registered at login.

    A stale entry from a previous application location is reported as disabled,
    so enabling it again replaces the entry with the current path.
    """
    registry = _require_winreg()
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            STARTUP_REGISTRY_PATH,
            0,
            registry.KEY_READ,
        ) as key:
            command, _value_type = registry.QueryValueEx(key, STARTUP_VALUE_NAME)
    except FileNotFoundError:
        return False
    return command == _startup_command()


def set_startup_enabled(enabled: bool) -> bool:
    """Enable or disable startup for the current Windows user.

    Returns the resulting state. Disabling is idempotent when no entry exists.
    Registry permission and OS errors deliberately propagate to the caller so
    the settings UI can show an actionable failure message.
    """
    registry = _require_winreg()
    if enabled:
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER,
            STARTUP_REGISTRY_PATH,
            0,
            registry.KEY_SET_VALUE,
        ) as key:
            registry.SetValueEx(
                key,
                STARTUP_VALUE_NAME,
                0,
                registry.REG_SZ,
                _startup_command(),
            )
        return True

    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            STARTUP_REGISTRY_PATH,
            0,
            registry.KEY_SET_VALUE,
        ) as key:
            registry.DeleteValue(key, STARTUP_VALUE_NAME)
    except FileNotFoundError:
        pass
    return False
