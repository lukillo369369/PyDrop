"""Standalone graph builds preserve editable inputs and explain missing dependencies."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import graph_packaging as packaging
from graph_core import demo_graph


class GraphPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "a folder with spaces"
        self.folder.mkdir()

    def script(self, text="print('standalone')\n", name="export.py"):
        path = self.folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_analysis_detects_imports_without_executing_source_or_importing_parent(self):
        sentinel = self.folder / "executed.txt"
        package = self.folder / "untrusted_package"
        package.mkdir()
        (package / "__init__.py").write_text(f"raise RuntimeError({str(sentinel)!r})\n", encoding="utf-8")
        (package / "submodule.py").write_text("", encoding="utf-8")
        source = self.script("import math\nfrom tkinter import ttk\nimport untrusted_package.submodule\n"
                             "import importlib as loader\nloader.import_module('json.decoder')\n"
                             "loader.import_module(user_selected_name)\n"
                             f"open({str(sentinel)!r}, 'w').write('executed')\n")
        report = packaging.dependency_report(source)
        imports = {item["module"]: item for item in report["imports"]}
        self.assertFalse(sentinel.exists())
        self.assertNotIn("untrusted_package", sys.modules)
        self.assertTrue(imports["untrusted_package.submodule"]["available"])
        self.assertEqual(imports["untrusted_package.submodule"]["kind"], "local")
        self.assertEqual(imports["math"]["kind"], "stdlib")
        self.assertTrue(imports["json.decoder"]["hidden_import"])
        self.assertTrue(any("Dynamic import" in warning for warning in report["warnings"]))

    def test_simulation_serial_is_optional_but_hardware_and_direct_imports_are_required(self):
        runtime = "import importlib as _serial_importlib\ndef _serial_library():\n    return _serial_importlib.import_module('serial')\n"
        simulated = self.script(runtime + "_serial_connect('SIMULATED', 9600, 1.0, 'Simulation')\n")
        hardware = self.script(runtime + "_serial_connect('COM3', 9600, 1.0, 'Hardware')\n", "hardware.py")
        with mock.patch.object(packaging, "_module_available", side_effect=lambda name, base: name != "serial"):
            sim_report = packaging.dependency_report(simulated)
            hard_report = packaging.dependency_report(hardware)
            direct_report = packaging.dependency_report(self.script("import serial\n", "direct.py"))
        self.assertNotIn("serial", sim_report["missing_required"])
        self.assertIn("serial", hard_report["missing_required"])
        self.assertIn("serial", direct_report["missing_required"])
        serial = next(item for item in hard_report["imports"] if item["module"] == "serial")
        self.assertIn("pyserial", [item["name"] for item in serial["distributions"]])

    def test_distribution_versions_and_explicit_hidden_import_are_reported(self):
        source = self.script("import example_module\n")
        with mock.patch.object(packaging.importlib.metadata, "packages_distributions", return_value={"example_module": ["example-package"]}), \
                mock.patch.object(packaging.importlib.metadata, "version", return_value="2.4.6"), \
                mock.patch.object(packaging, "_module_available", return_value=True):
            report = packaging.dependency_report(source, hidden_imports=["example_module.plugin"])
        modules = {item["module"]: item for item in report["imports"]}
        self.assertEqual(modules["example_module"]["distributions"], [{"name": "example-package", "version": "2.4.6"}])
        self.assertIn("example_module.plugin", report["hidden_imports"])

    def test_report_only_preserves_exact_python_and_editable_project_without_build_tool(self):
        source = self.script("# comment and editable code\nprint('kept')\n")
        graph = self.folder / "original.drop.json"
        demo_graph().save(graph)
        original_bytes, graph_bytes = source.read_bytes(), graph.read_bytes()
        with mock.patch.object(packaging.subprocess, "run", side_effect=AssertionError("report invoked a subprocess")):
            result = packaging.build_executable(source, project=graph, name="My Graph", output_dir=self.folder / "release", report_only=True)
        folder = Path(result["output_dir"])
        self.assertEqual((folder / "source" / source.name).read_bytes(), original_bytes)
        self.assertEqual((folder / "source" / graph.name).read_bytes(), graph_bytes)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(graph.read_bytes(), graph_bytes)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["editable_project"]["sha256"], hashlib.sha256(graph_bytes).hexdigest())
        self.assertIsNone(result["executable"])

    def test_editable_graph_source_is_compiled_but_never_executed(self):
        graph = self.folder / "force.drop.json"
        demo_graph().save(graph)
        with mock.patch.object(packaging.subprocess, "run", side_effect=AssertionError("report invoked a subprocess")):
            result = packaging.build_executable(graph, output_dir=self.folder / "reports", report_only=True)
        source = Path(result["output_dir"]) / "source"
        self.assertTrue((source / "force.py").is_file())
        self.assertEqual((source / graph.name).read_bytes(), graph.read_bytes())
        self.assertIn("def main():", (source / "force.py").read_text(encoding="utf-8"))

    def test_missing_import_stops_build_and_keeps_dependency_report_and_error_log(self):
        source = self.script("import nonexistent_pydrop_test_requirement_5762\n")
        with mock.patch.object(packaging.subprocess, "run", side_effect=AssertionError("missing deps reached PyInstaller")):
            with self.assertRaises(packaging.BuildError) as caught:
                packaging.build_executable(source, output_dir=self.folder / "release")
        failure = caught.exception
        self.assertIn("Missing required Python modules", str(failure))
        report = json.loads(Path(failure.report_path).read_text(encoding="utf-8"))
        self.assertIn("nonexistent_pydrop_test_requirement_5762", report["missing_required"])
        self.assertIn("Missing required", Path(failure.log_path).read_text(encoding="utf-8"))
        self.assertFalse((self.folder / "release" / "export").exists())
        self.assertTrue(source.exists())

    def test_build_failure_retains_tool_output_and_useful_hint(self):
        source = self.script()

        def fail(command, **kwargs):
            kwargs["stdout"].write("ModuleNotFoundError: No module named 'plugin'\n")
            return subprocess.CompletedProcess(command, 1)

        with mock.patch.object(packaging.importlib.metadata, "version", return_value="6.22.3"), \
                mock.patch.object(packaging.subprocess, "run", side_effect=fail):
            with self.assertRaises(packaging.BuildError) as caught:
                packaging.build_executable(source, output_dir=self.folder / "release")
        self.assertIn("--hidden-import", str(caught.exception))
        log = Path(caught.exception.log_path).read_text(encoding="utf-8")
        self.assertIn("ModuleNotFoundError", log)
        self.assertIn("Command (JSON argv)", log)

    def test_success_publishes_complete_output_with_console_and_paths_with_spaces(self):
        source = self.script()
        asset = self.folder / "sensor measurements.csv"
        asset.write_text("a,b\n1,2\n", encoding="utf-8")
        seen = []

        def succeed(command, **kwargs):
            seen.append(command)
            output = Path(command[command.index("--distpath") + 1])
            output.mkdir(parents=True)
            suffix = ".exe" if sys.platform == "win32" else ""
            (output / ("My Sensor" + suffix)).write_bytes(b"a mock executable")
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(packaging.importlib.metadata, "version", return_value="6.22.3"), \
                mock.patch.object(packaging.subprocess, "run", side_effect=succeed):
            result = packaging.build_executable(source, name="My Sensor", output_dir=self.folder / "release folder",
                                                add_data=[(asset, "sensor data")])
        self.assertTrue(Path(result["executable"]).is_file())
        self.assertTrue(Path(result["report"]).is_file())
        self.assertIn("--console", seen[0])
        self.assertIn("My Sensor", seen[0])
        self.assertEqual(len([item for item in seen[0] if item.endswith("export.py")]), 1)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["assets"][0]["destination"], "sensor data")
        self.assertTrue((Path(result["output_dir"]) / manifest["assets"][0]["preserved"]).is_file())
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["bundled_assets"][0]["sha256"], hashlib.sha256(asset.read_bytes()).hexdigest())

    def test_existing_build_and_source_are_never_overwritten(self):
        source = self.script()
        output = self.folder / "release"
        existing = output / "export"
        existing.mkdir(parents=True)
        sentinel = existing / "keep.txt"
        sentinel.write_text("old build", encoding="utf-8")
        with self.assertRaisesRegex(packaging.BuildError, "already exists"):
            packaging.build_executable(source, output_dir=output, report_only=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "old build")
        self.assertEqual(source.read_text(encoding="utf-8"), "print('standalone')\n")

    def test_unsafe_asset_destinations_and_missing_assets_fail_with_logs(self):
        source = self.script()
        for dest in ("../escape", "C:/absolute", "/absolute"):
            with self.subTest(dest=dest), self.assertRaisesRegex(packaging.BuildError, "relative directory") as caught:
                packaging.build_executable(source, name="Asset", output_dir=self.folder / "release",
                                            report_only=True, add_data=[(source, dest)])
            self.assertTrue(Path(caught.exception.log_path).is_file())

    def test_cli_rejects_missing_build_python_without_running_any_source(self):
        source = self.script("raise RuntimeError('must never execute')\n")
        with self.assertRaises(SystemExit) as caught, mock.patch("sys.stderr"):
            packaging.main([str(source), "--python", str(self.folder / "absent python.exe")])
        self.assertEqual(caught.exception.code, 2)

    def test_python_selection_delegates_report_and_build_to_same_interpreter(self):
        source = self.script("raise RuntimeError('must never execute')\n")
        interpreter = self.folder / "selected Python.exe"
        interpreter.write_bytes(b"a placeholder for the test")
        with mock.patch.object(packaging.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            code = packaging.main([str(source), "--python", str(interpreter), "--report-only", "--name", "Sensor Report"])
        self.assertEqual(code, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(interpreter))
        self.assertIn("--report-only", command)
        self.assertIn("Sensor Report", command)
        self.assertNotIn("--python", command)

    def test_cross_compiling_windows_is_rejected_with_platform_requirement(self):
        source = self.script()
        with mock.patch.object(packaging.sys, "platform", "linux"):
            with self.assertRaisesRegex(packaging.BuildError, "Windows executables must be built on Windows"):
                packaging.build_executable(source, output_dir=self.folder / "release", target_platform="windows")


if __name__ == "__main__":
    unittest.main()
