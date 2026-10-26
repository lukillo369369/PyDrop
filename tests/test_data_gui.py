"""Data workflows through the editor's real worker process and Tk event loop."""
import csv
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from example_graphs import experimental_measurements_demo
from graph_core import Graph
from nodeforge import Editor


class DataEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, experimental_measurements_demo(), path=self.directory / "lesson.drop.json")
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        self.app.closed = True
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
            self.app.process.wait()
        for window in self.app.data_windows:
            if window.winfo_exists():
                window.destroy()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def pump_until(self, condition, seconds=8):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition(), "Tk worker did not complete")

    def test_experiment_inspectors_views_labels_and_full_export(self):
        for node in self.app.graph.nodes.values():
            self.assertTrue(self.app.select(node.id))
            self.root.update()
        self.app.run_graph()
        self.pump_until(lambda: not self.app.busy)
        self.assertIn("Finished successfully", self.app.output_text.get("1.0", "end"))
        self.assertEqual(len(self.app.data_windows), 3)
        line = next(window for window in self.app.data_windows if window._data_payload["kind"] == "line")
        self.assertEqual(line._data_payload["y_label"], "Power (W)")
        self.assertAlmostEqual(line._data_payload["y"][-1], 3.0)
        source = next(node for node in self.app.graph.nodes.values() if node.kind == "CSV Input")
        self.app.select(source.id)
        self.assertIn("voltage_V", self.app.column_widgets[0].cget("values"))
        self.assertEqual(str(self.app.data_inspect_button.cget("state")), "normal")
        self.app.inspect_data(source.id)
        self.assertEqual(len(self.app.data_windows), 4)
        with (self.directory / "experimental_results.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 6)
        self.assertAlmostEqual(float(rows[-1]["power_W"]), 3.0)

    def test_header_discovery_runs_off_tk_thread_and_accepts_tab(self):
        source = next(node for node in self.app.graph.nodes.values() if node.kind == "CSV Input")
        path = self.directory / "tab.csv"
        path.write_text("time\treading\n0\t1\n", encoding="utf-8")
        self.app.select(source.id)
        self.app.form_vars["path"].set(str(path))
        self.app.form_vars["delimiter"].set(r"\t")
        original_open = Path.open
        def slow_open(target, *args, **kwargs):
            if target == path:
                time.sleep(.3)
            return original_open(target, *args, **kwargs)
        beats = []
        self.root.after(25, lambda: beats.append(True))
        with patch.object(Path, "open", slow_open):
            started = time.monotonic()
            self.app.read_csv_columns(source)
            self.assertLess(time.monotonic() - started, .2)
            self.pump_until(lambda: "reading" in self.app.column_widgets[0].cget("values"))
        self.assertEqual(beats, [True])
        self.assertTrue(self.app.form_dirty)

    def test_import_does_not_block_tk_and_data_error_selects_source(self):
        original_compile = self.app.compile_project
        def slow_compile(*args, **kwargs):
            compiled = original_compile(*args, **kwargs)
            start = compiled.code.index("def _data_read(")
            insertion = compiled.code.index("\n", start) + 1
            compiled.code = compiled.code[:insertion] + "    import time as _delay; _delay.sleep(.35)\n" + compiled.code[insertion:]
            return compiled
        beats = []
        self.root.after(40, lambda: beats.append(self.app.busy))
        with patch.object(self.app, "compile_project", side_effect=slow_compile):
            self.app.run_graph()
            self.pump_until(lambda: not self.app.busy)
        self.assertEqual(beats, [True])
        source = next(node for node in self.app.graph.nodes.values() if node.kind == "CSV Input")
        source.props["path"] = "missing.csv"
        self.app.run_graph()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(self.app.error_node, source.id)
        self.assertIn("was not found", self.app.status_var.get())

    def test_function_data_views_open_from_project_run(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        definition = graph.add_function("measure", [], ["done"])
        body = Graph.from_dict(definition["graph"], _body=True)
        source = body.add_node("CSV Input", 300, 0)
        source.props["path"] = str(Path(__file__).resolve().parents[1] / "examples" / "experimental_measurements.csv")
        table = body.add_node("Data Table", 600, 0)
        entry = next(node for node in body.nodes.values() if node.kind == "Main")
        result = next(node for node in body.nodes.values() if node.kind == "Return")
        body.connect(entry.id, "next", source.id, "run")
        body.connect(source.id, "next", table.id, "run")
        body.connect(table.id, "next", result.id, "run")
        body.connect(source.id, "data", table.id, "data")
        definition["graph"] = body.to_dict()
        call = graph.add_call("measure", 300, 0)
        graph.connect(main.id, "next", call.id, "run")
        self.app.replace_graph(graph, self.directory / "function.drop.json")
        self.app.run_graph()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(len(self.app.data_windows), 1)
        self.assertEqual(self.app.data_windows[0]._data_payload["row_count"], 6)
