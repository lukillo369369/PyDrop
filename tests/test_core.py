"""Behavioral tests: run with python -m unittest discover -s tests -v."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, demo_graph, branch_demo, formula, identifier


def run(graph, trace=False):
    code = compile_graph(graph, trace=trace).code
    return subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code],
                          capture_output=True, text=True, encoding="utf-8", timeout=5)


class GraphTests(unittest.TestCase):
    def test_sketch_outputs_force(self):
        result = run(demo_graph())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Force (N): 24.525\n")

    def test_editing_input_changes_result(self):
        graph = demo_graph()
        graph.nodes["n2"].props["value"] = "10"
        self.assertEqual(run(graph).stdout, "Force (N): 98.1\n")

    def test_both_branch_paths(self):
        graph = branch_demo()
        self.assertEqual(run(graph).stdout, "Fan ON\n")
        graph.nodes["n2"].props["value"] = "79"
        self.assertEqual(run(graph).stdout, "Fan OFF\n")
        graph.nodes["n2"].props["value"] = "80"
        self.assertEqual(run(graph).stdout, "Fan ON\n")

    def test_runtime_division_error(self):
        graph = demo_graph()
        graph.nodes["n5"].props["expression"] = "a / c"
        result = run(graph)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ZeroDivisionError", result.stderr)

    def test_incompatible_pins_do_not_change_graph(self):
        graph = demo_graph()
        before = graph.to_dict()
        with self.assertRaises(GraphError):
            graph.connect("n1", "next", "n5", "a")
        self.assertEqual(graph.to_dict(), before)

    def test_data_cycle_rejected_atomically(self):
        graph = Graph()
        a, b = graph.add_node("Math", 0, 0), graph.add_node("Math", 300, 0)
        graph.connect(a.id, "result", b.id, "a")
        before = graph.to_dict()
        with self.assertRaisesRegex(GraphError, "cycle"):
            graph.connect(b.id, "result", a.id, "a")
        self.assertEqual(graph.to_dict(), before)

    def test_execution_cycle_rejected(self):
        graph = demo_graph()
        with self.assertRaisesRegex(GraphError, "cycle"):
            graph.connect("n6", "next", "n5", "run")
        self.assertEqual(run(graph).returncode, 0)

    def test_use_before_function_run_rejected(self):
        graph = demo_graph()
        graph.connect("n1", "next", "n6", "run")
        with self.assertRaisesRegex(GraphError, "must run before"):
            compile_graph(graph)

    def test_duplicate_names_rejected(self):
        graph = demo_graph()
        graph.nodes["n2"].props["name"] = "calculate_force"
        with self.assertRaisesRegex(GraphError, "used twice"):
            compile_graph(graph)

    def test_reserved_names_and_code_injection_rejected(self):
        for name in ("print", "isfinite", "float", "__import__", "x; print('bad')", "True"):
            with self.subTest(name=name), self.assertRaises(GraphError):
                identifier(name)
        for expression in ("__import__('os').system('echo nope')", "a.__class__", "[x for x in ()]",
                           "(lambda: 1)()", "open('x')", "a[0]", "'text' * 100000", "(a := 2)"):
            with self.subTest(expression=expression), self.assertRaises(GraphError):
                formula(expression)

    def test_supported_math_functions(self):
        graph = demo_graph()
        graph.nodes["n5"].props["expression"] = "round(sqrt(a) + sin(pi / 2) + max(b, c), 3)"
        self.assertEqual(run(graph).stdout, "Force (N): 12.391\n")

    def test_text_print_and_unicode(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        text = graph.add_node("Variable", 0, 200)
        text.props.update(type="Text", value="Cole's graph: α → Ω\nsecond line")
        out = graph.add_node("Print", 300, 0)
        out.props["label"] = ""
        graph.connect(main.id, "next", out.id, "run")
        graph.connect(text.id, "value", out.id, "value")
        self.assertEqual(run(graph).stdout, "Cole's graph: α → Ω\nsecond line\n")

    def test_text_not_accepted_by_numeric_function(self):
        graph = demo_graph()
        graph.nodes["n2"].props.update(type="Text", value="1000")
        result = run(graph)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be numbers", result.stderr)

    def test_boolean_default_branch(self):
        graph = branch_demo()
        graph.edges = [e for e in graph.edges if e.input != "condition"]
        graph.nodes["n4"].props["condition"] = "False"
        self.assertEqual(run(graph).stdout, "Fan OFF\n")

    def test_connected_inputs_override_defaults(self):
        graph = demo_graph()
        graph.nodes["n5"].props.update(a="100", b="100", c="100")
        self.assertEqual(run(graph).stdout, "Force (N): 24.525\n")

    def test_disconnected_inputs_use_defaults(self):
        graph = demo_graph()
        graph.edges = [e for e in graph.edges if e.target != "n5" or e.input != "a"]
        graph.nodes["n5"].props["a"] = "10"
        self.assertEqual(run(graph).stdout, "Force (N): 98.1\n")

    def test_reconnecting_input_replaces_wire(self):
        graph = demo_graph()
        graph.connect("n3", "value", "n5", "a")
        self.assertEqual(len([e for e in graph.edges if e.target == "n5" and e.input == "a"]), 1)
        self.assertEqual(run(graph).stdout, "Force (N): 96.236\n")

    def test_save_reload_preserves_graph_and_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = demo_graph()
            path = Path(directory) / "graph.nodeforge.json"
            graph.save(path)
            loaded = Graph.load(path)
            self.assertEqual(graph.to_dict(), loaded.to_dict())
            self.assertEqual(run(loaded).stdout, run(graph).stdout)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_empty_draft_round_trip(self):
        self.assertEqual(Graph.from_dict(Graph().to_dict()).nodes, {})

    def test_new_node_name_avoids_user_defined_name_collision(self):
        graph = Graph()
        graph.add_node("Main", 0, 0)
        existing = graph.add_node("Variable", 0, 0)
        existing.props["name"] = "value_3"
        added = graph.add_node("Variable", 0, 0)
        self.assertNotEqual(added.props["name"], "value_3")
        compile_graph(graph)

    def test_malformed_projects_fail_cleanly(self):
        base = demo_graph().to_dict()
        mutations = [lambda d: d.update(version=999),
                     lambda d: d["edges"][0].update(target="missing"),
                     lambda d: d["edges"].append(d["edges"][0].copy()),
                     lambda d: d["nodes"][0].update(x=float("nan")),
                     lambda d: d["nodes"][0].update(id="n1\nprint('x')"),
                     lambda d: d["nodes"][0].update(props=[]),
                     lambda d: d["nodes"][1]["props"].update(value="NaN")]
        for mutate in mutations:
            d = copy.deepcopy(base)
            mutate(d)
            with self.subTest(data=d), self.assertRaises(GraphError):
                Graph.from_dict(d)

    def test_trace_matches_executed_path(self):
        result = run(branch_demo(), trace=True)
        self.assertEqual([line for line in result.stderr.splitlines() if line.startswith("NODEFORGE_TRACE:")],
                         ["NODEFORGE_TRACE:n1", "NODEFORGE_TRACE:n4", "NODEFORGE_TRACE:n5"])

    def test_repeated_data_dependencies_do_not_expand_exponentially(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        source = graph.add_node("Variable", 0, 0)
        source.props["value"] = "1"
        port = "value"
        for _ in range(35):
            node = graph.add_node("Math", 0, 0)
            graph.connect(source.id, port, node.id, "a")
            graph.connect(source.id, port, node.id, "b")
            source, port = node, "result"
        out = graph.add_node("Print", 0, 0)
        graph.connect(main.id, "next", out.id, "run")
        graph.connect(source.id, port, out.id, "value")
        self.assertLess(len(compile_graph(graph).code), 10000)
        self.assertEqual(run(graph).stdout, "Result: 34359738368.0\n")

    def test_branch_function_result_cannot_leak_to_sibling(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        branch = graph.add_node("Branch", 0, 0)
        function = graph.add_node("Function", 0, 0)
        out = graph.add_node("Print", 0, 0)
        graph.connect(main.id, "next", branch.id, "run")
        graph.connect(branch.id, "true", function.id, "run")
        graph.connect(branch.id, "false", out.id, "run")
        graph.connect(function.id, "result", out.id, "value")
        with self.assertRaisesRegex(GraphError, "must run before"):
            compile_graph(graph)

    def test_command_line_export_runs_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "export.py"
            app = Path(__file__).resolve().parents[1] / "nodeforge.py"
            result = subprocess.run([sys.executable, str(app), "--export", str(dest)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(dest)], capture_output=True, text=True, timeout=5, cwd=directory)
            self.assertEqual(result.stdout, "Force (N): 24.525\n")


if __name__ == "__main__":
    unittest.main()
