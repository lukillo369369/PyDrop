"""Physical behavior, wiring validation, persistence, and isolated exports."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engineering_units import (UnitError, _Quantity, _convert_unit, _engineering,
                               _unit_sqrt, unit_spec)
from graph_core import Graph, GraphError, compile_graph, validate_props
from example_graphs import engineering_force_demo


def quantity(graph, value, unit):
    node = graph.add_node("Quantity", 0, 0)
    node.props.update(value=str(value), unit=unit)
    return node


def output_graph():
    graph = Graph()
    main = graph.add_node("Main", 0, 0)
    output = graph.add_node("Print", 600, 0)
    output.props["label"] = ""
    graph.connect(main.id, "next", output.id, "run")
    return graph, output


def execute(graph, trace=False):
    # -I and an unrelated working directory prove exports need no project module.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "exported.py"
        path.write_text(compile_graph(graph, trace=trace).code, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-X", "utf8", str(path)], cwd=directory,
                              capture_output=True, text=True, encoding="utf-8", timeout=5)


class QuantityTests(unittest.TestCase):
    def test_required_dimensions_and_conversions(self):
        for value, source, target, expected in (
            (1, "m", "cm", 100), (12, "in", "ft", 1), (1, "lb", "kg", .45359237),
            (2, "min", "s", 120), (1, "kN", "N", 1000),
            (12, "V", "mV", 12000), (500, "mA", "A", .5),
            (2, "kohm", "ohm", 2000), (1, "kW", "W", 1000),
            (1, "lbf*ft", "N*m", 1.3558179483314004),
        ):
            with self.subTest(source=source):
                result = _Quantity(value, source).to(target)
                self.assertAlmostEqual(result.si / unit_spec(target)[0], expected)

    def test_arithmetic_preserves_and_combines_dimensions(self):
        self.assertEqual(str(_Quantity(1, "m") + _Quantity(25, "cm")), "1.25 m")
        self.assertEqual(str(2 * _Quantity(3, "m")), "6 m")
        self.assertEqual(str(_Quantity(6, "m") / _Quantity(2, "s")), "3 m/s")
        self.assertEqual(float(_Quantity(1, "m") / _Quantity(50, "cm")), 2)
        self.assertEqual(str(_unit_sqrt(_Quantity(9, "m^2"))), "3 m")
        self.assertEqual(str(round(_Quantity(123.456, "cm"), 1)), "123.5 cm")
        self.assertEqual(str(_Quantity(5, "m") % _Quantity(200, "cm")), "1 m")
        self.assertEqual(float(_Quantity(5, "m") // _Quantity(200, "cm")), 2)

    def test_mismatches_cannot_be_converted_or_silently_stripped(self):
        operations = [lambda: _Quantity(1, "m") + _Quantity(1, "s"),
                      lambda: _Quantity(1, "m") + 0,
                      lambda: _Quantity(1, "m") == _Quantity(1, "s"),
                      lambda: _Quantity(1, "m").to("kg"),
                      lambda: float(_Quantity(1, "V")),
                      lambda: _Quantity(2, "m") ** _Quantity(2, "s"),
                      lambda: _convert_unit(2, "m")]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(UnitError):
                operation()

    def test_unit_parser_is_bounded_and_does_not_execute_code(self):
        self.assertEqual(unit_spec("kg*m/s^2"), unit_spec("N"))
        for bad in ("unknown", "", None, "m^999", "m^0.5", "__import__('os')", "m.__class__", "m+kg", "m" * 100):
            with self.subTest(unit=bad), self.assertRaises(UnitError):
                unit_spec(bad)

    def test_finite_magnitudes(self):
        for magnitude in (float("inf"), float("nan"), 1e101, "3", True):
            with self.subTest(value=magnitude), self.assertRaises(UnitError):
                _Quantity(magnitude, "m")

    def test_engineering_results_and_typed_runtime_errors(self):
        self.assertEqual(str(_engineering("Force", _Quantity(2, "kg"), _Quantity(3, "m/s^2"))), "6 N")
        with self.assertRaisesRegex(UnitError, r"Force.mass needs mass.*received time"):
            _engineering("Force", _Quantity(1, "s"), _Quantity(1, "m/s^2"))


class EngineeringGraphTests(unittest.TestCase):
    def test_rejected_wire_preserves_previous_connection(self):
        graph = Graph()
        force = graph.add_node("Force", 0, 0)
        mass = quantity(graph, 2, "kg")
        time = quantity(graph, 3, "s")
        graph.connect(mass.id, "result", force.id, "mass")
        before = copy.deepcopy(graph.edges)
        with self.assertRaisesRegex(GraphError, r"Force.mass.*expected mass, received time") as caught:
            graph.connect(time.id, "result", force.id, "mass")
        self.assertEqual(caught.exception.node_id, force.id)
        self.assertEqual(graph.edges, before)

    def test_partial_addition_and_downstream_checks(self):
        graph, out = output_graph()
        a, b, time = quantity(graph, 1, "m"), quantity(graph, 25, "cm"), quantity(graph, 1, "s")
        add = graph.add_node("Math", 0, 0)
        graph.connect(a.id, "result", add.id, "a")
        with self.assertRaisesRegex(GraphError, "unitless"):
            compile_graph(graph)  # Missing b is a unitless default at compile time.
        graph.connect(b.id, "result", add.id, "b")
        graph.connect(add.id, "result", out.id, "value")
        self.assertEqual(execute(graph).stdout, "1.25 m\n")
        with self.assertRaisesRegex(GraphError, "length.*time"):
            graph.connect(time.id, "result", add.id, "b")

    def test_quantity_magnitude_requires_unitless_input(self):
        graph = Graph()
        a, b = quantity(graph, 1, "m"), quantity(graph, 2, "kg")
        with self.assertRaisesRegex(GraphError, "Quantity magnitude"):
            graph.connect(a.id, "result", b.id, "value")

    def test_conversion_round_trip_and_isolated_export(self):
        graph, out = output_graph()
        length = quantity(graph, 2, "m")
        conversion = graph.add_node("Unit Convert", 0, 0)
        conversion.props.update(unit="ft")
        graph.connect(length.id, "result", conversion.id, "value")
        graph.connect(conversion.id, "result", out.id, "value")
        loaded = Graph.from_dict(graph.to_dict())
        self.assertEqual(loaded.to_dict(), graph.to_dict())
        result = execute(loaded, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("6.56167979003 ft", result.stdout)
        self.assertIn("PYDROP_VALUE:", result.stderr)
        self.assertIn('"Quantity"', result.stderr)
        time = quantity(graph, 1, "s")
        with self.assertRaisesRegex(GraphError, "Unit Convert.*length.*time"):
            graph.connect(time.id, "result", conversion.id, "value")

    def test_saved_mismatch_and_property_edits_are_checked(self):
        graph = engineering_force_demo()
        mass = next(node for node in graph.nodes.values() if node.kind == "Quantity" and node.props["unit"] == "g")
        mass.props["unit"] = "s"
        with self.assertRaisesRegex(GraphError, "Force.mass"):
            Graph.from_dict(graph.to_dict())
        with self.assertRaisesRegex(GraphError, "Force.mass"):
            compile_graph(graph)

    def test_invalid_properties_report_graph_errors(self):
        graph = Graph()
        node = quantity(graph, "nan", "m")
        with self.assertRaises(GraphError):
            validate_props(node)
        node.props.update(value="2", unit="unknown")
        with self.assertRaises(GraphError):
            validate_props(node)
        convert = graph.add_node("Unit Convert", 0, 0)
        convert.props["unit"] = "kg"
        with self.assertRaisesRegex(GraphError, "compatible"):
            validate_props(convert)

    def test_unitless_math_remains_available(self):
        graph, out = output_graph()
        math = graph.add_node("Math", 0, 0)
        math.props.update(operator="*", a="6", b="7")
        graph.connect(math.id, "result", out.id, "value")
        self.assertEqual(execute(graph).stdout, "42.0\n")

    def test_typed_defaults_run_without_quantity_wires(self):
        graph, out = output_graph()
        force = graph.add_node("Force", 0, 0)
        force.props.update(mass="2", acceleration="3")
        graph.connect(force.id, "result", out.id, "value")
        self.assertEqual(execute(graph).stdout, "6 N\n")

    def test_formula_math_helpers_preserve_dimensions(self):
        graph, out = output_graph()
        length = quantity(graph, 3, "m")
        fn = graph.add_node("Function", 0, 0)
        fn.props.update(expression="round(sqrt(a * a), 2)")
        graph.connect(length.id, "result", fn.id, "a")
        graph.connect("n1", "next", fn.id, "run")
        graph.connect(fn.id, "next", out.id, "run")
        graph.connect(fn.id, "result", out.id, "value")
        self.assertEqual(execute(graph).stdout, "3 m\n")
        fn.props["expression"] = "sin(a)"
        with self.assertRaisesRegex(GraphError, "sin.*unitless.*length"):
            compile_graph(graph)

    def test_dynamic_function_values_keep_runtime_unit_checks_in_export(self):
        graph, out = output_graph()
        definition = graph.add_function("load_force", ["load"], ["force"])
        body = Graph.from_dict(definition["graph"])
        force = body.add_node("Force", 0, 0)
        body.connect("n2", "load", force.id, "mass")
        body.connect(force.id, "result", "n3", "force")
        definition["graph"] = body.to_dict()
        call = graph.add_call("load_force", 0, 0)
        mass = quantity(graph, 2, "kg")
        graph.connect(mass.id, "result", call.id, "load")
        graph.connect("n1", "next", call.id, "run")
        graph.connect(call.id, "next", out.id, "run")
        graph.connect(call.id, "force", out.id, "value")
        self.assertEqual(execute(graph).stdout, "2 N\n")
        mass.props["unit"] = "s"  # Signature dimensions are unknown until called.
        for trace in (False, True):
            result = execute(graph, trace=trace)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Force.mass needs mass", result.stderr)
            self.assertIn("received time", result.stderr)
            if trace:
                self.assertIn("PYDROP_ERROR:load_force/", result.stderr)

    def test_comparison_clamp_and_unitless_conversion_constraints(self):
        graph = Graph()
        length, time = quantity(graph, 1, "m"), quantity(graph, 1, "s")
        compare = graph.add_node("Compare", 0, 0)
        graph.connect(length.id, "result", compare.id, "a")
        with self.assertRaises(GraphError):
            graph.connect(time.id, "result", compare.id, "b")
        conversion = graph.add_node("Convert", 0, 0)
        with self.assertRaisesRegex(GraphError, "cannot discard units"):
            graph.connect(length.id, "result", conversion.id, "value")
        conversion.props["type"] = "Text"
        graph.connect(length.id, "result", conversion.id, "value")
        clamp = graph.add_node("Clamp", 0, 0)
        graph.connect(length.id, "result", clamp.id, "value")
        with self.assertRaisesRegex(GraphError, "Clamp bounds"):
            graph.connect(time.id, "result", clamp.id, "min")

    def test_dynamic_numeric_conversion_explains_units_in_export(self):
        graph, out = output_graph()
        definition = graph.add_function("pass_quantity", ["item"], ["item"])
        body = Graph.from_dict(definition["graph"])
        body.connect("n2", "item", "n3", "item")
        definition["graph"] = body.to_dict()
        call = graph.add_call("pass_quantity", 0, 0)
        length = quantity(graph, 2, "m")
        convert = graph.add_node("Convert", 0, 0)
        graph.connect(length.id, "result", call.id, "item")
        graph.connect("n1", "next", call.id, "run")
        graph.connect(call.id, "next", out.id, "run")
        graph.connect(call.id, "item", convert.id, "value")
        graph.connect(convert.id, "result", out.id, "value")
        result = execute(graph, trace=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UnitError: Cannot discard length units", result.stderr)


if __name__ == "__main__":
    unittest.main()
