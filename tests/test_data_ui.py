"""Data viewers preserve labels and remain bounded during resize and close."""
import math
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_ui


class AxisTests(unittest.TestCase):
    def test_constant_negative_zero_and_extreme_domains(self):
        for values in ([0, 0], [-4, -4], [-10, 0, 20], [1e308, -1e308], [5e-324, 5e-324]):
            with self.subTest(values=values):
                low, high, ticks, scale = data_ui._data_axis(values)
                self.assertLess(low, high)
                self.assertTrue(all(math.isfinite(value) for value in (low, high, scale)))
                self.assertTrue(2 <= len(ticks) <= 10)
                self.assertTrue(all(low <= value / scale <= high for value in values))
                self.assertTrue(all(data_ui._data_number(tick, scale) for tick in ticks))


@unittest.skipUnless(sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY"), "No desktop display")
class DataViewerTests(unittest.TestCase):
    def setUp(self):
        self.root = data_ui._data_tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if data_ui._data_root is not None:
            for window in list(data_ui._data_windows):
                window.destroy()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def render(self):
        self.root.update()
        time.sleep(.09)
        self.root.update()

    def test_read_only_table_handles_duplicate_headers_and_bounds_preview(self):
        window = data_ui._data_display({"type": "table", "columns": ["Force", "Force"],
            "rows": [[index, None] for index in range(300)], "row_count": 8000,
            "column_count": 9, "title": "Measured forces"}, parent=self.root)
        self.root.update()
        table = window._data_table
        self.assertEqual(window.title(), "Measured forces")
        self.assertEqual(len(table.get_children()), 200)
        self.assertEqual(table.item(table.get_children()[0], "values"), ("0", ""))
        self.assertEqual([table.heading(column, "text") for column in table["columns"]], ["Force", "Force"])
        self.assertTrue(table.cget("xscrollcommand"))
        self.assertTrue(table.cget("yscrollcommand"))
        labels = [widget.cget("text") for widget in window.winfo_children()[0].winfo_children()
                  if isinstance(widget, data_ui._data_ttk.Label)]
        self.assertTrue(any("first 2 of 9 columns" in text for text in labels))
        window.destroy()

    def test_plot_labels_and_resizing_do_not_accumulate_items(self):
        window = data_ui._data_display({"type": "plot", "kind": "scatter", "x": list(range(2000)),
            "y": [-3] * 2000, "row_count": 50000, "title": "Experiment",
            "x_label": "Time (s)", "y_label": "Velocity (m/s)"}, parent=self.root)
        self.render()
        canvas = window._data_canvas
        original_count = len(canvas.find_all())
        self.assertGreater(original_count, 2000)
        labels = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
        self.assertIn("Time (s)", labels)
        self.assertIn("Velocity (m/s)", labels)
        for size in ("950x620", "700x430", "840x540"):
            window.geometry(size)
            self.render()
            self.assertEqual(len(canvas.find_all()), original_count)
        window.destroy()

    def test_line_uses_one_path_and_handles_negative_values(self):
        window = data_ui._data_display({"kind": "line", "x": [-3, -2, -1],
                                      "y": [-1, -4, -9]}, parent=self.root)
        self.render()
        canvas = window._data_canvas
        self.assertEqual(sum(canvas.type(item) == "oval" for item in canvas.find_all()), 3)
        paths = [item for item in canvas.find_all() if canvas.type(item) == "line"
                 and canvas.itemcget(item, "fill") == "#2563b9"]
        self.assertEqual(len(paths), 1)
        window.destroy()

    def test_standalone_last_window_closes_root_and_mainloop(self):
        first = data_ui._data_display({"kind": "line", "x": [1], "y": [2]})
        second = data_ui._data_display({"type": "table", "columns": ["A"], "rows": [[1]]})
        root = data_ui._data_root
        first.destroy()
        self.assertIs(data_ui._data_root, root)
        root.after(50, second.destroy)
        data_ui._data_show()
        self.assertIsNone(data_ui._data_root)
        self.assertFalse(data_ui._data_windows)
        data_ui._data_show()

    def test_close_with_pending_resize_cancels_callback(self):
        window = data_ui._data_display({"kind": "scatter", "x": [1], "y": [2]}, parent=self.root)
        self.root.update()
        window.destroy()
        self.render()

    def test_embedded_source_survives_user_function_module_names(self):
        namespace = {}
        source = Path(data_ui.__file__).read_text(encoding="utf-8")
        exec("exported_program = True\n" + source, namespace)
        namespace.update(tk=lambda: 0, ttk=lambda: 0, math=lambda: 0)
        window = namespace["_data_display"]({"kind": "line", "x": [1, 2], "y": [3, 4]},
                                            parent=self.root)
        self.render()
        self.assertTrue(window._data_canvas.find_all())
        window.destroy()


if __name__ == "__main__":
    unittest.main()
