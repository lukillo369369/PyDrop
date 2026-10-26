"""Serial controls and real worker-process responsiveness without hardware."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph
from nodeforge import Editor, serial_run_allowance
from serial_runtime import SERIAL_KINDS


def serial_graph():
    graph = Graph()
    actions = [graph.add_node(kind, index * 320, 40) for index, kind in enumerate(
        ("Main", "Serial Connect", "Serial Send", "Serial Read Line", "Print", "Serial Disconnect"))]
    for previous, following in zip(actions, actions[1:]):
        graph.connect(previous.id, "next", following.id, "run")
    graph.connect(actions[3].id, "line", actions[4].id, "value")
    return graph


class SerialEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.temp = tempfile.TemporaryDirectory()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = Editor(self.root, serial_graph(), path=Path(self.temp.name) / "serial.drop.json")
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        self.app.closed = True
        if self.app.process is not None and self.app.process.poll() is None:
            self.app.process.kill()
            self.app.process.wait()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump_until(self, condition, seconds=8):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition(), "Serial worker did not complete")

    def delayed_compile(self, seconds):
        original = self.app.compile_project
        def compile_delayed(*args, **kwargs):
            compiled = original(*args, **kwargs)
            # Delay the action in the child, preserving the real protocol,
            # connection, compiler ordering, and editor pipe readers.
            anchor = "def _serial_read_line("
            position = compiled.code.index("\n", compiled.code.index(anchor)) + 1
            compiled.code = (compiled.code[:position] +
                             f"    import time as _test_time; _test_time.sleep({seconds})\n" +
                             compiled.code[position:])
            return compiled
        return compile_delayed

    def test_inspectors_accept_all_nodes_and_custom_port_and_baud(self):
        for kind in SERIAL_KINDS:
            node = next((node for node in self.app.graph.nodes.values() if node.kind == kind), None)
            if node is None:
                node = self.app.graph.add_node(kind, 0, 400)
            self.app.select(node.id)
            self.root.update()
        connect = next(node for node in self.app.graph.nodes.values() if node.kind == "Serial Connect")
        self.app.select(connect.id)
        self.assertEqual(self.app.form_vars["mode"].get(), "Simulation")
        self.app.form_vars["mode"].set("Hardware")
        self.app.form_vars["port"].set("COM42")
        self.app.form_vars["baud"].set("74880")
        self.assertTrue(self.app.apply_properties())
        self.assertEqual(connect.props["baud"], "74880")
        self.assertEqual(connect.props["port"], "COM42")

    def test_port_refresh_is_async_and_preserves_typed_port(self):
        connect = next(node for node in self.app.graph.nodes.values() if node.kind == "Serial Connect")
        self.app.select(connect.id)
        self.app.form_vars["port"].set("COM42")
        worker_threads = []
        def ports():
            worker_threads.append(threading.current_thread())
            time.sleep(.15)
            return ["COM9", "COM3"]
        beats = []
        self.root.after(20, lambda: beats.append(True))
        with patch("nodeforge._serial_ports", side_effect=ports):
            self.app.refresh_serial_ports()
            self.pump_until(lambda: not self.app.serial_scanning)
        self.assertEqual(beats, [True])
        self.assertIsNot(worker_threads[0], threading.main_thread())
        self.assertIn("COM9", self.app.serial_port_widget.cget("values"))
        self.assertEqual(self.app.form_vars["port"].get(), "COM42")

    def test_live_status_and_event_loop_during_serial_wait(self):
        with patch.object(self.app, "compile_project", side_effect=self.delayed_compile(.5)):
            self.app.run_graph()
            self.pump_until(lambda: self.app.serial_connected)
            self.assertTrue(self.app.busy)
            self.assertIn("Simulation", self.app.serial_status_var.get())
            self.pump_until(lambda: not self.app.busy)
        self.assertFalse(self.app.serial_connected)
        self.assertIn("isconnected", self.app.serial_status_var.get())
        output = self.app.output_text.get("1.0", "end")
        self.assertIn("temperature=23.5,light=512", output)
        self.assertIn("Finished successfully", output)
        self.assertNotIn("PYDROP_SERIAL:", output)

    def test_stop_releases_worker_and_clears_connected_state(self):
        with patch.object(self.app, "compile_project", side_effect=self.delayed_compile(3)):
            self.app.run_graph()
            self.pump_until(lambda: self.app.serial_connected)
            self.app.stop_run()
            self.pump_until(lambda: not self.app.busy, seconds=2)
        self.assertFalse(self.app.serial_connected)
        self.assertIn("stopped by user", self.app.output_text.get("1.0", "end"))
        self.assertEqual(str(self.app.stop_button.cget("state")), "disabled")

    def test_unconnected_send_selects_action_with_actionable_error(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        send = graph.add_node("Serial Send", 300, 0)
        graph.connect(main.id, "next", send.id, "run")
        self.app.replace_graph(graph)
        self.app.run_graph()
        self.pump_until(lambda: not self.app.busy)
        self.assertEqual(self.app.error_node, send.id)
        self.assertIn("Connect", self.app.status_var.get())
        self.assertFalse(self.app.serial_connected)

    def test_unplug_error_releases_connection_and_editor_can_run_again(self):
        original = self.app.compile_project
        def compile_unplugged(*args, **kwargs):
            compiled = original(*args, **kwargs)
            position = compiled.code.index("\n", compiled.code.index("def _serial_read_line(")) + 1
            compiled.code = (compiled.code[:position] +
                             "    _serial_transport_error('read', OSError('USB device removed'))\n" +
                             compiled.code[position:])
            return compiled
        with patch.object(self.app, "compile_project", side_effect=compile_unplugged):
            self.app.run_graph()
            self.pump_until(lambda: not self.app.busy)
        read = next(node for node in self.app.graph.nodes.values() if node.kind == "Serial Read Line")
        self.assertEqual(self.app.error_node, read.id)
        self.assertIn("unplugged", self.app.status_var.get())
        self.assertFalse(self.app.serial_connected)
        self.app.run_graph()
        self.pump_until(lambda: not self.app.busy)
        self.assertIn("Finished successfully", self.app.output_text.get("1.0", "end"))

    def test_final_connected_event_cannot_replay_after_completion(self):
        original = self.app.run_queue.get_nowait
        def receive_completion():
            result = original()
            self.app.serial_events.put((self.app.run_serial,
                                        [True, "Connected just before exit", "Simulation", "SIMULATED"]))
            return result
        with patch.object(self.app.run_queue, "get_nowait", side_effect=receive_completion):
            self.app.run_graph()
            self.pump_until(lambda: not self.app.busy)
        self.app.poll_serial()
        self.assertFalse(self.app.serial_connected)
        self.assertIn("disconnected", self.app.serial_status_var.get())

    def test_stop_during_connect_startup_clears_connecting_indicator(self):
        original = self.app.compile_project
        def compile_startup(*args, **kwargs):
            compiled = original(*args, **kwargs)
            position = compiled.code.index("\n", compiled.code.index("def _serial_connect(")) + 1
            compiled.code = (compiled.code[:position] +
                             "    _serial_emit('Connecting; waiting for Arduino startup...')\n"
                             "    import time as _test_time; _test_time.sleep(3)\n" +
                             compiled.code[position:])
            return compiled
        with patch.object(self.app, "compile_project", side_effect=compile_startup):
            self.app.run_graph()
            self.pump_until(lambda: "Connecting;" in self.app.serial_status_var.get())
            self.assertFalse(self.app.serial_connected)
            self.app.stop_run()
            self.pump_until(lambda: not self.app.busy, seconds=2)
        self.assertIn("disconnected", self.app.serial_status_var.get())
        self.assertIsNone(self.app.error_node)


class SerialRunBudgetTests(unittest.TestCase):
    def test_budget_counts_read_timeouts_and_repeated_function_calls(self):
        graph = Graph()
        graph.add_node("Main", 0, 0)
        definition = graph.add_function("sample", [], ["value"])
        body = Graph.from_dict(definition["graph"], _body=True)
        read = body.add_node("Serial Read Line", 300, 0)
        read.props["timeout"] = "12"
        definition["graph"] = body.to_dict()
        graph.add_call("sample", 300, 0)
        graph.add_call("sample", 600, 0)
        self.assertEqual(serial_run_allowance(graph, graph.functions), 26)

    def test_function_draft_budget_uses_parent_and_edited_definition(self):
        parent = Graph()
        parent.add_node("Main", 0, 0)
        definition = parent.add_function("sample", [], ["value"])
        draft = Graph.from_dict(definition["graph"], _body=True)
        draft.add_node("Serial Read Line", 300, 0).props["timeout"] = "20"
        parent.add_node("Serial Read Line", 300, 0).props["timeout"] = "30"
        parent.add_call("sample", 600, 0)
        parent.add_call("sample", 900, 0)
        editor = SimpleNamespace(graph=draft, function_owner=SimpleNamespace(graph=parent), function_name="sample")
        project = Editor.execution_project(editor)
        self.assertEqual(serial_run_allowance(project, project.functions), 31 + 21 * 2)
        self.assertNotEqual(project.functions["sample"]["graph"], parent.functions["sample"]["graph"])

    def test_nested_repeated_calls_calculate_budget_without_expanding_each_call(self):
        graph = Graph()
        previous = None
        for index in range(16):
            name = "sample_" + str(index)
            definition = graph.add_function(name, [], ["value"])
            body = Graph.from_dict(definition["graph"], _body=True)
            if previous is None:
                body.add_node("Serial Read Line", 300, 0).props["timeout"] = "2"
            else:
                body.add_call(previous, 300, 0, library=graph.functions)
                body.add_call(previous, 600, 0, library=graph.functions)
            definition["graph"] = body.to_dict()
            previous = name
        graph.add_call(previous, 300, 0)
        original = Graph.from_dict
        with patch.object(Graph, "from_dict", side_effect=original) as decode:
            self.assertEqual(serial_run_allowance(graph, graph.functions), 3 * 2**15)
        self.assertEqual(decode.call_count, 16)
