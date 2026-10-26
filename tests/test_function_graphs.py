"""Reusable graph functions, standalone Python, and legacy persistence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, demo_graph
from example_graphs import reusable_rectangle_demo


def run(graph, trace=False, answers=None):
    return subprocess.run([sys.executable, "-I", "-c", compile_graph(graph, trace).code],
                          input=answers, capture_output=True, text=True, timeout=5)


class FunctionGraphTests(unittest.TestCase):
    def identity(self):
        graph = Graph()
        definition = graph.add_function("identity", ["argument"], ["answer"])
        body = Graph.from_dict(definition["graph"])
        body.connect("n2", "argument", "n3", "answer")
        definition["graph"] = body.to_dict()
        main = graph.add_node("Main", 0, 0)
        call = graph.add_call("identity", 300, 0)
        call.props["inputs"][0]["default"] = "7"
        output = graph.add_node("Print", 600, 0)
        output.props["label"] = ""
        graph.connect(main.id, "next", call.id, "run")
        graph.connect(call.id, "next", output.id, "run")
        graph.connect(call.id, "answer", output.id, "value")
        return graph, call

    def test_same_definition_twice_multiple_returns_standalone(self):
        graph = reusable_rectangle_demo()
        code = compile_graph(graph).code
        self.assertEqual(code.count("def rectangle_metrics("), 1)
        self.assertIn("def rectangle_metrics(width, height):", code)
        self.assertNotIn("graph_core", code)
        namespace = {"__name__": "exported_library"}
        exec(code, namespace)
        self.assertEqual(namespace["rectangle_metrics"](4, 5), (20.0, 18.0))
        result = run(graph)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "First area: 6.0\nFirst perimeter: 10.0\nSecond area: 20.0\nSecond perimeter: 18.0\n")

    def test_roundtrip_and_old_project_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"functions.drop.json"
            graph = reusable_rectangle_demo()
            graph.save(path)
            loaded = Graph.load(path)
            self.assertEqual(graph.to_dict(), loaded.to_dict())
            self.assertEqual(run(graph).stdout, run(loaded).stdout)
        legacy = demo_graph().to_dict()
        self.assertNotIn("functions", legacy)
        self.assertEqual(Graph.from_dict(legacy).to_dict(), legacy)
        self.assertEqual(run(Graph.from_dict(legacy)).returncode, 0)

    def test_body_edit_updates_every_call(self):
        graph = reusable_rectangle_demo()
        body = graph.functions["rectangle_metrics"]["graph"]
        next(node for node in body["nodes"] if node["id"] == "n4")["props"]["operator"] = "+"
        result = run(graph)
        self.assertIn("First area: 5.0", result.stdout)
        self.assertIn("Second area: 9.0", result.stdout)

    def test_body_drafts_and_serialized_properties_are_independent(self):
        graph, _ = self.identity()
        before = copy.deepcopy(graph.to_dict())
        draft = Graph.from_dict(graph.functions["identity"]["graph"])
        draft.nodes["n3"].props["values"][0]["default"] = "99"
        draft.nodes["n2"].props["names"].append("extra")
        serialized = graph.to_dict()
        serialized["nodes"][1]["props"]["inputs"][0]["default"] = "22"
        self.assertEqual(graph.to_dict(), before)

    def test_connected_arguments_override_defaults_and_allow_text(self):
        graph, call = self.identity()
        text = graph.add_node("Variable", 0, 200)
        text.props.update(name="greeting", type="Text", value="Hello Ω")
        graph.connect(text.id, "value", call.id, "argument")
        result = run(graph)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Hello Ω\n")

    def test_call_must_execute_before_consuming_results(self):
        graph, call = self.identity()
        graph.connect("n1", "next", "n3", "run")
        with self.assertRaisesRegex(GraphError, "must run before"):
            compile_graph(graph)

    def test_argument_names_do_not_collide_with_trace_imports(self):
        for name in ("sys", "json"):
            with self.subTest(name=name):
                graph, call = self.identity()
                definition = graph.functions["identity"]
                definition["inputs"] = [name]
                body = definition["graph"]
                body["nodes"][1]["props"]["names"] = [name]
                next(edge for edge in body["edges"] if edge["source"] == "n2")["output"] = name
                call.props["inputs"][0]["name"] = name
                result = run(graph, trace=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "7.0\n")

    def test_nested_calls(self):
        graph, call = self.identity()
        definition = graph.add_function("outer", ["item"], ["result"])
        body = Graph.from_dict(definition["graph"])
        nested = body.add_call("identity", 350, 0, graph.functions)
        body.connect("n1", "next", nested.id, "run")
        body.connect("n2", "item", nested.id, "argument")
        body.connect(nested.id, "next", "n3", "run")
        body.connect(nested.id, "answer", "n3", "result")
        definition["graph"] = body.to_dict()
        # Add another caller without changing the original identity caller.
        second = graph.add_call("outer", 300, 300)
        second.props["inputs"][0]["default"] = "11"
        graph.connect("n3", "next", second.id, "run")
        output = graph.add_node("Print", 600, 300)
        output.props["label"] = ""
        graph.connect(second.id, "next", output.id, "run")
        graph.connect(second.id, "result", output.id, "value")
        self.assertEqual(run(graph).stdout, "7.0\n11.0\n")

    def test_all_paths_need_return_and_branch_returns_work(self):
        graph, call = self.identity()
        definition = graph.functions["identity"]
        body = Graph.from_dict(definition["graph"])
        branch = body.add_node("Branch", 300, 0)
        body.connect("n1", "next", branch.id, "run")
        body.connect(branch.id, "true", "n3", "run")
        definition["graph"] = body.to_dict()
        with self.assertRaisesRegex(GraphError, "Every execution path"):
            compile_graph(graph)
        other = body.add_node("Return", 600, 300)
        other.props = {"values": [{"name": "answer", "default": "99"}]}
        body.connect(branch.id, "false", other.id, "run")
        for condition, expected in (("True", "7.0\n"), ("False", "99.0\n")):
            branch.props["condition"] = condition
            definition["graph"] = body.to_dict()
            self.assertEqual(run(graph).stdout, expected)

    def test_missing_function_and_stale_signature_fail_cleanly(self):
        graph, call = self.identity()
        call.props["function"] = "missing"
        with self.assertRaisesRegex(GraphError, "missing"):
            compile_graph(graph)
        call.props["function"] = "identity"
        call.props["returns"] = ["different"]
        with self.assertRaisesRegex(GraphError, "outdated interface"):
            compile_graph(graph)

    def test_recursion_direct_and_indirect_rejected(self):
        for indirect in (False, True):
            with self.subTest(indirect=indirect):
                graph, _ = self.identity()
                if indirect:
                    graph.add_function("other", ["argument"], ["answer"])
                targets = {"identity": "other", "other": "identity"} if indirect else {"identity": "identity"}
                for name, target in targets.items():
                    definition = graph.functions[name]
                    body = Graph.from_dict(definition["graph"])
                    body.add_call(target, 300, 0, graph.functions)
                    definition["graph"] = body.to_dict()
                with self.assertRaisesRegex(GraphError, "Recursive"):
                    compile_graph(graph)

    def test_interface_validation_and_no_partial_creation(self):
        graph = Graph()
        for name, inputs, returns in (("print", [], ["answer"]), ("okay", ["same", "same"], ["answer"]),
                                       ("okay", ["run"], ["answer"]), ("okay", [], []),
                                       ("okay", [], ["next"]), ("okay", ["bad name"], ["answer"])):
            with self.subTest(name=name, inputs=inputs, returns=returns):
                before = graph.to_dict()
                with self.assertRaises(GraphError):
                    graph.add_function(name, inputs, returns)
                self.assertEqual(before, graph.to_dict())

    def test_wrong_scope_nodes_and_shadowed_arguments_rejected(self):
        graph = demo_graph()
        graph.add_node("Return", 0, 0)
        with self.assertRaisesRegex(GraphError, "Return pins"):
            compile_graph(graph)
        graph, _ = self.identity()
        definition = graph.functions["identity"]
        body = Graph.from_dict(definition["graph"])
        node = body.add_node("Variable", 0, 0)
        node.props["name"] = "argument"
        definition["graph"] = body.to_dict()
        with self.assertRaisesRegex(GraphError, "Local names"):
            compile_graph(graph)

    def test_interactive_body_and_trace_ids_are_scoped(self):
        graph, _ = self.identity()
        definition = graph.functions["identity"]
        body = Graph.from_dict(definition["graph"])
        question = body.add_node("Input", 300, 0)
        question.props["prompt"] = "Who? "
        body.connect("n1", "next", question.id, "run")
        body.connect(question.id, "next", "n3", "run")
        body.connect(question.id, "result", "n3", "answer")
        definition["graph"] = body.to_dict()
        normal = run(graph, answers="Ada\n")
        self.assertEqual(normal.stdout, "Who? Ada\n")
        traced = run(graph, trace=True, answers=json.dumps("Ada")+"\n")
        self.assertEqual(traced.returncode, 0, traced.stderr)
        self.assertEqual(traced.stdout, "Ada\n")
        self.assertIn('PYDROP_INPUT:["identity/n4", "Who? "]', traced.stderr)
        self.assertIn("NODEFORGE_TRACE:identity/n1", traced.stderr)

    def test_malformed_nested_library_rejected_without_recursive_load(self):
        graph, _ = self.identity()
        data = graph.to_dict()
        data["functions"]["identity"]["graph"]["functions"] = copy.deepcopy(data["functions"])
        with self.assertRaisesRegex(GraphError, "project level"):
            Graph.from_dict(data)


if __name__ == "__main__":
    unittest.main()
