"""Graph editing gestures, history, and text-safe shortcuts on a real Tk display."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph
from node_libraries import make_reference, validate_library
from nodeforge import Editor, NODE_W, node_height


def editing_graph():
    """A valid program with deliberately irregular, manually chosen positions."""
    graph = Graph()
    main = graph.add_node("Main", 50, 50)
    number = graph.add_node("Function", 410, 310)
    math = graph.add_node("Custom", 780, 90)
    output = graph.add_node("Print", 1140, 400)
    graph.connect(main.id, "next", number.id, "run")
    graph.connect(number.id, "next", math.id, "run")
    graph.connect(math.id, "next", output.id, "run")
    graph.connect(number.id, "result", math.id, "a")
    graph.connect(math.id, "result", output.id, "value")
    return graph


@unittest.skipUnless(sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY"),
                     "No desktop display")
class EditorToolsTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, editing_graph(),
                          custom_library_path=self.directory / "formulas.json",
                          library_store_path=self.directory / "installed")
        self.pump(.12)  # Complete the editor's initial deferred fit.
        self.app.fit_view()
        self.root.update()

    def tearDown(self):
        self.app.closed = True
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [], str(self.errors))

    def pump(self, seconds=.02):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.root.update()
            time.sleep(.004)

    def positions(self):
        return {node.id: (node.x, node.y) for node in self.app.graph.nodes.values()}

    def event_at(self, x, y, state=0, num=1):
        x, y = self.app.transform(x, y)
        return SimpleNamespace(x=x, y=y, state=state, num=num, widget=self.app.canvas)

    def click_node(self, node_id, state=0):
        node = self.app.graph.nodes[node_id]
        event = self.event_at(node.x + NODE_W / 2, node.y + 24, state)
        self.app.canvas_press(event)
        self.app.canvas_release(event)

    def assert_single_undo(self, before, operation):
        depth = len(self.app.undo_stack)
        operation()
        after = self.app.snapshot()
        self.assertNotEqual(after, before)
        self.assertEqual(len(self.app.undo_stack), depth + 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)
        self.app.redo()
        self.assertEqual(self.app.snapshot(), after)

    def test_shift_adds_and_control_toggles_selection_without_editing_graph(self):
        before = self.app.snapshot()
        self.click_node("n2")
        self.click_node("n3", state=0x1)
        self.assertEqual(set(self.app.current_selection()), {"n2", "n3"})
        self.click_node("n2", state=0x4)
        self.assertEqual(set(self.app.current_selection()), {"n3"})
        self.click_node("n1")
        self.assertEqual(set(self.app.current_selection()), {"n1"})
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_direct_legacy_selected_assignment_is_respected(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        self.app.selected = "n4"
        self.assertEqual(set(self.app.current_selection()), {"n4"})
        self.app.selected = None
        self.assertEqual(set(self.app.current_selection()), set())

    def test_box_selection_and_shift_box_add_without_panning(self):
        before, offset = self.app.snapshot(), self.app.offset[:]
        node = self.app.graph.nodes["n2"]
        start = self.event_at(node.x - 15, node.y - 15, state=0x1)
        end = self.event_at(node.x + NODE_W + 15, node.y + node_height(node) + 15, state=0x1)
        self.app.canvas_press(start)
        self.app.canvas_drag(end)
        self.app.canvas_release(end)
        self.assertEqual(set(self.app.current_selection()), {"n2"})
        node = self.app.graph.nodes["n3"]
        start = self.event_at(node.x - 15, node.y - 15, state=0x1)
        end = self.event_at(node.x + NODE_W + 15, node.y + node_height(node) + 15, state=0x1)
        self.app.canvas_press(start)
        self.app.canvas_drag(end)
        self.app.canvas_release(end)
        self.assertEqual(set(self.app.current_selection()), {"n2", "n3"})
        node = self.app.graph.nodes["n4"]
        start = self.event_at(node.x - 15, node.y - 15, state=0x4)
        end = self.event_at(node.x + NODE_W + 15, node.y + node_height(node) + 15, state=0x4)
        self.app.canvas_press(start)
        self.app.canvas_drag(end)
        self.app.canvas_release(end)
        self.assertEqual(set(self.app.current_selection()), {"n2", "n3", "n4"})
        self.assertEqual(self.app.offset, offset)
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_multi_node_drag_is_one_undo_and_preserves_relative_positions(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        positions = self.positions()
        before = self.app.snapshot()
        node = self.app.graph.nodes["n2"]
        start = self.event_at(node.x + 120, node.y + 24)
        end = SimpleNamespace(**vars(start))
        end.x += 56
        end.y -= 21
        def drag():
            self.app.canvas_press(start)
            self.app.canvas_drag(SimpleNamespace(**dict(vars(end), x=end.x - 12)))
            self.app.canvas_drag(end)
            self.app.canvas_release(end)
        self.assert_single_undo(before, drag)
        for node_id in ("n2", "n3"):
            x, y = self.positions()[node_id]
            self.assertAlmostEqual(x - positions[node_id][0], 56 / self.app.zoom)
            self.assertAlmostEqual(y - positions[node_id][1], -21 / self.app.zoom)
        for node_id in ("n1", "n4"):
            self.assertEqual(self.positions()[node_id], positions[node_id])

    def test_click_without_motion_does_not_create_history(self):
        before = self.app.snapshot()
        self.click_node("n3")
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_tk_click_then_drag_moves_selected_nodes(self):
        # Exercise Tk's double-click dispatch, which replaces ButtonPress-1 on
        # the second press. Direct handler calls cannot catch this regression.
        self.app.set_selection({"n2", "n3"}, primary="n2")
        node = self.app.graph.nodes["n2"]
        x, y = map(round, self.app.transform(node.x + 120, node.y + 24))
        positions = self.positions()
        before = self.app.snapshot()
        canvas = self.app.canvas
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=10000)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=10010)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=10100)
        canvas.event_generate("<Motion>", x=x + 56, y=y + 21, state=0x100, time=10120)
        canvas.event_generate("<ButtonRelease-1>", x=x + 56, y=y + 21, time=10140)
        self.root.update()
        for node_id in ("n2", "n3"):
            self.assertAlmostEqual(self.app.graph.nodes[node_id].x - positions[node_id][0], 56 / self.app.zoom)
            self.assertAlmostEqual(self.app.graph.nodes[node_id].y - positions[node_id][1], 21 / self.app.zoom)
        self.assertEqual(len(self.app.undo_stack), 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)

    def test_tk_click_then_shift_drag_empty_space_starts_box_selection(self):
        node = self.app.graph.nodes["n2"]
        x, y = map(round, self.app.transform(node.x - 15, node.y - 15))
        ex, ey = map(round, self.app.transform(node.x + NODE_W + 15, node.y + node_height(node) + 15))
        before = self.app.snapshot()
        canvas = self.app.canvas
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, state=0x1, time=20000)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y, state=0x1, time=20010)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, state=0x1, time=20100)
        canvas.event_generate("<Motion>", x=ex, y=ey, state=0x101, time=20120)
        canvas.event_generate("<ButtonRelease-1>", x=ex, y=ey, state=0x1, time=20140)
        self.root.update()
        self.assertEqual(self.app.current_selection(), {"n2"})
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_tk_plain_empty_canvas_drag_pans_without_moving_nodes(self):
        node = self.app.graph.nodes["n2"]
        x, y = map(round, self.app.transform(node.x - 15, node.y - 15))
        before, offset = self.app.snapshot(), self.app.offset[:]
        canvas = self.app.canvas
        # Include a preceding click so Tk also exercises its rapid-click path.
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=21000)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=21010)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=21100)
        canvas.event_generate("<Motion>", x=x + 63, y=y - 27, state=0x100, time=21120)
        canvas.event_generate("<ButtonRelease-1>", x=x + 63, y=y - 27, time=21140)
        self.root.update()
        self.assertAlmostEqual(self.app.offset[0], offset[0] + 63)
        self.assertAlmostEqual(self.app.offset[1], offset[1] - 27)
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_tk_node_body_drag_cancels_pending_wire_and_moves_in_same_gesture(self):
        self.app.select("n2")
        self.app.pending = ("n1", "out", "next")
        self.app.draw()
        node = self.app.graph.nodes["n2"]
        x, y = map(round, self.app.transform(node.x + 120, node.y + 24))
        positions, before = self.positions(), self.app.snapshot()
        canvas = self.app.canvas
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=22000)
        canvas.event_generate("<Motion>", x=x + 45, y=y + 18, state=0x100, time=22020)
        canvas.event_generate("<ButtonRelease-1>", x=x + 45, y=y + 18, time=22040)
        self.root.update()
        self.assertIsNone(self.app.pending)
        self.assertAlmostEqual(node.x - positions["n2"][0], 45 / self.app.zoom)
        self.assertAlmostEqual(node.y - positions["n2"][1], 18 / self.app.zoom)
        self.assertEqual(self.positions()["n1"], positions["n1"])
        self.assertEqual(len(self.app.undo_stack), 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)

    def test_tk_selected_node_drag_preserves_invalid_inspector_draft(self):
        self.app.select("n2")
        node = self.app.graph.nodes["n2"]
        original_expression = node.props["expression"]
        draft = self.app.form_vars["expression"]
        draft.set("a +")  # An incomplete expression must not block moving a card.
        self.assertTrue(self.app.form_dirty)
        x, y = map(round, self.app.transform(node.x + 120, node.y + 24))
        positions = self.positions()
        canvas = self.app.canvas
        with patch.object(self.app, "apply_properties", side_effect=AssertionError("Geometry drag validated the draft")) as apply, \
                patch("tkinter.messagebox.showerror") as modal:
            canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=23000)
            canvas.event_generate("<Motion>", x=x + 39, y=y + 24, state=0x100, time=23020)
            canvas.event_generate("<ButtonRelease-1>", x=x + 39, y=y + 24, time=23040)
            self.root.update()
            apply.assert_not_called()
            modal.assert_not_called()
        self.assertAlmostEqual(node.x - positions["n2"][0], 39 / self.app.zoom)
        self.assertAlmostEqual(node.y - positions["n2"][1], 24 / self.app.zoom)
        self.assertEqual(node.props["expression"], original_expression)
        self.assertIs(self.app.form_vars["expression"], draft)
        self.assertEqual(draft.get(), "a +")
        self.assertTrue(self.app.form_dirty)
        self.assertEqual(len(self.app.undo_stack), 1)

    def test_tk_function_call_rapid_second_press_can_drag_without_opening(self):
        graph = Graph()
        graph.add_node("Main", 40, 40)
        graph.add_function("calculate", ["value"], ["answer"])
        node = graph.add_call("calculate", 400, 40)
        self.app.replace_graph(graph)
        self.app.fit_view()
        self.root.update()
        x, y = map(round, self.app.transform(node.x + 120, node.y + 24))
        before, position = self.app.snapshot(), (node.x, node.y)
        canvas = self.app.canvas
        with patch.object(self.app, "open_function") as open_function:
            canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=24000)
            canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=24010)
            canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=24100)
            open_function.assert_not_called()
            canvas.event_generate("<Motion>", x=x + 42, y=y + 16, state=0x100, time=24120)
            canvas.event_generate("<ButtonRelease-1>", x=x + 42, y=y + 16, time=24140)
            self.root.update()
            open_function.assert_not_called()
        self.assertAlmostEqual(node.x - position[0], 42 / self.app.zoom)
        self.assertAlmostEqual(node.y - position[1], 16 / self.app.zoom)
        self.assertEqual(len(self.app.undo_stack), 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)

    def test_tk_function_call_double_click_opens_on_release_without_movement(self):
        graph = Graph()
        graph.add_node("Main", 40, 40)
        graph.add_function("calculate", ["value"], ["answer"])
        node = graph.add_call("calculate", 400, 40)
        self.app.replace_graph(graph)
        self.app.fit_view()
        self.root.update()
        x, y = map(round, self.app.transform(node.x + 120, node.y + 24))
        before = self.app.snapshot()
        canvas = self.app.canvas
        with patch.object(self.app, "open_function") as open_function:
            canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=25000)
            canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=25010)
            canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=25100)
            open_function.assert_not_called()
            canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=25110)
            self.root.update()
            open_function.assert_called_once_with("calculate")
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_tk_group_double_click_still_collapses_and_is_undoable(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        group_id = self.app.create_selection_group("Calculation")
        self.app.fit_view()
        self.root.update()
        left, top, right, bottom, _ = next(hit for hit in self.app.group_hits if hit[-1] == group_id)
        x, y = round(left + 70 * self.app.zoom), round(top + 16 * self.app.zoom)
        before = self.app.snapshot()
        depth = len(self.app.undo_stack)
        canvas = self.app.canvas
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=30000)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=30010)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y, time=30100)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y, time=30110)
        self.root.update()
        self.assertTrue(self.app.graph.groups[group_id]["collapsed"])
        self.assertEqual(len(self.app.undo_stack), depth + 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)

    def test_copy_paste_clones_only_internal_wires_and_is_one_undo(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        before = self.app.snapshot()
        original_ids = set(self.app.graph.nodes)
        original_positions = self.positions()
        self.app.copy_selected()
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])
        self.assert_single_undo(before, self.app.paste_selected)
        created = set(self.app.graph.nodes) - original_ids
        self.assertEqual(len(created), 2)
        clones = {self.app.graph.nodes[node_id].kind: self.app.graph.nodes[node_id] for node_id in created}
        clone_edges = [edge for edge in self.app.graph.edges
                       if edge.source in created or edge.target in created]
        self.assertEqual({(edge.source, edge.output, edge.target, edge.input) for edge in clone_edges},
                         {(clones["Function"].id, "next", clones["Custom"].id, "run"),
                          (clones["Function"].id, "result", clones["Custom"].id, "a")})
        dx = clones["Function"].x - original_positions["n2"][0]
        dy = clones["Function"].y - original_positions["n2"][1]
        self.assertNotEqual((dx, dy), (0, 0))
        self.assertAlmostEqual(clones["Custom"].x - original_positions["n3"][0], dx)
        self.assertAlmostEqual(clones["Custom"].y - original_positions["n3"][1], dy)

    def test_duplicate_and_delete_each_have_single_history_entry(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        before = self.app.snapshot()
        self.assert_single_undo(before, self.app.duplicate_selected)
        self.assertEqual(len(self.app.graph.nodes), 6)
        self.app.set_selection({"n2", "n3"}, primary="n2")
        before = self.app.snapshot()
        self.assert_single_undo(before, self.app.delete_selected)
        self.assertNotIn("n2", self.app.graph.nodes)
        self.assertNotIn("n3", self.app.graph.nodes)
        self.assertTrue(all(edge.source not in {"n2", "n3"} and edge.target not in {"n2", "n3"}
                            for edge in self.app.graph.edges))

    def test_alignment_moves_only_selection_and_is_one_undo(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        positions = self.positions()
        before = self.app.snapshot()
        self.assert_single_undo(before, lambda: self.app.align_selected("left"))
        self.assertEqual(self.app.graph.nodes["n2"].x, self.app.graph.nodes["n3"].x)
        self.assertEqual(self.app.graph.nodes["n2"].y, positions["n2"][1])
        self.assertEqual(self.app.graph.nodes["n3"].y, positions["n3"][1])
        for node_id in ("n1", "n4"):
            self.assertEqual(self.positions()[node_id], positions[node_id])
        depth = len(self.app.undo_stack)
        self.app.set_selection({"n2", "n3"}, primary="n2")
        self.app.align_selected("left")
        self.assertEqual(len(self.app.undo_stack), depth)

    def test_explicit_layout_follows_execution_order_and_is_one_undo(self):
        before, source = self.app.snapshot(), self.app.current_code
        self.assert_single_undo(before, self.app.auto_layout)
        execution_nodes = [self.app.graph.nodes[node_id] for node_id in ("n1", "n2", "n3", "n4")]
        self.assertTrue(all(left.x < right.x for left, right in zip(execution_nodes, execution_nodes[1:])))
        self.assertEqual(self.app.current_code, source)

    def test_load_add_and_navigation_preserve_manual_positions(self):
        graph = editing_graph()
        positions = {node.id: (node.x, node.y) for node in graph.nodes.values()}
        path = self.directory / "manual.drop.json"
        graph.save(path)
        self.app.replace_graph(Graph.load(path), path)
        self.assertEqual(self.positions(), positions)
        self.app.add_node("Variable", 1750, 730)
        for node_id, position in positions.items():
            self.assertEqual(self.positions()[node_id], position)
        before, depth = self.app.snapshot(), len(self.app.undo_stack)
        self.app.minimap_navigate(SimpleNamespace(x=140, y=65))
        self.app.fit_view()
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(len(self.app.undo_stack), depth)

    def test_group_creation_collapse_rename_and_ungroup_are_undoable(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        before = self.app.snapshot()
        group_id = self.app.create_selection_group("Calculation")
        self.assertIsNotNone(group_id)
        self.assertEqual(set(self.app.graph.groups[group_id]["nodes"]), {"n2", "n3"})
        self.assertEqual(self.app.graph.groups[group_id]["label"], "Calculation")
        after = self.app.snapshot()
        self.assertEqual(len(self.app.undo_stack), 1)
        self.app.undo()
        self.assertEqual(self.app.snapshot(), before)
        self.app.redo()
        self.assertEqual(self.app.snapshot(), after)
        positions, source = self.positions(), self.app.current_code
        self.assert_single_undo(self.app.snapshot(), lambda: self.app.toggle_group(group_id))
        self.assertTrue(self.app.graph.groups[group_id]["collapsed"])
        self.assertEqual(self.positions(), positions)
        self.assertEqual(self.app.current_code, source)
        self.assert_single_undo(self.app.snapshot(), lambda: self.app.rename_group(group_id, "Result stage"))
        self.assertEqual(self.app.graph.groups[group_id]["label"], "Result stage")
        self.app.set_selection({"n2", "n3"}, primary="n2", group_id=group_id)
        self.assert_single_undo(self.app.snapshot(), self.app.ungroup_selected)
        self.assertNotIn(group_id, self.app.graph.groups)
        self.assertEqual(self.positions(), positions)

    def test_collapsed_groups_hide_members_and_internal_wires_only(self):
        self.app.set_selection({"n2", "n3"}, primary="n2")
        group_id = self.app.create_selection_group("Calculation")
        self.app.toggle_group(group_id)
        self.app.fit_view()
        self.app.draw()
        self.assertEqual({node_id: self.app.collapsed_members[node_id] for node_id in ("n2", "n3")},
                         {"n2": group_id, "n3": group_id})
        self.assertTrue({hit[-1] for hit in self.app.node_hits}.isdisjoint({"n2", "n3"}))
        self.assertIn(group_id, {hit[-1] for hit in self.app.group_hits})
        visible_edges = list(self.app.edge_items.values())
        self.assertTrue(all(not (edge.source in {"n2", "n3"} and edge.target in {"n2", "n3"})
                            for edge in visible_edges))
        external = [edge for edge in self.app.graph.edges
                    if (edge.source in {"n2", "n3"}) != (edge.target in {"n2", "n3"})]
        self.assertCountEqual(visible_edges, external)
        self.app.toggle_group(group_id)
        self.app.draw()
        self.assertEqual(self.app.collapsed_members, {})
        self.assertCountEqual(self.app.edge_items.values(), self.app.graph.edges)

    def test_space_picker_is_nonmutating_until_accept_and_add_is_undoable(self):
        self.app.canvas.focus_force()
        self.root.update()
        before = self.app.snapshot()
        self.app.canvas.event_generate("<KeyPress-space>")
        self.root.update()
        self.assertIsNotNone(self.app.node_picker)
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])
        self.app.picker_query.set("Torque")
        self.root.update()
        self.assertEqual(self.app.picker_matches[0], "Torque")
        self.assert_single_undo(before, self.app.accept_node_picker)
        self.assertTrue(any(node.kind == "Torque" for node in self.app.graph.nodes.values()))

    def test_text_widgets_keep_native_shortcuts_without_graph_callbacks(self):
        import tkinter as tk
        from tkinter import ttk
        before = self.app.snapshot()
        widgets = [tk.Entry(self.root), tk.Text(self.root, height=1),
                   ttk.Combobox(self.root), tk.Spinbox(self.root, from_=0, to=10)]
        try:
            for widget in widgets:
                widget.place(x=20, y=20, width=150, height=30)
                widget.focus_force()
                self.root.update()
                action = Mock()
                self.app.graph_key(action)
                action.assert_not_called()
                for sequence in ("<Control-a>", "<Control-c>", "<Control-v>",
                                 "<Control-x>", "<Control-g>", "<Control-l>", "<space>"):
                    widget.event_generate(sequence)
                    self.root.update()
                self.assertEqual(self.app.snapshot(), before)
                self.assertIsNone(self.app.node_picker)
                self.assertEqual(self.app.undo_stack, [])
        finally:
            for widget in widgets:
                widget.destroy()

    def test_canvas_shortcuts_select_all_and_copy_without_mutation(self):
        self.app.canvas.focus_force()
        self.root.update()
        before = self.app.snapshot()
        self.app.canvas.event_generate("<Control-a>")
        self.root.update()
        self.assertEqual(set(self.app.current_selection()), set(self.app.graph.nodes))
        self.app.canvas.event_generate("<Control-c>")
        self.root.update()
        self.assertEqual(self.app.snapshot(), before)
        self.assertEqual(self.app.undo_stack, [])

    def test_copy_paste_library_reference_does_not_execute_or_install_code(self):
        marker = self.directory / "executed.txt"
        data = {"format": "pydrop-node-library", "schema_version": 1,
                "id": "clipboard-check", "name": "Clipboard check", "version": "1.0.0",
                "requirements": [], "nodes": [{"id": "probe", "version": "1.0.0",
                "name": "Probe", "category": "Tests", "description": "Execution boundary",
                "inputs": [], "outputs": [{"name": "value", "type": "Number"}],
                "implementation": f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nreturn {{'value': 1}}"}]}
        library = validate_library(data)
        reference = make_reference(library, library["nodes"][0])
        graph = Graph()
        graph.add_node("Main", 0, 0)
        node = graph.add_library_node(reference, 400, 0)
        self.app.replace_graph(graph)
        self.app.select(node.id)
        self.app.copy_selected()
        self.app.paste_selected()
        copies = [candidate for candidate in self.app.graph.nodes.values() if candidate.kind == "Library"]
        self.assertEqual(len(copies), 2)
        self.assertEqual(copies[0].props, copies[1].props)
        self.assertFalse(marker.exists())
        self.assertFalse(self.app.library_catalog.store.exists())

    def test_cut_retains_unique_entry_and_undo_restores_nodes(self):
        before = self.app.snapshot()
        self.app.set_selection({"n1", "n2", "n3"})
        self.assert_single_undo(before, self.app.cut_selected)
        self.assertIn("n1", self.app.graph.nodes)
        self.assertNotIn("n2", self.app.graph.nodes)
        self.assertNotIn("n3", self.app.graph.nodes)

    def test_group_label_drag_preserves_multi_group_selection(self):
        self.app.set_selection({"n2"})
        first = self.app.create_selection_group("First")
        self.app.set_selection({"n3"})
        second = self.app.create_selection_group("Second")
        self.app.toggle_group(first)
        self.app.toggle_group(second)
        self.app.set_selection({"n2", "n3"})
        positions, before = self.positions(), self.app.snapshot()
        left, top, right, bottom, key = next(hit for hit in self.app.group_hits if hit[-1] == first)
        start = SimpleNamespace(x=left+70*self.app.zoom,y=top+16*self.app.zoom,state=0)
        end = SimpleNamespace(x=start.x+40,y=start.y+20,state=0)
        def drag():
            self.app.canvas_press(start)
            self.app.canvas_drag(end)
            self.app.canvas_release(end)
        self.assert_single_undo(before, drag)
        for key in ("n2", "n3"):
            self.assertAlmostEqual(self.positions()[key][0]-positions[key][0],40/self.app.zoom)
            self.assertAlmostEqual(self.positions()[key][1]-positions[key][1],20/self.app.zoom)

    def test_undo_cancels_in_progress_drag_without_stale_nodes(self):
        self.app.select("n2")
        self.app.duplicate_selected()
        duplicate = self.app.graph.nodes[self.app.selected]
        before = self.app.snapshot()
        start = self.event_at(duplicate.x+110,duplicate.y+20)
        end = SimpleNamespace(x=start.x+30,y=start.y+20,state=0)
        self.app.canvas_press(start)
        self.app.canvas_drag(end)
        self.app.undo()  # Cancel the unfinished gesture first.
        self.assertEqual(self.app.snapshot(),before)
        self.assertIsNone(self.app.drag_node)
        self.app.undo()  # Remove the duplicated node.
        self.app.canvas_drag(end)
        self.assertEqual(len(self.app.graph.nodes),4)

    def test_edit_controls_remain_visible_at_minimum_window_size(self):
        self.root.geometry("1050x700")
        self.pump(.05)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for title in ("Edit ▾","Layout","Undo","Redo"):
            button = next(widget for widget in descendants(self.root) if widget.winfo_class() == "Button" and widget.cget("text") == title)
            self.assertTrue(button.winfo_ismapped(),title)
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width(),title)


if __name__ == "__main__":
    unittest.main()
