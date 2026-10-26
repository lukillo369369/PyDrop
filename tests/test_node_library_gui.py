"""Desktop checks for authoring, review/install boundaries, and library nodes."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph
from library_ui import open_library_manager
from node_libraries import LibraryCatalog, install_library, make_reference, save_library
from nodeforge import Editor
from test_node_libraries import make_graph, sample_library


@unittest.skipUnless(sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY"), "No desktop display")
class LibraryEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, custom_library_path=self.directory / "formulas.json",
                          library_store_path=self.directory / "installed")
        self.root.update()

    def tearDown(self):
        self.app.closed = True
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_author_browse_install_search_inspect_and_run(self):
        manager = open_library_manager(self.app)
        self.root.update()
        self.assertIn("Executable Python", manager.window.title())
        manager.notebook.select(1)
        self.root.update()
        manager.author_canvas.yview_moveto(1)
        self.root.update()
        self.assertTrue(manager.save_button.winfo_ismapped())
        self.assertLessEqual(manager.save_button.winfo_rooty() + manager.save_button.winfo_height(),
                             manager.author_canvas.winfo_rooty() + manager.author_canvas.winfo_height() + 2)
        marker = self.directory / "executed.txt"
        source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n" + manager.implementation.get("1.0", "end-1c")
        manager.implementation.delete("1.0", "end")
        manager.implementation.insert("1.0", source)
        manager.fields["category"].set("Signal / Control")
        path = self.directory / "authored.pydrop-lib.json"
        manager.save_authored(path)
        self.assertTrue(path.exists())
        self.assertEqual(self.app.library_nodes, {})
        manager.browse(path)
        self.assertEqual(self.app.library_nodes, {})
        self.assertFalse(marker.exists())
        self.assertIn("Python body:", manager.preview_text.get("1.0", "end"))
        manager.install_button.invoke()  # The explicit consent action.
        self.assertEqual(len(self.app.library_nodes), 1)
        self.assertFalse(marker.exists())
        self.app.search_anchor = (0, 0)
        self.app.open_node_picker("Signal / Control")
        self.assertFalse(self.app.picker_code)
        self.assertEqual(len(self.app.picker_matches), 1)
        key = self.app.picker_matches[0]
        graph, node, out = make_graph(self.app.library_nodes[key])
        self.app.replace_graph(graph)
        self.app.select(node.id)
        self.assertEqual(set(self.app.form_vars), {"input:value", "input:factor"})
        self.app.form_vars["input:value"].set("4")
        self.assertTrue(self.app.apply_properties())
        self.assertFalse(marker.exists())
        self.app.run_graph()
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.assertIn("Scaled: 8", self.app.output_text.get("1.0", "end"))
        self.assertEqual(marker.read_text(), "ran")

    def test_open_project_missing_library_warns_without_executing(self):
        library = save_library(self.directory / "tools.pydrop-lib.json", sample_library())
        graph, node, _ = make_graph(make_reference(library, library["nodes"][0]))
        path = self.directory / "project.drop.json"
        graph.save(path)
        with patch("tkinter.filedialog.askopenfilename", return_value=str(path)), patch("tkinter.messagebox.showwarning") as warning:
            self.app.open_project()
        self.assertIn("Missing library test-tools@1.0.0", warning.call_args.args[1])
        self.assertEqual(self.app.graph.to_dict(), graph.to_dict())
        self.app.select(node.id)
        self.root.update()
        self.assertEqual(self.app.current_code, "")
        self.assertIn("explicitly install", self.app.status_var.get())
        self.assertFalse((self.directory / "installed").exists())

    def test_open_project_reports_missing_python_module(self):
        data = sample_library()
        data["requirements"] = [{"module":"pydrop_missing_dependency_for_test", "package":"pydrop-missing-package"}]
        library = save_library(self.directory / "tools.pydrop-lib.json", data)
        install_library(self.directory / "tools.pydrop-lib.json", self.app.library_catalog.store, allow_executable=True)
        self.app.reload_libraries()
        graph, _, _ = make_graph(make_reference(library, library["nodes"][0]))
        path = self.directory / "project.drop.json"
        graph.save(path)
        with patch("tkinter.filedialog.askopenfilename", return_value=str(path)), patch("tkinter.messagebox.showwarning") as warning:
            self.app.open_project()
        self.assertIn("pydrop_missing_dependency_for_test", warning.call_args.args[1])
        self.assertIn("pydrop-missing-package", warning.call_args.args[1])
        self.assertEqual(self.app.current_code, "")

    def test_changed_preview_cannot_be_installed(self):
        path = self.directory / "tools.pydrop-lib.json"
        library = save_library(path, sample_library())
        manager = open_library_manager(self.app)
        manager.browse(path)
        library["nodes"][0]["implementation"] += "\n# changed after review"
        save_library(path, library)
        manager.install_button.invoke()
        self.assertIn("changed after preview", manager.preview_status.get())
        self.assertEqual(LibraryCatalog(self.app.library_catalog.store).palette(), {})

    def test_large_implementations_run_without_command_line_limit(self):
        data = sample_library()
        data["nodes"][0]["implementation"] = "# " + "padding" * 2500 + "\n" + data["nodes"][0]["implementation"]
        second = dict(data["nodes"][0], id="second", name="Second implementation")
        data["nodes"].append(second)
        path = self.directory / "large.pydrop-lib.json"
        library = save_library(path, data)
        install_library(path, self.app.library_catalog.store, allow_executable=True)
        self.app.reload_libraries()
        graph, first, out = make_graph(make_reference(library, library["nodes"][0]))
        extra = graph.add_library_node(make_reference(library, library["nodes"][1]), 0, 300)
        graph.connect(first.id, "next", extra.id, "run")
        graph.connect(extra.id, "next", out.id, "run")
        self.app.replace_graph(graph)
        self.assertGreater(len(self.app.current_code), 32767)
        self.app.run_graph()
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.assertIn("Finished successfully.", self.app.output_text.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
