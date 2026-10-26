"""Installed resource paths, runtime commands, and editable-data preservation."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_runtime
import data_runtime


class DistributionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_frozen_scripts_use_companion_runner_without_python_flags(self):
        (self.root / "PyDropRuntime.exe").touch()
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(self.root / "PyDrop.exe")), patch.object(sys, "platform", "win32"):
            command = app_runtime.script_command("example with spaces.py")
        self.assertEqual(command, [str(self.root / "PyDropRuntime.exe"), "--script", "example with spaces.py"])

    def test_incomplete_application_folder_explains_missing_runner(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(self.root / "PyDrop.exe")):
            with self.assertRaisesRegex(OSError, "complete application folder"):
                app_runtime.script_command("example.py")

    def test_source_pythonw_uses_console_interpreter(self):
        with patch.object(sys, "frozen", False, create=True), patch.object(sys, "executable", str(self.root / "pythonw.exe")):
            command = app_runtime.script_command("program.py")
        self.assertEqual(Path(command[0]).name, "python.exe")
        self.assertIn("-I", command)

    def test_first_launch_copies_templates_and_upgrade_preserves_edits(self):
        resources = self.root / "resources"
        resources.mkdir()
        template = resources / "custom_nodes.json"
        template.write_text('{"initial":true}', encoding="utf-8")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(resources), create=True), patch.object(sys, "platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "profile")}):
            saved = app_runtime.custom_library_path()
            self.assertEqual(saved.read_text(encoding="utf-8"), '{"initial":true}')
            saved.write_text('{"user_edited":true}', encoding="utf-8")
            template.write_text('{"new_release":true}', encoding="utf-8")
            self.assertEqual(app_runtime.custom_library_path().read_text(encoding="utf-8"), '{"user_edited":true}')
            self.assertEqual(app_runtime.library_store_path().parent, saved.parent)
            self.assertEqual(app_runtime.project_directory().parent, saved.parent)
        self.assertEqual(template.read_text(encoding="utf-8"), '{"new_release":true}')

    def test_external_sdk_does_not_inherit_bundled_python_or_tcl_settings(self):
        with patch.dict(os.environ, {"PYTHONHOME": "bundle", "PYTHONPATH": "bundle", "TCL_LIBRARY": "bundle", "TK_LIBRARY": "bundle", "_PYI_APPLICATION_HOME_DIR": "bundle"}):
            result = app_runtime.external_python_environment()
        for key in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "_PYI_APPLICATION_HOME_DIR"):
            self.assertNotIn(key, result)
        self.assertEqual(result["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_desktop_executable_is_rejected_as_build_python(self):
        fake = self.root / "PyDropRuntime.exe"
        fake.touch()
        with self.assertRaisesRegex(ValueError, "Python interpreter"):
            app_runtime.resolve_build_python(str(fake))

    def test_frozen_csv_prefers_editable_external_data_then_bundled_data(self):
        external, bundled = self.root / "external", self.root / "bundle"
        external.mkdir()
        bundled.mkdir()
        (bundled / "input.csv").write_text("value\n1\n", encoding="utf-8")
        with patch.object(data_runtime, "_DATA_BASE", external), patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(bundled), create=True):
            self.assertEqual(data_runtime._data_path("input.csv", "CSV Input"), bundled / "input.csv")
            self.assertEqual(data_runtime._data_path("input.csv", "CSV Output"), external / "input.csv")
            (external / "input.csv").write_text("value\n2\n", encoding="utf-8")
            self.assertEqual(data_runtime._data_path("input.csv", "CSV Input"), external / "input.csv")

    def test_runtime_self_test_uses_real_graph_subprocesses(self):
        from runtime_entry import self_test
        report = self_test()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["checks"]["simulation"])
        self.assertTrue(report["checks"]["input"])
        self.assertTrue(report["checks"]["compiler_resources"])
