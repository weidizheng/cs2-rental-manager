import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules import startup_manager


class StartupManagerTests(unittest.TestCase):
    def make_registry(self):
        key = MagicMock()
        key.__enter__.return_value = "registry-key"
        registry = SimpleNamespace(
            HKEY_CURRENT_USER="hkcu",
            KEY_READ=1,
            KEY_SET_VALUE=2,
            REG_SZ=1,
            OpenKey=MagicMock(return_value=key),
            CreateKeyEx=MagicMock(return_value=key),
            QueryValueEx=MagicMock(),
            SetValueEx=MagicMock(),
            DeleteValue=MagicMock(),
        )
        return registry

    def windows_runtime(self, registry):
        return patch.multiple(startup_manager, _winreg=registry), patch.object(
            startup_manager.sys, "platform", "win32"
        )

    def test_frozen_executable_is_written_for_current_user(self):
        registry = self.make_registry()
        runtime_patch, platform_patch = self.windows_runtime(registry)
        with runtime_patch, platform_patch, patch.object(
            startup_manager.sys, "executable", r"C:\Apps\CS2 Rental Manager\manager.exe"
        ), patch.object(startup_manager.sys, "frozen", True, create=True), patch.object(
            startup_manager.Path, "resolve", lambda value: value
        ):
            self.assertTrue(startup_manager.set_startup_enabled(True))

        registry.CreateKeyEx.assert_called_once_with(
            "hkcu", startup_manager.STARTUP_REGISTRY_PATH, 0, registry.KEY_SET_VALUE
        )
        registry.SetValueEx.assert_called_once_with(
            "registry-key",
            startup_manager.STARTUP_VALUE_NAME,
            0,
            registry.REG_SZ,
            '"C:\\Apps\\CS2 Rental Manager\\manager.exe"',
        )

    def test_source_mode_uses_pythonw_and_main_py(self):
        registry = self.make_registry()
        runtime_patch, platform_patch = self.windows_runtime(registry)
        entrypoint = Path(r"C:\Project Folder\main.py")
        with runtime_patch, platform_patch, patch.object(
            startup_manager.sys, "executable", r"C:\Python313\python.exe"
        ), patch.object(startup_manager.sys, "frozen", False, create=True), patch.object(
            startup_manager, "_source_entrypoint", return_value=entrypoint
        ), patch.object(startup_manager.Path, "resolve", lambda value: value):
            startup_manager.set_startup_enabled(True)

        written_command = registry.SetValueEx.call_args.args[-1]
        self.assertEqual(written_command, 'C:\\Python313\\pythonw.exe "C:\\Project Folder\\main.py"')

    def test_enabled_requires_the_current_command(self):
        registry = self.make_registry()
        runtime_patch, platform_patch = self.windows_runtime(registry)
        with runtime_patch, platform_patch, patch.object(
            startup_manager, "_startup_command", return_value='"C:\\current.exe"'
        ):
            registry.QueryValueEx.return_value = ('"C:\\current.exe"', registry.REG_SZ)
            self.assertTrue(startup_manager.is_startup_enabled())
            registry.QueryValueEx.return_value = ('"C:\\old.exe"', registry.REG_SZ)
            self.assertFalse(startup_manager.is_startup_enabled())

    def test_missing_registry_value_is_disabled(self):
        registry = self.make_registry()
        registry.QueryValueEx.side_effect = FileNotFoundError
        runtime_patch, platform_patch = self.windows_runtime(registry)
        with runtime_patch, platform_patch:
            self.assertFalse(startup_manager.is_startup_enabled())

    def test_disable_removes_only_the_application_value(self):
        registry = self.make_registry()
        runtime_patch, platform_patch = self.windows_runtime(registry)
        with runtime_patch, platform_patch:
            self.assertFalse(startup_manager.set_startup_enabled(False))
        registry.DeleteValue.assert_called_once_with("registry-key", startup_manager.STARTUP_VALUE_NAME)

    def test_disable_is_idempotent_when_run_key_or_value_is_missing(self):
        registry = self.make_registry()
        registry.OpenKey.side_effect = FileNotFoundError
        runtime_patch, platform_patch = self.windows_runtime(registry)
        with runtime_patch, platform_patch:
            self.assertFalse(startup_manager.set_startup_enabled(False))

    def test_non_windows_has_an_explicit_error(self):
        with patch.object(startup_manager.sys, "platform", "linux"):
            with self.assertRaisesRegex(startup_manager.StartupNotSupportedError, "Windows"):
                startup_manager.is_startup_enabled()
            with self.assertRaisesRegex(startup_manager.StartupNotSupportedError, "Windows"):
                startup_manager.set_startup_enabled(True)


if __name__ == "__main__":
    unittest.main()
