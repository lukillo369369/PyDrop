"""Geometry and ordering checks for explicitly requested automatic layout."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph_core import Edge, Graph, ports
from graph_layout import layout_positions


def height(node):
    inputs, outputs = ports(node)
    return 54 + max(len(inputs), len(outputs)) * 29 + 48


def crossing_count(graph, positions):
    """Crossings for a two-column fixture with no shared endpoints."""
    count = 0
    for index, edge in enumerate(graph.edges):
        for other in graph.edges[index + 1:]:
            if edge.source == other.source or edge.target == other.target:
                continue
            a = positions[edge.source][1] - positions[other.source][1]
            b = positions[edge.target][1] - positions[other.target][1]
            count += a * b < 0
    return count


class GraphLayoutTests(unittest.TestCase):
    def assert_no_overlap(self, graph, positions, width=270, measure=height):
        items = list(positions.items())
        for index, (node_id, (x, y)) in enumerate(items):
            for other_id, (other_x, other_y) in items[index + 1:]:
                overlap = (x < other_x + width and x + width > other_x
                           and y < other_y + measure(graph.nodes[other_id])
                           and y + measure(graph.nodes[node_id]) > other_y)
                self.assertFalse(overlap, f"{node_id} overlaps {other_id}")

    def test_execution_order_and_branch_lanes(self):
        graph = Graph()
        main = graph.add_node("Main", 900, 600)
        branch = graph.add_node("Branch", -200, -100)
        upper = graph.add_node("Print", 300, 600)
        lower = graph.add_node("Print", -100, -500)
        upper_next = graph.add_node("Print", 0, 0)
        lower_next = graph.add_node("Print", -400, 700)
        graph.connect(main.id, "next", branch.id, "run")
        graph.connect(branch.id, "true", upper.id, "run")
        graph.connect(branch.id, "false", lower.id, "run")
        graph.connect(upper.id, "next", upper_next.id, "run")
        graph.connect(lower.id, "next", lower_next.id, "run")

        positions = layout_positions(graph)

        for edge in graph.edges:
            self.assertGreater(positions[edge.target][0], positions[edge.source][0] + 270)
        self.assertLess(positions[upper.id][1], positions[lower.id][1])
        self.assertLess(positions[upper_next.id][1], positions[lower_next.id][1])
        self.assertEqual(positions[lower.id][1], positions[lower_next.id][1])
        self.assert_no_overlap(graph, positions)

    def test_barycentric_ordering_reduces_wire_crossings(self):
        graph = Graph()
        first = graph.add_node("Variable", 10, 10)
        second = graph.add_node("Variable", 10, 300)
        top = graph.add_node("Print", 600, 10)
        bottom = graph.add_node("Print", 600, 300)
        graph.connect(first.id, "value", bottom.id, "value")
        graph.connect(second.id, "value", top.id, "value")
        before = {node_id: (node.x, node.y) for node_id, node in graph.nodes.items()}

        positions = layout_positions(graph)

        self.assertEqual(crossing_count(graph, before), 1)
        self.assertEqual(crossing_count(graph, positions), 0)
        self.assert_no_overlap(graph, positions)

    def test_shared_data_predecessor_precedes_both_consumers(self):
        graph = Graph()
        value = graph.add_node("Variable", 400, 20)
        left = graph.add_node("Math", 30, 10)
        right = graph.add_node("Math", 40, 10)
        result = graph.add_node("Math", 0, 0)
        graph.connect(value.id, "value", left.id, "a")
        graph.connect(value.id, "value", right.id, "a")
        graph.connect(left.id, "result", result.id, "a")
        graph.connect(right.id, "result", result.id, "b")

        positions = layout_positions(graph)

        self.assertEqual(set(positions), set(graph.nodes))
        for edge in graph.edges:
            self.assertLess(positions[edge.source][0], positions[edge.target][0])
        self.assert_no_overlap(graph, positions)

    def test_variable_default_heights_do_not_overlap(self):
        graph = Graph()
        source = graph.add_node("Function Inputs", 15, 25)
        source.props["names"] = [f"argument_{i}" for i in range(12)]
        other = graph.add_node("Variable", 15, 25)
        target = graph.add_node("Math", 15, 25)
        graph.connect(source.id, "argument_0", target.id, "a")
        graph.connect(other.id, "value", target.id, "b")

        positions = layout_positions(graph)

        self.assertGreater(height(source), 400)
        self.assert_no_overlap(graph, positions)
        self.assertEqual(min(x for x, _ in positions.values()), 15)
        self.assertEqual(min(y for _, y in positions.values()), 25)

    def test_custom_width_and_height_are_used(self):
        graph = Graph()
        first = graph.add_node("Variable", 0, 0)
        second = graph.add_node("Variable", 0, 0)
        target = graph.add_node("Math", 0, 0)
        graph.connect(first.id, "value", target.id, "a")
        graph.connect(second.id, "value", target.id, "b")
        measure = lambda node: 500 if node.id == first.id else 210

        positions = layout_positions(graph, node_width=400, height_fn=measure)

        self.assert_no_overlap(graph, positions, width=400, measure=measure)
        self.assertEqual(positions[target.id][0] - positions[first.id][0], 510)

    def test_disconnected_components_pack_without_overlap(self):
        graph = Graph()
        for index in range(4):
            source = graph.add_node("Variable", 0, 0)
            target = graph.add_node("Math", 100, 0)
            graph.connect(source.id, "value", target.id, "a")
        isolated = graph.add_node("Quantity", 4000, 3000)

        positions = layout_positions(graph)

        self.assert_no_overlap(graph, positions)
        self.assertEqual(positions[isolated.id], (4000, 3000))

    def test_layout_is_pure_and_deterministic_including_groups(self):
        graph = Graph()
        first = graph.add_node("Variable", -10, -20)
        second = graph.add_node("Math", 0, 0)
        graph.connect(first.id, "value", second.id, "a")
        graph.groups = {"g1": {"label": "Manual assembly", "nodes": [first.id, second.id], "collapsed": True}}
        before, groups = copy.deepcopy(graph.to_dict()), copy.deepcopy(graph.groups)
        original = {node_id: (node.x, node.y) for node_id, node in graph.nodes.items()}

        positions = layout_positions(graph)

        self.assertEqual(layout_positions(graph), positions)
        self.assertEqual(graph.to_dict(), before)
        self.assertEqual(graph.groups, groups)
        self.assertEqual({node_id: (node.x, node.y) for node_id, node in graph.nodes.items()}, original)
        self.assertNotEqual(positions, original)
        # The UI can apply the complete mapping in a single history snapshot.
        for node_id, (x, y) in positions.items():
            graph.nodes[node_id].x, graph.nodes[node_id].y = x, y
        self.assertEqual({node_id: (node.x, node.y) for node_id, node in graph.nodes.items()}, positions)

    def test_selection_limits_layout_to_the_induced_subgraph(self):
        graph = Graph()
        outside = graph.add_node("Variable", 123, 456)
        first = graph.add_node("Math", 800, 0)
        second = graph.add_node("Math", 0, 0)
        graph.connect(outside.id, "value", first.id, "a")
        graph.connect(first.id, "result", second.id, "a")
        before = copy.deepcopy(graph.to_dict())

        positions = layout_positions(graph, selected={first.id, second.id, "missing"})

        self.assertEqual(set(positions), {first.id, second.id})
        self.assertLess(positions[first.id][0], positions[second.id][0])
        self.assertEqual(graph.to_dict(), before)
        self.assertEqual(layout_positions(graph, selected=[]), {})

    def test_execution_takes_priority_over_conflicting_draft_data(self):
        graph = Graph()
        earlier = graph.add_node("Function", 900, 0)
        later = graph.add_node("Function", 0, 0)
        # The separate execution/data graphs are acyclic, but the draft has an
        # invalid use-before-run dependency. Layout must not reverse execution.
        graph.edges = [Edge(earlier.id, "next", later.id, "run"), Edge(later.id, "result", earlier.id, "a")]

        positions = layout_positions(graph)

        self.assertLess(positions[earlier.id][0], positions[later.id][0])
        self.assert_no_overlap(graph, positions)

    def test_empty_graph_and_invalid_geometry(self):
        self.assertEqual(layout_positions(Graph()), {})
        graph = Graph()
        graph.add_node("Variable", 0, 0)
        with self.assertRaises(ValueError):
            layout_positions(graph, node_width=0)
        with self.assertRaises(ValueError):
            layout_positions(graph, height_fn=lambda node: float("nan"))

    def test_selection_layout_avoids_fixed_unselected_cards(self):
        graph = Graph()
        first = graph.add_node("Math", 0, 0)
        second = graph.add_node("Math", 10, 300)
        obstacle = graph.add_node("Variable", 380, 0)
        graph.connect(first.id, "result", second.id, "a")
        before = copy.deepcopy(graph.to_dict())
        positions = layout_positions(graph, selected={first.id, second.id})
        self.assertEqual(graph.to_dict(), before)
        self.assertNotIn(obstacle.id, positions)
        rectangles = dict(positions)
        rectangles[obstacle.id] = obstacle.x, obstacle.y
        self.assert_no_overlap(graph, rectangles)


if __name__ == "__main__":
    unittest.main()
