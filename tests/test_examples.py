"""Verify each menu lesson survives saving and runs as standalone Python."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from example_graphs import EXAMPLES, pass_demo, sensor_dashboard_demo
from graph_core import Graph, compile_graph


class ExampleTests(unittest.TestCase):
    def test_all_lessons_round_trip_and_run_export(self):
        expected = {
            "sensor_dashboard": "Connection: Connected to simulated Arduino (9600 baud).\nTemperature (C): 23.5\nLight (ADC): 512.0\nConnection: Disconnected.",
            "engineering_force": "Force: 24.525 N",
            "torque": "Torque: 25 N*m",
            "ohms_law": "Voltage: 500 V",
            "electrical_power": "Power: 6 W",
            "reusable_rectangle": "First area: 6.0\nFirst perimeter: 10.0\nSecond area: 20.0\nSecond perimeter: 18.0",
            "drop_shopping": "Your name? Hello Ada\n37.5",
            "drop_logic": "True\nFalse",
            "controlled_temperature": "Fan ON (hot and enabled)",
            "clamp_power": "Clamped power: 100.0",
            "dice": "Dice roll: 6",
            "interactive_calculator": "First number: Second number: Sum: 5.0",
            "greeting": "Hello from pydrop!",
            "rectangle_area": "Rectangle area: 40.0",
            "temperature_conversion": "Fahrenheit: 77.0",
            "triangle": "Hypotenuse: 5.0",
            "split_bill": "Each person pays: $24.0",
            "pass_or_fail": "Passed! Score is at least 60.",
            "force": "Force (N): 24.525",
            "temperature": "Fan ON",
        }
        with tempfile.TemporaryDirectory() as directory:
            for slug, title, factory in EXAMPLES:
                with self.subTest(example=slug):
                    graph = factory()
                    path = Path(directory) / (slug + ".nodeforge.json")
                    graph.save(path)
                    loaded = Graph.load(path)
                    self.assertEqual(graph.to_dict(), loaded.to_dict())
                    compiled = compile_graph(loaded)
                    self.assertEqual(compiled.warnings, [])
                    if slug == "experimental_measurements":
                        # Trace mode sends bounded table/plot payloads instead of
                        # opening standalone Tk windows during this subprocess test.
                        compiled = compile_graph(loaded, trace=True, data_base=directory)
                    exported = Path(directory) / (slug + ".py")
                    exported.write_text(compiled.code, encoding="utf-8")
                    result = subprocess.run([sys.executable, str(exported)], cwd=directory,
                                            input={"interactive_calculator":"2\n3\n", "drop_shopping":"Ada\n"}.get(slug),
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if slug == "experimental_measurements":
                        self.assertIn("Calculated power_W", result.stdout)
                        self.assertIn("PYDROP_DATA:", result.stderr)
                        self.assertTrue((Path(directory) / "experimental_results.csv").exists())
                    else:
                        self.assertEqual(result.stdout.strip(), expected[slug])

    def test_distributed_sensor_dashboard_matches_menu(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "sensor_dashboard.nodeforge.json"
        self.assertEqual(Graph.load(path).to_dict(), sensor_dashboard_demo().to_dict())

    def test_sensor_dashboard_timeout_reports_status_and_disconnects(self):
        graph = sensor_dashboard_demo()
        for node in graph.nodes.values():
            if node.kind == "Serial Send":
                node.props["newline"] = "None"  # A partial command has no response.
        result = subprocess.run([sys.executable, "-c", compile_graph(graph).code],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sensor read: ", result.stdout)
        self.assertIn("timed out", result.stdout.lower())
        self.assertNotIn("Temperature (C):", result.stdout)
        self.assertTrue(result.stdout.strip().endswith("Connection: Disconnected."))

    def test_pass_fail_boundary(self):
        for score, message in (("59", "Keep practicing"), ("60", "Passed!")):
            with self.subTest(score=score):
                graph = pass_demo()
                graph.nodes["n2"].props["value"] = score
                result = subprocess.run([sys.executable, "-c", compile_graph(graph).code],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stdout)
