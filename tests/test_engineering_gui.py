"""Focused editor checks for unit-aware nodes; requires a desktop display."""
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from example_graphs import EXAMPLES, engineering_force_demo
from graph_core import Graph
from nodeforge import Editor


@unittest.skipUnless(sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY"), "No desktop display")
class EngineeringEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, engineering_force_demo())
        self.root.update()
        self.app.fit_view()

    def tearDown(self):
        self.app.closed = True
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def run_and_wait(self):
        self.app.run_graph()
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.assertIn("Finished successfully.", self.app.output_text.get("1.0", "end"))

    def test_all_engineering_lessons_draw_inspect_and_run(self):
        expected = {"engineering_force": "24.525 N", "torque": "25 N*m",
                    "ohms_law": "500 V", "electrical_power": "6 W"}
        for slug, title, factory in EXAMPLES:
            if slug not in expected:
                continue
            with self.subTest(example=slug):
                self.app.replace_graph(factory())
                for node in self.app.graph.nodes.values():
                    self.assertTrue(self.app.select(node.id))
                    self.root.update()
                self.run_and_wait()
                self.assertIn(expected[slug], self.app.output_text.get("1.0", "end"))

    def test_property_mismatch_does_not_change_live_graph(self):
        mass = next(node for node in self.app.graph.nodes.values()
                    if node.kind == "Quantity" and node.props["unit"] == "g")
        self.app.select(mass.id)
        self.app.form_vars["unit"].set("s")
        with patch("tkinter.messagebox.showerror") as message:
            self.assertFalse(self.app.apply_properties())
        self.assertEqual(mass.props["unit"], "g")
        self.assertIn("Force.mass", message.call_args.args[1])
        self.assertIn("expected mass, received time", self.app.status_var.get())
        self.app.form_vars["unit"].set("kg")
        self.assertTrue(self.app.apply_properties())
        self.assertEqual(mass.props["unit"], "kg")

    def test_canvas_rejected_wire_keeps_existing_wire_and_explains_it(self):
        force = next(node for node in self.app.graph.nodes.values() if node.kind == "Force")
        mass = next(node for node in self.app.graph.nodes.values()
                    if node.kind == "Quantity" and node.props["unit"] == "g")
        before = self.app.graph.edges[:]
        self.app.pending = (mass.id, "out", "result")
        x, y = self.app.pin_position(force, "in", "acceleration")
        self.app.canvas_press(SimpleNamespace(x=x, y=y))
        self.assertEqual(self.app.graph.edges, before)
        self.assertIn("expected acceleration, received mass", self.app.status_var.get())

    def test_picker_adds_nodes_with_editable_unit_fields(self):
        self.app.replace_graph(Graph())
        self.app.search_anchor = (0, 0)
        self.app.open_node_picker("Quantity")
        self.app.accept_node_picker()
        node = self.app.graph.nodes[self.app.selected]
        self.assertEqual(node.kind, "Quantity")
        self.app.form_vars["unit"].set("kg*m/s^2")
        self.assertTrue(self.app.apply_properties())
        self.assertEqual(node.props["unit"], "kg*m/s^2")
        self.assertEqual(self.app.runtime_advice("Traceback...\nUnitError: Cannot convert time to m (length)."),
                         "Cannot convert time to m (length).")

    def test_engineering_names_including_apostrophe_are_searchable(self):
        for kind in ("Quantity", "Unit Convert", "Force", "Torque", "Ohm's Law", "Electrical Power"):
            with self.subTest(kind=kind):
                self.app.search_anchor = (0, 0)
                self.app.open_node_picker(kind)
                self.assertFalse(self.app.picker_code)
                self.assertEqual(self.app.picker_matches[0], kind)
                self.app.accept_node_picker()
                self.assertEqual(self.app.graph.nodes[self.app.selected].kind, kind)


if __name__ == "__main__":
    unittest.main()
