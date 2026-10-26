"""Desktop interaction tests. Requires Tkinter and a display; otherwise skipped."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, demo_graph


@unittest.skipUnless(sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY"), "No desktop display")
class EditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from nodeforge import Editor
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root)
        self.pump(.12)
        self.app.fit_view()
        self.root.update()

    def tearDown(self):
        self.app.closed = True
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.assertEqual(self.errors, [], str(self.errors))

    def pump(self, seconds=.03):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.root.update()
            time.sleep(.005)

    def click(self, x, y):
        self.app.canvas.event_generate("<ButtonPress-1>", x=round(x), y=round(y))
        self.app.canvas.event_generate("<ButtonRelease-1>", x=round(x), y=round(y))
        self.root.update()

    def run_and_wait(self):
        self.app.run_graph()
        until = time.monotonic() + 7
        while self.app.busy and time.monotonic() < until:
            self.pump(.03)
        self.assertFalse(self.app.busy)
        return self.app.output_text.get("1.0", "end")

    def test_initial_graph_draws(self):
        self.assertEqual(len(self.app.node_hits), 6)
        self.assertEqual(len(self.app.edge_items), 6)
        self.assertIn("def calculate_force", self.app.code_text.get("1.0", "end"))

    def test_reusable_function_double_click_edit_apply_undo_and_reopen(self):
        from example_graphs import reusable_rectangle_demo
        self.app.replace_graph(reusable_rectangle_demo())
        call = next(n for n in self.app.graph.nodes.values() if n.kind == "Function Call")
        self.app.fit_view()
        x, y = self.app.transform(call.x+100, call.y+20)
        self.click(x, y)
        self.click(x, y)
        self.pump(.15)
        editor = self.app.function_editor
        self.assertIsNotNone(editor)
        self.assertEqual(editor.function_name, "rectangle_metrics")
        self.assertIn("Function: rectangle_metrics", editor.root.title())
        self.assertIs(editor.root.grab_current(), editor.root)
        before = self.app.snapshot()
        editor.select("n4")
        editor.form_vars["operator"].set("+")
        self.assertTrue(editor.save_project())
        self.assertNotEqual(self.app.snapshot(), before)
        self.assertNotEqual(self.app.snapshot(), self.app.saved_state)
        editor.close()
        self.assertIsNone(self.app.function_editor)
        self.pump(.15)
        text = self.run_and_wait()
        self.assertIn("First area: 5.0", text)
        self.assertIn("Second area: 9.0", text)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)
        self.app.redo()
        self.app.open_function("rectangle_metrics")
        self.assertEqual(self.app.function_editor.graph.nodes["n4"].props["operator"], "+")
        self.app.function_editor.close()

    def test_function_creation_dialog_opens_editable_body(self):
        self.app.new_function_dialog()
        self.root.update()
        dialog = next(child for child in self.root.winfo_children()
                      if child.winfo_class() == "Toplevel" and child.title() == "New reusable function")
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        create = next(widget for widget in descendants(dialog)
                      if widget.winfo_class() == "Button" and widget.cget("text") == "Create & open graph")
        self.assertTrue(create.winfo_ismapped())
        create.invoke()
        self.pump(.15)
        self.assertIn("calculate_total", self.app.graph.functions)
        editor = self.app.function_editor
        self.assertEqual(editor.function_name, "calculate_total")
        self.assertEqual(editor.graph.nodes["n2"].props["names"], ["price", "quantity"])
        self.assertEqual(editor.graph.nodes["n3"].props["values"][0]["name"], "total")
        editor.close()

    def test_function_creation_named_pins_call_defaults_and_saved_project(self):
        from graph_core import ports
        self.app.create_reusable_function("shared_calculation", ["price", "quantity"], ["total", "tax"], open_editor=False)
        self.app.code_connect_var.set("After Main")
        self.app.add_function_call("shared_calculation")
        call = self.app.graph.nodes[self.app.selected]
        self.assertEqual(call.kind, "Function Call")
        self.assertEqual(list(ports(call)[0]), ["run", "price", "quantity"])
        self.assertEqual(list(ports(call)[1]), ["next", "total", "tax"])
        self.app.form_vars["input:price"].set("12.5")
        self.assertTrue(self.app.apply_properties())
        self.assertEqual(call.props["inputs"][0]["default"], "12.5")
        with tempfile.TemporaryDirectory() as directory:
            self.app.path = Path(directory)/"functions.drop.json"
            self.assertTrue(self.app.save_project())
            loaded = Graph.load(self.app.path)
            self.app.replace_graph(loaded, self.app.path)
            self.app.open_function("shared_calculation")
            self.assertEqual(self.app.function_editor.graph.nodes["n2"].props["names"], ["price", "quantity"])
            self.app.function_editor.close()

    def test_function_draft_discard_and_run_value_scope(self):
        from example_graphs import reusable_rectangle_demo
        self.app.replace_graph(reusable_rectangle_demo())
        before = self.app.snapshot()
        self.app.open_function("rectangle_metrics")
        editor = self.app.function_editor
        editor.graph.nodes["n4"].props["operator"] = "+"
        editor.refresh()
        editor.run_graph()
        until = time.monotonic()+7
        while editor.busy and time.monotonic() < until:
            self.pump(.03)
        self.assertFalse(editor.busy)
        self.assertIn("Second area: 9.0", editor.output_text.get("1.0", "end"))
        self.assertIn("n4", editor.run_values)
        self.assertEqual(editor.run_values["n4"]["value"], "9.0")
        self.assertEqual(self.app.snapshot(), before)
        with patch("tkinter.messagebox.askyesnocancel", return_value=False):
            editor.close()
        self.assertEqual(self.app.snapshot(), before)

    def test_function_branch_return_palette_and_nested_editor_navigation(self):
        self.app.create_reusable_function("first_function", ["item"], ["answer"], open_editor=False)
        self.app.create_reusable_function("second_function", [], ["result"], open_editor=False)
        self.app.open_function("first_function")
        editor = self.app.function_editor
        editor.add_node("Return", 700, 300)
        self.assertEqual(editor.graph.nodes[editor.selected].props["values"][0]["name"], "answer")
        editor.add_function_call("second_function")
        self.assertEqual(editor.graph.nodes[editor.selected].kind, "Function Call")
        editor.open_function("second_function")
        nested = editor.function_editor
        self.assertEqual(nested.function_name, "second_function")
        nested.close()
        self.assertIs(editor.root.grab_current(), editor.root)
        with patch("tkinter.messagebox.askyesnocancel", return_value=True):
            editor.close()
        body = Graph.from_dict(self.app.graph.functions["first_function"]["graph"])
        self.assertTrue(any(n.kind == "Function Call" for n in body.nodes.values()))

    def test_save_as_preserves_original_and_switches_future_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory)/"original.nodeforge.json"
            destination = Path(directory)/"copy.drop.json"
            self.app.path = original
            self.assertTrue(self.app.save_project())
            original_bytes = original.read_bytes()
            self.app.graph.nodes["n2"].props["value"] = "10"
            with patch("tkinter.filedialog.asksaveasfilename", return_value=str(destination)) as dialog:
                self.assertTrue(self.app.save_project_as())
            self.assertEqual(dialog.call_args.kwargs["initialfile"], original.name)
            self.assertEqual(Path(dialog.call_args.kwargs["initialdir"]), original.parent)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(Graph.load(destination).nodes["n2"].props["value"], "10")
            self.assertEqual(self.app.path, destination)
            self.assertEqual(self.app.saved_state, self.app.snapshot())
            self.app.graph.nodes["n2"].props["value"] = "20"
            with patch("tkinter.filedialog.asksaveasfilename") as dialog:
                self.assertTrue(self.app.save_project())
                dialog.assert_not_called()
            self.assertEqual(Graph.load(destination).nodes["n2"].props["value"], "20")
            self.assertEqual(original.read_bytes(), original_bytes)

    def test_save_as_cancel_and_failure_preserve_project_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.path = Path(directory)/"original.drop.json"
            self.assertTrue(self.app.save_project())
            original_path, saved_state = self.app.path, self.app.saved_state
            self.app.graph.nodes["n2"].props["value"] = "10"
            for answer, failure in (("", False), (str(Path(directory)/"copy.drop.json"), True)):
                with self.subTest(failure=failure), \
                     patch("tkinter.filedialog.asksaveasfilename", return_value=answer), \
                     patch.object(self.app.graph, "save", side_effect=OSError("Disk full")) as save, \
                     patch("tkinter.messagebox.showerror") as error:
                    self.assertFalse(self.app.save_project_as())
                    self.assertEqual(self.app.path, original_path)
                    self.assertEqual(self.app.saved_state, saved_state)
                    self.assertNotEqual(self.app.snapshot(), saved_state)
                    self.assertEqual(save.called, failure)
                    self.assertEqual(error.called, failure)

    def test_open_discovers_both_project_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".drop.json", ".nodeforge.json"):
                with self.subTest(suffix=suffix):
                    path = Path(directory)/("graph"+suffix)
                    demo_graph().save(path)
                    with patch("tkinter.filedialog.askopenfilename", return_value=str(path)) as dialog:
                        self.app.open_project()
                    patterns = dialog.call_args.kwargs["filetypes"][0][1]
                    self.assertIn("*.drop.json", patterns)
                    self.assertIn("*.nodeforge.json", patterns)
                    self.assertEqual(self.app.path, path)
                    self.assertEqual(self.app.graph.to_dict(), Graph.load(path).to_dict())

    def test_save_as_shortcut_and_invalid_inspector(self):
        self.assertTrue(self.root.bind("<Control-Shift-S>"))
        with patch.object(self.app, "apply_properties", return_value=False), \
             patch("tkinter.filedialog.asksaveasfilename") as dialog:
            self.assertFalse(self.app.save_project_as())
            dialog.assert_not_called()

    def test_create_custom_node_test_save_search_run_and_edit(self):
        from custom_nodes import load_templates
        with tempfile.TemporaryDirectory() as directory:
            self.app.custom_library_path = Path(directory)/"custom_nodes.json"
            self.app.custom_templates = {}
            self.app.refresh_custom_palette()
            self.assertTrue(self.app.create_node_button.winfo_ismapped())
            before = self.app.snapshot()
            self.app.create_node_button.invoke()
            self.root.update()
            self.assertIsNotNone(self.app.creator_window)
            self.assertTrue(self.app.creator_create_button.winfo_ismapped())
            self.assertLessEqual(self.app.creator_create_button.winfo_rooty()+self.app.creator_create_button.winfo_height(),
                                 self.app.creator_window.winfo_rooty()+self.app.creator_window.winfo_height())
            self.app.creator_test_button.invoke()
            self.assertIn("110.0",self.app.creator_result.get())
            self.assertEqual(self.app.snapshot(),before)
            self.app.creator_create_button.invoke()
            self.root.update()
            self.assertIsNone(self.app.creator_window)
            node = self.app.graph.nodes[self.app.selected]
            self.assertEqual(node.kind,"Custom")
            self.assertEqual(node.title,"Add tax")
            self.assertIn("Add tax",load_templates(self.app.custom_library_path))
            after = self.app.snapshot()
            self.app.undo()
            self.assertEqual(self.app.snapshot(),before)
            self.assertIn("Add tax",self.app.custom_templates)
            self.app.redo()
            self.assertEqual(self.app.snapshot(),after)
            out = self.app.graph.add_node("Print",700,100)
            out.props["label"] = ""
            self.app.graph.connect("n1","next",node.id,"run")
            self.app.graph.connect(node.id,"next",out.id,"run")
            self.app.graph.connect(node.id,"result",out.id,"value")
            self.assertIn("110.0",self.run_and_wait())
            self.app.select(node.id)
            self.app.form_vars["input:price"].set("200")
            self.app.apply_properties()
            self.assertIn("220.0",self.run_and_wait())
            self.assertEqual(load_templates(self.app.custom_library_path)["Add tax"]["inputs"][0]["default"],"100")
            self.app.open_code_at(100,100)
            self.app.picker_query.set("Add tax")
            self.assertEqual(self.app.picker_matches,["Add tax"])
            self.app.accept_node_picker()
            second = self.app.graph.nodes[self.app.selected]
            self.assertEqual(second.kind,"Custom")
            self.assertEqual(second.props["inputs"][0]["default"],"100")
            props = load_templates(self.app.custom_library_path)["Add tax"]
            props["template_name"] = "Tax and tip"
            self.app.custom_templates["Tax and tip"] = props
            self.app.open_code_at(100,100)
            self.app.picker_query.set("Tax and tip")
            self.assertEqual(self.app.picker_matches,["Tax and tip"])
            self.app.accept_node_picker()
            self.assertEqual(self.app.graph.nodes[self.app.selected].title,"Tax and tip")

    def test_custom_node_validation_preserves_graph_and_library(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"custom_nodes.json"
            self.app.custom_library_path = path
            before = self.app.snapshot()
            self.app.open_node_creator()
            self.app.creator_vars["expression"].set("missing + price")
            self.app.create_custom_node()
            self.assertIn("Unknown input",self.app.creator_result.get())
            self.assertEqual(self.app.snapshot(),before)
            self.assertFalse(path.exists())
            self.app.creator_vars["expression"].set("price / rate")
            self.app.creator_inputs[1][1].set("0")
            self.app.test_custom_formula()
            self.assertIn("ZeroDivisionError",self.app.creator_result.get())
            self.assertFalse(path.exists())

    def test_custom_node_automatic_connection_and_duplicate_name(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.custom_library_path = Path(directory)/"custom_nodes.json"
            graph = Graph()
            main = graph.add_node("Main",0,0)
            out = graph.add_node("Print",660,0)
            graph.connect(main.id,"next",out.id,"run")
            self.app.replace_graph(graph)
            self.app.code_connect_var.set("After Main")
            self.app.open_node_creator()
            self.app.create_custom_node()
            custom = self.app.selected
            self.assertEqual(self.app.graph.next_node(main.id),custom)
            self.assertEqual(self.app.graph.next_node(custom),out.id)
            self.app.graph.connect(custom,"result",out.id,"value")
            self.assertIn("110.0",self.run_and_wait())
            before = self.app.snapshot()
            self.app.open_node_creator()
            self.app.create_custom_node()
            self.assertIn("already saved",self.app.creator_result.get())
            self.assertEqual(self.app.snapshot(),before)

    def run_with_answers(self, answers):
        self.app.run_graph()
        until = time.monotonic()+7
        prompts = []
        answers = iter(answers)
        while self.app.busy and time.monotonic() < until:
            self.pump(.02)
            if self.app.pending_input is not None:
                prompts.append(self.app.input_prompt.get())
                self.app.input_entry.insert(0,next(answers))
                self.app.submit_input()
        self.assertFalse(self.app.busy)
        return prompts,self.app.output_text.get("1.0","end")

    def test_interactive_calculator_prompts_and_conversion_feedback(self):
        from example_graphs import interactive_calculator_demo
        self.app.replace_graph(interactive_calculator_demo())
        prompts,text = self.run_with_answers(["2.5","4"])
        self.assertEqual(prompts,["First number: ","Second number: "])
        self.assertIn("Sum: 6.5",text)
        self.assertIn("Finished successfully",text)
        self.assertIsNone(self.app.error_node)
        self.assertFalse(self.app.input_panel.winfo_ismapped())
        prompts,text = self.run_with_answers(["oops","4"])
        self.assertIn("Cannot convert this value to a number",text)
        self.assertEqual(self.app.error_node,"n4")
        self.assertEqual(self.app.inspector_node,"n4")

    def test_interactive_wait_is_not_execution_timeout_and_cancel_stops(self):
        from example_graphs import interactive_calculator_demo
        self.app.replace_graph(interactive_calculator_demo())
        self.app.execution_timeout = .5
        self.app.run_graph()
        until = time.monotonic()+4
        while self.app.pending_input is None and time.monotonic() < until:
            self.pump(.02)
        self.assertIsNotNone(self.app.pending_input)
        self.assertTrue(self.app.input_panel.winfo_ismapped())
        self.pump(.7)
        self.assertTrue(self.app.busy)
        self.assertIsNone(self.app.process.poll())
        self.app.submit_input(cancel=True)
        until = time.monotonic()+4
        while self.app.busy and time.monotonic() < until:
            self.pump(.02)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.error_node,"n2")
        self.assertIn("Input cancelled",self.app.output_text.get("1.0","end"))

    def test_execution_timeout_still_stops_noninteractive_program(self):
        from graph_core import Compilation
        self.app.execution_timeout = .3
        with patch("nodeforge.compile_graph",return_value=Compilation("import time\ntime.sleep(10)",[],set())):
            text = self.run_and_wait()
        self.assertIn("Execution stopped after 0.3 seconds",text)
        self.assertIsNone(self.app.pending_input)

    def test_new_nodes_are_searchable_and_editable(self):
        from graph_core import validate_props
        for kind in ("Input","Convert","Text Join","Boolean Logic","Clamp","Random","Assign"):
            with self.subTest(kind=kind):
                self.app.search_anchor = self.app.inverse(12,12)
                self.app.open_node_picker(kind)
                # Related built-ins and installed libraries may share search
                # terms; the exact built-in name must remain the first choice.
                self.assertEqual(self.app.picker_matches[0],kind)
                self.app.accept_node_picker()
                node = self.app.graph.nodes[self.app.selected]
                self.assertEqual(node.kind,kind)
                validate_props(node)
                self.assertTrue(self.app.form_vars)

    def test_boolean_clamp_random_examples_run_in_editor(self):
        from example_graphs import controlled_temperature_demo, clamp_demo, dice_demo
        for factory,expected in ((controlled_temperature_demo,"Fan ON (hot and enabled)"),
                                 (clamp_demo,"Clamped power: 100.0"),(dice_demo,"Dice roll: 6")):
            with self.subTest(example=factory.__name__):
                self.app.replace_graph(factory())
                self.assertIn(expected,self.run_and_wait())
                self.assertIsNone(self.app.error_node)
        self.assertIn("Dice roll: 6",self.run_and_wait())
        self.app.select("n2")
        self.app.form_vars["mode"].set("Decimal")
        self.app.form_vars["min"].set("0")
        self.app.form_vars["max"].set("1")
        self.app.apply_properties()
        self.assertIn("Finished successfully",self.run_and_wait())

    def test_boolean_clamp_random_error_feedback(self):
        from example_graphs import controlled_temperature_demo, clamp_demo, dice_demo
        graph = controlled_temperature_demo()
        graph.nodes["n7"].props.update(type="Text",value="False")
        self.app.replace_graph(graph)
        self.assertIn("Boolean Logic needs True/False",self.run_and_wait())
        self.assertEqual(self.app.error_node,"n8")
        graph = clamp_demo()
        graph.nodes["n3"].props.update(min="200")
        self.app.replace_graph(graph)
        self.assertIn("Minimum is greater than maximum",self.run_and_wait())
        self.assertEqual(self.app.error_node,"n3")
        graph = dice_demo()
        graph.nodes["n2"].props.update(min="1.5")
        self.app.replace_graph(graph)
        self.assertIn("Random Integer needs whole-number bounds",self.run_and_wait())
        self.assertEqual(self.app.error_node,"n2")

    def test_drag_node_and_undo_redo(self):
        node = self.app.graph.nodes["n2"]
        original = (node.x, node.y)
        x, y = self.app.transform(node.x + 40, node.y + 20)
        self.app.canvas.event_generate("<ButtonPress-1>", x=round(x), y=round(y))
        self.app.canvas.event_generate("<B1-Motion>", x=round(x+80), y=round(y+40))
        self.app.canvas.event_generate("<ButtonRelease-1>", x=round(x+80), y=round(y+40))
        self.root.update()
        moved = (node.x, node.y)
        self.assertNotEqual(original, moved)
        self.app.undo()
        self.assertEqual((self.app.graph.nodes["n2"].x, self.app.graph.nodes["n2"].y), original)
        self.app.redo()
        self.assertEqual((self.app.graph.nodes["n2"].x, self.app.graph.nodes["n2"].y), moved)

    def test_click_connection_and_disconnect(self):
        g = Graph()
        a, b = g.add_node("Main", 0, 0), g.add_node("Print", 350, 0)
        self.app.replace_graph(g)
        self.root.update()
        self.click(*self.app.pin_position(a, "out", "next"))
        self.click(*self.app.pin_position(b, "in", "run"))
        self.assertEqual(len(g.edges), 1)
        self.app.disconnect_pin((b.id, "in", "run"))
        self.assertEqual(g.edges, [])

    def test_drag_connection(self):
        g = Graph()
        a, b = g.add_node("Main", 0, 0), g.add_node("Print", 350, 0)
        self.app.replace_graph(g)
        self.root.update()
        sx, sy = self.app.pin_position(a, "out", "next")
        tx, ty = self.app.pin_position(b, "in", "run")
        self.app.canvas.event_generate("<ButtonPress-1>", x=round(sx), y=round(sy))
        self.app.canvas.event_generate("<B1-Motion>", x=round(tx), y=round(ty))
        self.app.canvas.event_generate("<ButtonRelease-1>", x=round(tx), y=round(ty))
        self.root.update()
        self.assertEqual(len(g.edges), 1)

    def test_properties_applied_when_reselecting_same_node(self):
        self.app.select("n2")
        self.app.form_vars["value"].set("10")
        node = self.app.graph.nodes["n2"]
        self.click(*self.app.transform(node.x+40, node.y+20))
        self.assertEqual(node.props["value"], "10")
        self.assertIn("98.1", self.run_and_wait())

    def test_pending_properties_applied_before_wire_change(self):
        self.app.select("n2")
        self.app.form_vars["value"].set("12")
        a, b = self.app.graph.nodes["n2"], self.app.graph.nodes["n5"]
        self.click(*self.app.pin_position(a, "out", "value"))
        self.click(*self.app.pin_position(b, "in", "b"))
        self.assertEqual(a.props["value"], "12")

    def test_duplicate_delete_undo(self):
        self.app.select("n2")
        self.app.duplicate_selected()
        duplicate = self.app.selected
        self.assertEqual(len(self.app.graph.nodes), 7)
        self.assertNotEqual(self.app.graph.nodes[duplicate].props["name"], "mass_kg")
        self.app.delete_selected()
        self.assertEqual(len(self.app.graph.nodes), 6)
        self.app.undo()
        self.assertIn(duplicate, self.app.graph.nodes)

    def test_palette_drag_adds_node_at_drop(self):
        def find_label(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "Label" and child.cget("text") == "Variable":
                    return child
                found = find_label(child)
                if found:
                    return found
        label = find_label(self.app.palette)
        self.assertIsNotNone(label)
        tx = self.app.canvas.winfo_rootx()+200
        ty = self.app.canvas.winfo_rooty()+200
        label.event_generate("<ButtonPress-1>", x=10, y=10, rootx=label.winfo_rootx()+10, rooty=label.winfo_rooty()+10)
        label.event_generate("<B1-Motion>", x=200, y=200, rootx=tx, rooty=ty)
        label.event_generate("<ButtonRelease-1>", x=200, y=200, rootx=tx, rooty=ty)
        self.root.update()
        self.assertEqual(len(self.app.graph.nodes), 7)
        node = self.app.graph.nodes[self.app.selected]
        px, py = self.app.transform(node.x, node.y)
        self.assertAlmostEqual(px, 200)
        self.assertAlmostEqual(py, 200)

    def test_zoom_keeps_point_under_cursor(self):
        before = self.app.inverse(200, 200)
        self.app.zoom_at(200, 200, 1.1)
        after = self.app.inverse(200, 200)
        self.assertAlmostEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1])

    def test_node_picker_type_enter_places_at_clicked_world_position(self):
        self.assertIsNone(self.app.hit_node(12, 12))
        self.app.zoom_at(12, 12, 1.1)
        expected = self.app.inverse(12, 12)
        before = self.app.snapshot()
        self.click(12, 12)
        self.app.canvas.focus_force()
        self.root.update()
        self.app.canvas.event_generate("<KeyPress>", keysym="m")
        self.root.update()
        self.assertIsNotNone(self.app.node_picker)
        self.app.picker_entry.insert("end", "ath")
        self.assertEqual(self.app.picker_matches, ["Math"])
        self.app.picker_entry.event_generate("<Return>")
        self.root.update()
        added = self.app.graph.nodes[self.app.selected]
        self.assertEqual(added.kind, "Math")
        self.assertEqual((added.x, added.y), expected)
        self.assertIsNone(self.app.node_picker)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)

    def test_python_picker_typed_print_placement_undo_and_run(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        self.app.replace_graph(graph)
        before = self.app.snapshot()
        expected = self.app.inverse(12,12)
        self.click(12,12)
        self.app.canvas.focus_force()
        self.root.update()
        self.app.canvas.event_generate("<KeyPress>",keysym="p")
        self.root.update()
        self.app.picker_entry.insert("end",'rint("Hello from code")')
        self.assertTrue(self.app.picker_code)
        self.assertEqual(self.app.picker_matches,["Print"])
        self.app.picker_entry.event_generate("<Return>")
        self.root.update()
        node = self.app.graph.nodes[self.app.selected]
        self.assertEqual(node.props["value"],"Hello from code")
        self.assertEqual((node.x,node.y),expected)
        self.assertIsNone(self.app.node_picker)
        self.app.undo()
        self.assertEqual(self.app.snapshot(),before)
        self.app.redo()
        self.app.graph.connect("n1","next",node.id,"run")
        self.assertIn("Hello from code",self.run_and_wait())

    def test_python_picker_variable_reference_group_and_persistence(self):
        self.click(12,12)
        self.app.open_node_picker("speed = 10")
        self.app.accept_node_picker()
        self.assertEqual(self.app.graph.nodes[self.app.selected].props["name"],"speed")
        before = self.app.snapshot()
        self.app.open_code_at(123,45)
        self.app.picker_query.set("print(speed * 2)")
        self.app.accept_node_picker()
        primary = self.app.selected
        self.assertEqual(self.app.graph.nodes[primary].kind,"Print")
        self.assertEqual((self.app.graph.nodes[primary].x,self.app.graph.nodes[primary].y),(123,45))
        after = self.app.snapshot()
        self.app.undo()
        self.assertEqual(self.app.snapshot(),before)
        self.app.redo()
        self.assertEqual(self.app.snapshot(),after)
        self.app.graph.connect("n1","next",primary,"run")
        self.assertIn("20.0",self.run_and_wait())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"typed.nodeforge.json"
            self.app.graph.save(path)
            self.assertEqual(Graph.load(path).to_dict(),self.app.graph.to_dict())

    def test_python_picker_invalid_code_is_preserved(self):
        before = self.app.snapshot()
        self.app.open_code_at(100,100)
        self.app.picker_query.set('print(')
        self.app.accept_node_picker()
        self.assertIsNotNone(self.app.node_picker)
        self.assertEqual(self.app.picker_query.get(),'print(')
        self.assertIn("Finish",self.app.picker_message.get())
        self.assertEqual(self.app.snapshot(),before)
        self.app.picker_query.set('print("Recovered")')
        self.app.accept_node_picker()
        self.assertEqual(self.app.graph.nodes[self.app.selected].props["value"],"Recovered")

    def test_visible_code_entry_accepts_typing_without_canvas_click(self):
        self.assertIsNone(self.app.search_anchor)
        self.assertTrue(self.app.code_entry.winfo_ismapped())
        expected = self.app.inverse(self.app.canvas.winfo_width()/2,self.app.canvas.winfo_height()/2)
        before = len(self.app.graph.nodes)
        self.app.code_entry.focus_force()
        self.root.update()
        self.app.code_entry.event_generate("<KeyPress>",keysym="p")
        self.root.update()
        self.assertEqual(self.app.code_entry_var.get(),"p")
        self.app.code_entry.insert("end",'rint("Visible entry works")')
        self.app.code_entry.event_generate("<Return>")
        self.root.update()
        node = self.app.graph.nodes[self.app.selected]
        self.assertEqual(node.kind,"Print")
        self.assertEqual(node.props["value"],"Visible entry works")
        self.assertEqual((node.x,node.y),expected)
        self.assertEqual(len(self.app.graph.nodes),before+1)
        self.assertEqual(self.app.code_entry_var.get(),"")

    def test_script_preview_import_undo_layout_and_value_inspection(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        self.app.replace_graph(graph)
        before = self.app.snapshot()
        self.app.open_script_editor()
        self.root.update()
        source = "price = 10\nquantity = 2\ntotal = price * quantity\nprint(total)"
        self.app.script_text.delete("1.0","end")
        self.app.script_text.insert("1.0",source)
        self.root.update()
        self.app.preview_script()
        self.root.update()
        self.assertEqual(str(self.app.script_import_button.cget("state")),"normal")
        self.assertEqual(self.app.snapshot(),before)
        self.assertTrue(self.app.script_preview.find_all())
        self.app.import_script()
        self.assertIsNone(self.app.script_window)
        after = self.app.snapshot()
        self.app.undo()
        self.assertEqual(self.app.snapshot(),before)
        self.app.redo()
        self.assertEqual(self.app.snapshot(),after)
        self.assertIn("20.0",self.run_and_wait())
        total = next(n for n in self.app.graph.nodes.values() if n.kind == "Assign")
        self.assertEqual(self.app.run_values[total.id],{"type":"float","value":"20.0"})
        self.app.select(total.id)
        self.assertIn("20.0",self.app.node_value_var.get())
        math = next(n for n in self.app.graph.nodes.values() if n.kind == "Math")
        self.app.select(math.id)
        self.app.form_vars["operator"].set("+")
        self.app.apply_properties()
        self.assertEqual(self.app.run_values,{})
        self.assertIn("Not evaluated",self.app.node_value_var.get())

    def test_script_input_assignment_and_failed_preview(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        self.app.replace_graph(graph)
        before = self.app.snapshot()
        self.app.open_script_editor()
        self.app.script_text.delete("1.0","end")
        self.app.script_text.insert("1.0","price = 2\ntotal = missing * price")
        self.root.update()
        self.app.preview_script()
        self.assertIn("Line 2",self.app.script_note.get())
        self.assertEqual(self.app.snapshot(),before)
        self.assertEqual(str(self.app.script_import_button.cget("state")),"disabled")
        self.app.script_text.delete("1.0","end")
        self.app.script_text.insert("1.0",'name = input("Your name? ")\nprint("Hello " + name)')
        self.root.update()
        self.app.preview_script()
        self.app.import_script()
        prompts,text = self.run_with_answers(["Ada"])
        self.assertEqual(prompts,["Your name? "])
        self.assertIn("Hello Ada",text)
        assigned = next(n for n in self.app.graph.nodes.values() if n.kind == "Assign")
        self.assertEqual(self.app.run_values[assigned.id]["value"],"'Ada'")

    def test_drop_language_script_editor_preview_and_run(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        self.app.replace_graph(graph)
        before = self.app.snapshot()
        self.app.open_script_editor()
        self.app.script_language_var.set("Drop")
        self.app.load_language_example()
        self.root.update()
        self.assertIn("let total be price times quantity",self.app.script_text.get("1.0","end"))
        self.app.preview_script()
        self.root.update()
        self.assertEqual(str(self.app.script_import_button.cget("state")),"normal")
        self.assertEqual(self.app.snapshot(),before)
        self.app.import_script()
        prompts,text = self.run_with_answers(["Ada"])
        self.assertEqual(prompts,["Your name? "])
        self.assertIn("Hello Ada\n37.5",text)
        self.assertIn("Finished successfully",text)

    def test_script_language_switch_preserves_source_and_invalidates_preview(self):
        self.app.open_script_editor()
        self.root.update()
        self.app.preview_script()
        self.root.update()
        source = self.app.script_text.get("1.0","end-1c")
        self.assertEqual(str(self.app.script_import_button.cget("state")),"normal")
        self.app.script_language_var.set("Drop")
        self.assertEqual(str(self.app.script_import_button.cget("state")),"disabled")
        self.assertEqual(self.app.script_text.get("1.0","end-1c"),source)
        before = self.app.snapshot()
        self.app.preview_script()
        self.assertIn("Line 1",self.app.script_note.get())
        self.assertEqual(self.app.snapshot(),before)

    def test_code_bar_connects_after_selected_and_preserves_tail(self):
        graph = Graph()
        main = graph.add_node("Main",0,0)
        old = graph.add_node("Print",350,0)
        old.props.update(label="",value="old")
        graph.connect(main.id,"next",old.id,"run")
        self.app.replace_graph(graph)
        self.app.select(main.id)
        self.app.code_connect_var.set("After selected")
        self.app.code_entry_var.set('print("new")')
        self.app.submit_code_bar()
        new = self.app.selected
        self.assertEqual(self.app.graph.next_node(main.id),new)
        self.assertEqual(self.app.graph.next_node(new),old.id)
        self.assertIn("new\nold",self.run_and_wait())

    def test_group_visibility_and_named_action_connection(self):
        from code_to_nodes import translate_code, connect_drop
        graph = Graph()
        graph.add_node("Main",0,0)
        self.app.replace_graph(graph)
        plan = translate_code("\n".join(f"print({i})" for i in range(40)),self.app.graph,0,0)
        connect_drop(plan,"After Main")
        imported = plan.added[:]
        self.app.commit_code_drop(plan)
        self.app.fit_view()
        from nodeforge import NODE_W,node_height
        for node_id in imported:
            node = self.app.graph.nodes[node_id]
            left,top = self.app.transform(node.x,node.y)
            right,bottom = self.app.transform(node.x+NODE_W,node.y+node_height(node))
            self.assertGreaterEqual(left,-1)
            self.assertGreaterEqual(top,-1)
            self.assertLessEqual(right,self.app.canvas.winfo_width()+1)
            self.assertLessEqual(bottom,self.app.canvas.winfo_height()+1)
        self.app.code_connect_var.set("After Main")
        self.app.code_entry_var.set("Print")
        self.app.submit_code_bar()
        self.assertEqual(self.app.graph.next_node("n1"),self.app.selected)

    def test_visible_code_entry_shortcut_and_invalid_code_recovery(self):
        before = self.app.snapshot()
        self.root.focus_force()
        self.root.update()
        self.root.event_generate("<Control-space>")
        self.root.update()
        self.assertEqual(self.root.focus_get(),self.app.code_entry)
        self.app.code_entry_var.set("print(")
        self.app.submit_code_bar()
        self.assertEqual(self.app.snapshot(),before)
        self.assertEqual(self.app.code_entry_var.get(),"print(")
        self.assertIn("Finish",self.app.picker_message.get())
        self.app.close_node_picker()
        self.app.code_entry_var.set("recovered_value = 10")
        self.app.submit_code_bar()
        self.assertEqual(self.app.graph.nodes[self.app.selected].props["name"],"recovered_value")

    def test_canvas_tiny_mouse_movement_preserves_typing_target(self):
        from types import SimpleNamespace
        self.click(12,12)
        anchor = self.app.search_anchor
        self.app.pan_start(SimpleNamespace(x=12,y=12))
        self.app.pan_move(SimpleNamespace(x=13,y=12))
        self.assertEqual(self.app.search_anchor,anchor)
        self.app.pan_move(SimpleNamespace(x=30,y=12))
        self.assertIsNone(self.app.search_anchor)

    def test_node_entry_tab_and_b_shortcuts(self):
        for keysym in ("Tab","b","B"):
            with self.subTest(keysym=keysym):
                self.app.canvas.focus_force()
                self.root.update()
                self.app.canvas.event_generate("<KeyPress>",keysym=keysym)
                self.root.update()
                self.assertEqual(self.root.focus_get(),self.app.code_entry)
                self.assertIsNone(self.app.node_picker)
                self.assertEqual(self.app.code_entry_var.get(),"")

    def test_node_entry_shortcuts_do_not_interrupt_typing(self):
        self.app.code_entry_var.set("clamp")
        self.app.code_entry.focus_force()
        self.app.code_entry.icursor("end")
        self.root.update()
        self.app.code_entry.event_generate("<KeyPress>",keysym="b")
        self.root.update()
        self.assertEqual(self.app.code_entry_var.get(),"clampb")
        self.assertEqual(self.root.focus_get(),self.app.code_entry)
        self.app.code_entry.event_generate("<Tab>")
        self.root.update()
        self.assertNotEqual(self.root.focus_get(),self.app.code_entry)

    def test_node_picker_no_match_cancel_and_aliases(self):
        before = self.app.snapshot()
        self.click(12, 12)
        self.app.open_node_picker("not_a_node")
        self.assertEqual(self.app.picker_matches, [])
        self.app.accept_node_picker()
        self.assertEqual(self.app.snapshot(), before)
        self.app.picker_query.set("console")
        self.assertEqual(self.app.picker_matches, ["Print"])
        self.app.picker_entry.focus_force()
        self.root.update()
        self.app.picker_entry.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.app.node_picker)
        self.assertEqual(self.app.snapshot(), before)

    def test_node_picker_arrows_shortcut_and_duplicate_main(self):
        self.click(12, 12)
        self.app.canvas.focus_force()
        self.root.update()
        zoom = self.app.zoom
        self.app.canvas.event_generate("<KeyPress>", keysym="f")
        self.root.update()
        self.assertIsNotNone(self.app.node_picker)
        self.assertEqual(self.app.zoom, zoom)
        self.app.picker_query.set("")
        self.assertNotIn("Main", self.app.picker_matches)
        self.app.picker_entry.event_generate("<Down>")
        self.root.update()
        self.assertEqual(self.app.picker_list.curselection(), (1,))
        self.app.picker_entry.event_generate("<Up>")
        self.root.update()
        self.assertEqual(self.app.picker_list.curselection(), (0,))
        self.click(15, 15)
        self.assertIsNone(self.app.node_picker)

    def test_zoom_controls_and_minimap_preserve_graph(self):
        from types import SimpleNamespace
        before = self.app.snapshot()
        center = (self.app.canvas.winfo_width()/2, self.app.canvas.winfo_height()/2)
        world_center = self.app.inverse(*center)
        self.app.zoom_center(1.15)
        self.app.reset_zoom()
        self.assertAlmostEqual(self.app.zoom, 1)
        for actual, expected in zip(self.app.inverse(*center), world_center):
            self.assertAlmostEqual(actual, expected)
        x1, y1, scale, ox, oy = self.app.minimap_transform
        node = self.app.graph.nodes["n2"]
        from nodeforge import NODE_W, node_height
        wx, wy = node.x+NODE_W/2, node.y+node_height(node)/2
        self.app.minimap_navigate(SimpleNamespace(x=ox+(wx-x1)*scale, y=oy+(wy-y1)*scale))
        actual = self.app.inverse(*center)
        self.assertAlmostEqual(actual[0], wx)
        self.assertAlmostEqual(actual[1], wy)
        self.assertEqual(self.app.snapshot(), before)

    def test_pending_wire_marks_matching_ports(self):
        from types import SimpleNamespace
        self.app.pending = ("n1", "out", "next")
        self.assertTrue(self.app.compatible_pin(("n5", "in", "run")))
        self.assertFalse(self.app.compatible_pin(("n5", "in", "a")))
        x, y = self.app.pin_position(self.app.graph.nodes["n5"], "in", "run")
        self.app.canvas_motion(SimpleNamespace(x=x, y=y))
        self.assertIn("Release to connect", self.app.hint_var.get())
        self.assertTrue(self.app.canvas.find_withtag("hover"))
        self.app.cancel_wire()
        self.assertFalse(self.app.canvas.find_withtag("pending"))

    def test_save_open_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.nodeforge.json"
            self.app.path = path
            self.assertTrue(self.app.save_project())
            with patch("tkinter.filedialog.askopenfilename", return_value=str(path)):
                self.app.open_project()
            self.assertEqual(self.app.graph.to_dict(), demo_graph().to_dict())
            export = Path(directory) / "export.py"
            with patch("tkinter.filedialog.asksaveasfilename", return_value=str(export)):
                self.app.export_python()
            self.assertIn("def calculate_force", export.read_text())

    def test_run_displays_output_and_runtime_errors(self):
        self.assertIn("Force (N): 24.525", self.run_and_wait())
        self.app.select("n5")
        self.app.form_vars["expression"].set("a / c")
        self.app.apply_properties()
        self.assertIn("ZeroDivisionError", self.run_and_wait())
        self.assertEqual(self.app.error_node, "n5")
        self.assertEqual(self.app.selected, "n5")
        self.assertIn("divisor", self.app.output_text.get("1.0", "end"))
        self.pump(1.2)
        self.assertEqual(self.app.error_node, "n5")
        self.app.form_vars["expression"].set("a * b")
        self.app.apply_properties()
        self.assertIn("Finished successfully", self.run_and_wait())
        self.assertIsNone(self.app.error_node)

    def test_upstream_math_error_identifies_math_instead_of_print(self):
        g = Graph()
        main = g.add_node("Main", 0, 0)
        math = g.add_node("Math", 300, 0)
        math.props.update(operator="/", a="1", b="0")
        out = g.add_node("Print", 600, 0)
        g.connect(main.id, "next", out.id, "run")
        g.connect(math.id, "result", out.id, "value")
        self.app.replace_graph(g)
        self.assertIn("Division by zero", self.run_and_wait())
        self.assertEqual(self.app.error_node, math.id)
        self.assertEqual(self.app.inspector_node, math.id)

    def test_compile_error_highlights_function_that_needs_to_run(self):
        self.app.graph.connect("n1", "next", "n6", "run")
        self.app.refresh()
        self.app.run_graph()
        self.assertEqual(self.app.error_node, "n5")
        self.assertIn("must run before", self.app.output_text.get("1.0", "end"))

    def test_invalid_connection_leaves_graph_unchanged(self):
        old = self.app.snapshot()
        a, b = self.app.graph.nodes["n1"], self.app.graph.nodes["n5"]
        self.click(*self.app.pin_position(a, "out", "next"))
        self.click(*self.app.pin_position(b, "in", "a"))
        self.assertEqual(self.app.snapshot(), old)
        self.assertIn("execution", self.app.status_var.get())


if __name__ == "__main__":
    unittest.main()
