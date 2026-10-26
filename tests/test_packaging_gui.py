"""Executable reports run outside Tk and retain editable project snapshots."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from example_graphs import sensor_dashboard_demo
from nodeforge import Editor


class PackagingEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, sensor_dashboard_demo(), path=self.directory / "source.drop.json")
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        self.app.closed = True
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump_until(self, condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition(), self.app.packaging_result.get("1.0", "end"))

    def test_dependency_report_preserves_project_and_runs_in_background(self):
        before = self.app.snapshot()
        self.app.open_executable_builder()
        self.app.packaging_vars["output"].set(str(self.directory / "packages"))
        self.app.packaging_vars["name"].set("Sensor demo")
        beats = []
        self.root.after(20, lambda: beats.append(True))
        self.app.start_executable_build(True)
        self.pump_until(lambda: "Completed." in self.app.packaging_result.get("1.0", "end") or "needs attention" in self.app.packaging_result.get("1.0", "end"))
        self.assertIn("Completed.", self.app.packaging_result.get("1.0", "end"))
        self.assertEqual(beats, [True])
        self.assertEqual(self.app.snapshot(), before)
        reports = list((self.directory / "packages" / "dependency-reports").glob("*/dependency-report.json"))
        self.assertEqual(len(reports), 1)
        self.assertTrue(list(reports[0].parent.glob("source/*.drop.json")))

    def test_missing_build_python_is_explained_without_modifying_graph(self):
        before = self.app.snapshot()
        self.app.open_executable_builder()
        self.app.packaging_vars["python"].set("")
        self.app.start_executable_build()
        self.assertIn("build interpreter", self.app.packaging_result.get("1.0", "end"))
        self.assertEqual(self.app.snapshot(), before)
