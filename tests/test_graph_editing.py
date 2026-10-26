"""Clipboard, alignment, and group metadata without starting a desktop."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, demo_graph
from graph_editing import (alignment_positions, copy_fragment, create_group,
                           group_bounds, paste_fragment, ungroup)
from node_libraries import make_reference, validate_library


class ClipboardTests(unittest.TestCase):
    def simple(self):
        graph = Graph()
        main = graph.add_node("Main", -300, 0)
        number = graph.add_node("Variable", 50, 80)
        calculation = graph.add_node("Math", 400, 180)
        output = graph.add_node("Print", 800, 200)
        graph.connect(number.id, "value", calculation.id, "a")
        graph.connect(main.id, "next", output.id, "run")
        graph.connect(calculation.id, "result", output.id, "value")
        return graph, number, calculation, output

    def reusable(self):
        graph = Graph()
        graph.add_function("inner", ["item"], ["answer"])
        definition = graph.add_function("outer", [], ["answer"])
        body = Graph.from_dict(definition["graph"], _body=True)
        body.add_call("inner", 400, 200, graph.functions)
        definition["graph"] = body.to_dict()
        graph.add_function("unused", [], ["result"])
        call = graph.add_call("outer", 20, 30)
        return graph, call

    def test_internal_wires_only_and_relative_positions(self):
        graph, number, calculation, output = self.simple()
        before = graph.to_dict()
        fragment = copy_fragment(graph, [number.id, calculation.id])
        self.assertEqual(len(fragment["edges"]), 1)
        result, added = paste_fragment(graph, fragment, -100, -200)
        self.assertEqual((result.nodes[added[0]].x, result.nodes[added[0]].y), (-100, -200))
        self.assertEqual((result.nodes[added[1]].x, result.nodes[added[1]].y), (250, -100))
        self.assertEqual(result.input_edge(added[1], "a").source, added[0])
        self.assertEqual(result.input_edge(output.id, "value").source, calculation.id)
        self.assertEqual(graph.to_dict(), before)
        self.assertEqual([result.nodes[key].props["name"] for key in added[:1]], [number.props["name"]+"_2"])

    def test_names_avoid_both_existing_and_new_nodes(self):
        graph = Graph()
        nodes = [graph.add_node(kind, index*300, 0) for index, kind in enumerate(("Variable", "Function", "Assign"))]
        nodes[0].props["name"] = "captured"
        nodes[1].props["name"] = "captured_2"
        nodes[2].props["name"] = "captured_3"
        result, added = paste_fragment(graph, copy_fragment(graph, graph.nodes), 0, 500)
        names = [result.nodes[key].props["name"] for key in added]
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(set(names).isdisjoint({"captured", "captured_2", "captured_3"}))
        result.add_node("Main", -300, 0)
        result.validate()

    def test_long_names_remain_valid(self):
        graph = Graph()
        node = graph.add_node("Variable", 0, 0)
        node.props["name"] = "a"*48
        result, added = paste_fragment(graph, copy_fragment(graph, [node.id]), 0, 0)
        self.assertEqual(len(result.nodes[added[0]].props["name"]), 48)

    def test_independent_clipboard_and_nested_properties(self):
        graph, number, _, _ = self.simple()
        fragment = copy_fragment(graph, [number.id])
        result, added = paste_fragment(graph, fragment, 0, 0)
        result.nodes[added[0]].props["value"] = "99"
        self.assertEqual(fragment["nodes"][0]["props"]["value"], "10")
        fragment["nodes"][0]["props"]["value"] = "42"
        self.assertEqual(number.props["value"], "10")

    def test_structural_nodes_rejected(self):
        for kind in ("Main", "Function Inputs"):
            with self.subTest(kind=kind):
                graph = Graph()
                node = graph.add_node(kind, 0, 0)
                with self.assertRaisesRegex(GraphError, "structural"):
                    copy_fragment(graph, [node.id])
                fragment = {"format": "pydrop-fragment", "version": 1, "nodes": [node.to_dict()], "edges": []}
                with self.assertRaisesRegex(GraphError, "structural"):
                    paste_fragment(Graph(), fragment, 0, 0)

    def test_empty_and_unknown_selection_rejected(self):
        graph, *_ = self.simple()
        for selected in ([], ["n999"]):
            with self.subTest(selected=selected), self.assertRaisesRegex(GraphError, "Select existing"):
                copy_fragment(graph, selected)

    def test_malformed_paste_is_atomic(self):
        graph, number, *_ = self.simple()
        before = graph.to_dict()
        good = copy_fragment(graph, [number.id])
        bad = copy.deepcopy(good)
        bad["edges"] = [{"source": "n999", "output": "value", "target": number.id, "input": "a"}]
        for fragment in ({}, bad):
            with self.subTest(fragment=fragment), self.assertRaises(GraphError):
                paste_fragment(graph, fragment, 0, 0)
            self.assertEqual(graph.to_dict(), before)
        with self.assertRaises(GraphError):
            paste_fragment(graph, good, float("nan"), 0)
        self.assertEqual(graph.to_dict(), before)

    def test_paste_respects_node_limit(self):
        graph = Graph()
        for _ in range(120):
            graph.add_node("Math", 0, 0)
        before = graph.to_dict()
        with self.assertRaisesRegex(GraphError, "120"):
            paste_fragment(graph, copy_fragment(graph, ["n1"]), 0, 0)
        self.assertEqual(graph.to_dict(), before)

    def test_function_closure_only_and_identity_preserved(self):
        graph, call = self.reusable()
        fragment = copy_fragment(graph, [call.id])
        self.assertEqual(set(fragment["functions"]), {"outer", "inner"})
        result, added = paste_fragment(Graph(), fragment, 100, 200)
        self.assertEqual(set(result.functions), {"outer", "inner"})
        self.assertEqual(result.nodes[added[0]].props["function"], "outer")
        same, _ = paste_fragment(result, fragment, 500, 200)
        self.assertEqual(same.functions, result.functions)

    def test_conflicting_definition_rejected_without_overwrite(self):
        graph, call = self.reusable()
        fragment = copy_fragment(graph, [call.id])
        graph.functions["inner"]["graph"]["nodes"][-1]["props"]["values"][0]["default"] = "8"
        before = graph.to_dict()
        with self.assertRaisesRegex(GraphError, "inner.*conflicts"):
            paste_fragment(graph, fragment, 0, 0)
        self.assertEqual(graph.to_dict(), before)

    def test_imported_function_cannot_shadow_existing_local(self):
        graph, call = self.reusable()
        target = Graph()
        target.add_node("Variable", 0, 0).props["name"] = "inner"
        with self.assertRaisesRegex(GraphError, "inner.*local name"):
            paste_fragment(target, copy_fragment(graph, [call.id]), 0, 0)

    def test_function_body_context_preserves_empty_library(self):
        graph, call = self.reusable()
        body = Graph.from_dict(graph.functions["outer"]["graph"], _body=True)
        nested = next(node for node in body.nodes.values() if node.kind == "Function Call")
        fragment = copy_fragment(body, [nested.id], library=graph.functions)
        result, added = paste_fragment(body, fragment, 0, 0, library=graph.functions)
        self.assertEqual(result.functions, {})
        self.assertEqual(result.nodes[added[0]].props["function"], "inner")
        Graph.from_dict(result.to_dict(), _body=True)
        with self.assertRaisesRegex(GraphError, "Paste into the project graph first"):
            paste_fragment(body, fragment, 0, 0, library={})

    def test_missing_and_outdated_functions_reported(self):
        graph, call = self.reusable()
        call.props["returns"] = ["changed"]
        with self.assertRaisesRegex(GraphError, "outdated interface"):
            copy_fragment(graph, [call.id])
        call.props["returns"] = ["answer"]
        del graph.functions["inner"]
        with self.assertRaisesRegex(GraphError, "inner.*missing"):
            copy_fragment(graph, [call.id])

    def test_library_copy_keeps_reference_without_python_or_installation(self):
        data = {"format": "pydrop-node-library", "schema_version": 1, "id": "tools", "name": "Tools", "version": "1.0.0",
                "requirements": [], "nodes": [{"id": "double", "version": "1.0.0", "name": "Double", "category": "Examples",
                "description": "Double a value", "inputs": [{"name": "value", "type": "Number", "default": "0"}],
                "outputs": [{"name": "result", "type": "Number"}], "implementation": "return value * 2"}]}
        library = validate_library(data)
        graph = Graph()
        node = graph.add_library_node(make_reference(library, library["nodes"][0]), 0, 0)
        fragment = copy_fragment(graph, [node.id])
        self.assertNotIn("implementation", json.dumps(fragment))
        result, added = paste_fragment(graph, fragment, 100, 200)
        self.assertEqual(result.nodes[added[0]].props, node.props)

    def test_engineering_fragment_keeps_units_and_wires(self):
        graph = Graph()
        quantity = graph.add_node("Quantity", 0, 0)
        convert = graph.add_node("Unit Convert", 400, 0)
        graph.connect(quantity.id, "result", convert.id, "value")
        result, added = paste_fragment(Graph(), copy_fragment(graph, graph.nodes), 50, 60)
        self.assertEqual(result.nodes[added[0]].props["unit"], "m")
        self.assertEqual(result.nodes[added[1]].props["unit"], "cm")
        self.assertEqual(result.input_edge(added[1], "value").source, added[0])


class GroupTests(unittest.TestCase):
    def graph(self):
        graph = Graph()
        first = graph.add_node("Variable", 100, 200)
        second = graph.add_node("Math", 500, 300)
        return graph, first, second

    def test_legacy_roundtrip_omits_empty_groups(self):
        graph = demo_graph()
        data = graph.to_dict()
        self.assertNotIn("groups", data)
        self.assertEqual(Graph.from_dict(data).to_dict(), data)

    def test_group_roundtrip_and_compilation_unchanged(self):
        graph = demo_graph()
        code = compile_graph(graph).code
        group_id = create_group(graph, ["n2", "n3"], "Force inputs")
        graph.groups[group_id]["collapsed"] = True
        result = Graph.from_dict(graph.to_dict())
        self.assertEqual(result.to_dict(), graph.to_dict())
        self.assertEqual(compile_graph(result).code, code)

    def test_function_body_groups_roundtrip(self):
        graph = Graph()
        definition = graph.add_function("operation", [], ["answer"])
        body = Graph.from_dict(definition["graph"], _body=True)
        create_group(body, body.nodes, "Reusable body")
        definition["graph"] = body.to_dict()
        loaded = Graph.from_dict(graph.to_dict())
        self.assertEqual(loaded.functions, graph.functions)

    def test_groups_cannot_overlap_and_error_is_atomic(self):
        graph, first, second = self.graph()
        create_group(graph, [first.id], "First")
        before = graph.to_dict()
        with self.assertRaisesRegex(GraphError, "only one group"):
            create_group(graph, [first.id, second.id], "Second")
        self.assertEqual(graph.to_dict(), before)

    def test_invalid_groups_fail_at_load(self):
        graph, first, _ = self.graph()
        good = {"label": "Examples", "nodes": [first.id], "collapsed": False}
        invalid = [("g0", good), ("g1", {**good, "label": " "}), ("g1", {**good, "label": "x"*121}),
                   ("g1", {**good, "nodes": []}), ("g1", {**good, "nodes": ["n999"]}),
                   ("g1", {**good, "nodes": [first.id, first.id]}), ("g1", {**good, "collapsed": 1})]
        for key, group in invalid:
            with self.subTest(group=group):
                data = graph.to_dict()
                data["groups"] = {key: group}
                with self.assertRaises(GraphError):
                    Graph.from_dict(data)

    def test_remove_node_cleans_members_and_empty_group(self):
        graph, first, second = self.graph()
        group_id = create_group(graph, [first.id, second.id], "Calculation")
        graph.remove_node(first.id)
        self.assertEqual(graph.groups[group_id]["nodes"], [second.id])
        graph.remove_node(second.id)
        self.assertEqual(graph.groups, {})

    def test_ungroup_keeps_nodes_and_wires(self):
        graph, first, second = self.graph()
        graph.connect(first.id, "value", second.id, "a")
        before = graph.to_dict()
        group_id = create_group(graph, [first.id, second.id], "Calculation")
        ungroup(graph, group_id)
        self.assertEqual(graph.to_dict(), before)

    def test_group_bounds_includes_label_and_padding(self):
        graph, first, second = self.graph()
        group_id = create_group(graph, [first.id, second.id], "Calculation")
        self.assertEqual(group_bounds(graph, group_id), (80, 148, 790, 480))
        self.assertEqual(group_bounds(graph, group_id, height_fn=lambda node: 100), (80, 148, 790, 420))

    def test_group_clipboard_remaps_members_and_retains_collapsed_state(self):
        graph, first, second = self.graph()
        group_id = create_group(graph, [first.id, second.id], "Calculation")
        graph.groups[group_id]["collapsed"] = True
        fragment = copy_fragment(graph, graph.nodes)
        result, added = paste_fragment(graph, fragment, 0, 0)
        pasted = next(group for key, group in result.groups.items() if key != group_id)
        self.assertEqual(pasted, {"label": "Calculation", "nodes": added, "collapsed": True})
        self.assertNotIn("groups", copy_fragment(graph, [first.id]))


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.a = self.graph.add_node("Variable", 100, 100)
        self.b = self.graph.add_node("Math", 450, 300)
        self.c = self.graph.add_node("Print", 950, 600)

    def test_horizontal_alignment(self):
        for mode, expected in (("left", 100), ("right", 950), ("center_x", 525)):
            with self.subTest(mode=mode):
                positions = alignment_positions(self.graph, self.graph.nodes, mode)
                self.assertEqual([value[0] for value in positions.values()], [expected]*3)
                self.assertEqual([value[1] for value in positions.values()], [100, 300, 600])

    def test_vertical_alignment_respects_node_height(self):
        heights = {self.a.id: 100, self.b.id: 200, self.c.id: 300}
        fn = lambda node: heights[node.id]
        bottom = alignment_positions(self.graph, self.graph.nodes, "bottom", height_fn=fn)
        self.assertEqual([bottom[key][1]+heights[key] for key in bottom], [900]*3)
        centered = alignment_positions(self.graph, self.graph.nodes, "center_y", height_fn=fn)
        self.assertEqual([centered[key][1]+heights[key]/2 for key in centered], [500]*3)
        top = alignment_positions(self.graph, self.graph.nodes, "top")
        self.assertEqual([value[1] for value in top.values()], [100]*3)

    def test_distribute_preserves_extremes_and_equal_gaps(self):
        horizontal = alignment_positions(self.graph, self.graph.nodes, "distribute_x")
        self.assertEqual([value[0] for value in horizontal.values()], [100, 525, 950])
        heights = {self.a.id: 100, self.b.id: 200, self.c.id: 300}
        vertical = alignment_positions(self.graph, self.graph.nodes, "distribute_y", height_fn=lambda node: heights[node.id])
        self.assertEqual([value[1] for value in vertical.values()], [100, 300, 600])
        self.assertEqual(alignment_positions(self.graph, [self.a.id, self.b.id], "distribute_y"),
                         {self.a.id: (100, 100), self.b.id: (450, 300)})

    def test_alignment_does_not_change_manual_positions(self):
        before = self.graph.to_dict()
        alignment_positions(self.graph, self.graph.nodes, "left")
        alignment_positions(self.graph, self.graph.nodes, "distribute_x")
        self.assertEqual(self.graph.to_dict(), before)
        with self.assertRaisesRegex(GraphError, "Unknown alignment"):
            alignment_positions(self.graph, self.graph.nodes, "unknown")


if __name__ == "__main__":
    unittest.main()
