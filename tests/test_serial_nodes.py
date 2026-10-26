"""Serial graph contracts and isolated hardware-free Python exports."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, SERIAL_KINDS, compile_graph, ports


def serial_graph():
    graph = Graph()
    main = graph.add_node("Main", 0, 0)
    connect = graph.add_node("Serial Connect", 250, 0)
    send = graph.add_node("Serial Send", 500, 0)
    read = graph.add_node("Serial Read Line", 750, 0)
    parse = graph.add_node("Parse Value", 750, 250)
    output = graph.add_node("Print", 1000, 0)
    output.props["label"] = "Temperature: "
    disconnect = graph.add_node("Serial Disconnect", 1250, 0)
    for before, after in ((main, connect), (connect, send), (send, read),
                          (read, output), (output, disconnect)):
        graph.connect(before.id, "next", after.id, "run")
    graph.connect(read.id, "line", parse.id, "line")
    graph.connect(parse.id, "result", output.id, "value")
    return graph, connect, send, read, parse, disconnect


def run_export(graph, trace=False):
    """A moved export must run with only Python and no checkout imports."""
    code = compile_graph(graph, trace=trace).code
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "sensor_export.py"
        script.write_text(code, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-X", "utf8", str(script)],
                              cwd=directory, capture_output=True, text=True,
                              encoding="utf-8", timeout=10)


class SerialNodeTests(unittest.TestCase):
    def test_node_defaults_ports_and_project_roundtrip(self):
        graph, connect, send, read, parse, disconnect = serial_graph()
        self.assertEqual(connect.props,
                         {"port": "SIMULATED", "baud": "9600", "timeout": "1", "mode": "Simulation"})
        self.assertEqual(send.props, {"command": "READ", "newline": "LF"})
        self.assertEqual(read.props, {"timeout": "1"})
        self.assertEqual(parse.props, {"line": "", "key": "temperature", "separator": ","})
        self.assertEqual(disconnect.props, {})
        self.assertEqual(ports(parse), ({"line": "data"}, {"result": "data"}))
        self.assertEqual(ports(read)[1], {"next": "exec", "line": "data", "received": "data", "status": "data"})
        loaded = Graph.from_dict(json.loads(json.dumps(graph.to_dict())))
        self.assertEqual(loaded.to_dict(), graph.to_dict())
        self.assertEqual({node.kind for node in graph.nodes.values()} & set(SERIAL_KINDS), set(SERIAL_KINDS))

    def test_simulated_sensor_flow_runs_without_hardware_or_local_modules(self):
        graph, *_ = serial_graph()
        result = run_export(graph)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("Temperature: "), result.stdout)
        temperature = float(result.stdout.strip().split(": ", 1)[1])
        self.assertTrue(math.isfinite(temperature))
        self.assertNotIn("Traceback", result.stderr)

    def test_trace_reports_connection_and_each_serial_output(self):
        graph, connect, send, read, parse, disconnect = serial_graph()
        result = run_export(graph, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        statuses = [json.loads(line[len("PYDROP_SERIAL:"):])
                    for line in result.stderr.splitlines() if line.startswith("PYDROP_SERIAL:")]
        self.assertTrue(any(status[0] is True and status[2] == "Simulation" for status in statuses), statuses)
        self.assertIs(statuses[-1][0], False)
        for node in (connect, send, read, parse, disconnect):
            self.assertIn('PYDROP_VALUE:["' + node.id + '"', result.stderr)

    def test_send_and_read_require_explicit_connection_at_runtime(self):
        for kind in ("Serial Send", "Serial Read Line"):
            with self.subTest(kind=kind):
                graph = Graph()
                main = graph.add_node("Main", 0, 0)
                action = graph.add_node(kind, 250, 0)
                graph.connect(main.id, "next", action.id, "run")
                result = run_export(graph, trace=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr.lower(), r"connect|connection")
                self.assertIn("PYDROP_ERROR:" + action.id, result.stderr)

    def test_command_data_wire_overrides_default(self):
        graph, _, send, read, _, _ = serial_graph()
        command = graph.add_node("Variable", 250, 250)
        command.props.update(name="sensor_command", type="Text", value="READ")
        send.props["command"] = "WRONG_DEFAULT"
        graph.connect(command.id, "value", send.id, "command")
        result = run_export(graph)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Temperature: ", result.stdout)

    def test_execution_outputs_are_unavailable_before_their_nodes_run(self):
        for kind, pin in (("Serial Connect", "connected"), ("Serial Send", "sent"),
                          ("Serial Read Line", "line"), ("Serial Disconnect", "status")):
            with self.subTest(kind=kind):
                graph = Graph()
                main = graph.add_node("Main", 0, 0)
                source = graph.add_node(kind, 0, 250)
                output = graph.add_node("Print", 250, 0)
                graph.connect(main.id, "next", output.id, "run")
                graph.connect(source.id, pin, output.id, "value")
                with self.assertRaisesRegex(GraphError, "must run before"):
                    compile_graph(graph)

    def test_branch_cannot_use_read_from_other_branch(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        branch = graph.add_node("Branch", 250, 0)
        read = graph.add_node("Serial Read Line", 500, 0)
        output = graph.add_node("Print", 500, 250)
        graph.connect(main.id, "next", branch.id, "run")
        graph.connect(branch.id, "true", read.id, "run")
        graph.connect(branch.id, "false", output.id, "run")
        graph.connect(read.id, "line", output.id, "value")
        with self.assertRaisesRegex(GraphError, "must run before"):
            compile_graph(graph)

    def test_parse_value_is_pure_and_checked_when_used(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        parse = graph.add_node("Parse Value", 0, 250)
        parse.props.update(line="temperature=24.75,humidity=50")
        output = graph.add_node("Print", 250, 0)
        output.props["label"] = ""
        graph.connect(main.id, "next", output.id, "run")
        graph.connect(parse.id, "result", output.id, "value")
        result = run_export(graph)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "24.75\n")
        parse.props["line"] = "temperature=NaN"
        result = run_export(graph, trace=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PYDROP_ERROR:" + parse.id, result.stderr)

    def test_invalid_settings_are_rejected_without_opening_a_port(self):
        cases = [
            ("Serial Connect", "baud", "0"), ("Serial Connect", "baud", "9600.5"),
            ("Serial Connect", "baud", "4000001"), ("Serial Connect", "baud", "True"),
            ("Serial Connect", "timeout", "-1"), ("Serial Connect", "timeout", "31"),
            ("Serial Connect", "timeout", "NaN"), ("Serial Connect", "mode", "Auto"),
            ("Serial Read Line", "timeout", "inf"), ("Serial Read Line", "timeout", "-0.01"),
            ("Serial Read Line", "timeout", "30.001"),
            ("Serial Send", "newline", "automatic"), ("Serial Send", "command", "x" * 2001),
            ("Parse Value", "separator", ""),
        ]
        for kind, key, invalid in cases:
            with self.subTest(kind=kind, key=key, invalid=str(invalid)[:30]):
                graph = Graph()
                graph.add_node("Main", 0, 0)
                node = graph.add_node(kind, 250, 0)
                node.props[key] = invalid
                with self.assertRaises(GraphError):
                    compile_graph(graph)
        graph = Graph()
        graph.add_node("Main", 0, 0)
        connect = graph.add_node("Serial Connect", 250, 0)
        connect.props.update(mode="Hardware", port=" ")
        with self.assertRaisesRegex(GraphError, "port"):
            compile_graph(graph)

    def test_main_always_cleans_up_even_when_called_as_a_library(self):
        graph, *_ = serial_graph()
        namespace = {"__name__": "serial_export_library"}
        exec(compile_graph(graph).code, namespace)
        cleanup = []
        namespace["_serial_connect"] = lambda *args: {"connected": True, "status": "connected"}
        namespace["_serial_cleanup"] = lambda: cleanup.append("closed")
        namespace["_serial_send"] = lambda *args: (_ for _ in ()).throw(RuntimeError("device unplugged"))
        with self.assertRaisesRegex(RuntimeError, "device unplugged"):
            namespace["main"]()
        self.assertEqual(cleanup, ["closed"])

    def test_reusable_functions_share_one_embedded_connection(self):
        graph = Graph()
        definition = graph.add_function("read_sensor", [], ["temperature"])
        body = Graph.from_dict(definition["graph"])
        send = body.add_node("Serial Send", 250, 0)
        read = body.add_node("Serial Read Line", 500, 0)
        parse = body.add_node("Parse Value", 500, 250)
        body.connect("n1", "next", send.id, "run")
        body.connect(send.id, "next", read.id, "run")
        body.connect(read.id, "next", "n3", "run")
        body.connect(read.id, "line", parse.id, "line")
        body.connect(parse.id, "result", "n3", "temperature")
        definition["graph"] = body.to_dict()
        main = graph.add_node("Main", 0, 0)
        connect = graph.add_node("Serial Connect", 250, 0)
        call = graph.add_call("read_sensor", 500, 0)
        output = graph.add_node("Print", 750, 0)
        output.props["label"] = ""
        graph.connect(main.id, "next", connect.id, "run")
        graph.connect(connect.id, "next", call.id, "run")
        graph.connect(call.id, "next", output.id, "run")
        graph.connect(call.id, "temperature", output.id, "value")
        code = compile_graph(graph).code
        self.assertEqual(code.count("class SerialError("), 1)
        self.assertEqual(code.count("def _serial_connect("), 1)
        result = run_export(graph, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(math.isfinite(float(result.stdout.strip())))
        self.assertIn("read_sensor/" + read.id, result.stderr)


if __name__ == "__main__":
    unittest.main()
