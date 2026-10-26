"""Exercise data workflows through isolated, self-contained Python exports."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph


def execute(graph, trace=False, setup=""):
    """Run exports outside the checkout; collect views instead of opening Tk."""
    source = compile_graph(graph, trace=trace).code
    harness = (
        "import json\n"
        "namespace = {'__name__': 'pydrop_export_under_test'}\n"
        f"exec(compile({source!r}, '<export>', 'exec'), namespace)\n"
        "views = []\n"
        "namespace['_data_display'] = views.append\n"
        f"{setup}\n"
        "namespace['main']()\n"
        "print('DATA_VIEWS:' + json.dumps(views, ensure_ascii=False))\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "isolated_export.py"
        script.write_text(harness, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(script)], cwd=directory,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )


def views_from(result):
    line = next(line for line in result.stdout.splitlines() if line.startswith("DATA_VIEWS:"))
    collected = json.loads(line[len("DATA_VIEWS:"):])
    if collected:
        return collected
    return [json.loads(line[len("PYDROP_DATA:"):])[1]
            for line in result.stderr.splitlines() if line.startswith("PYDROP_DATA:")]


def input_graph(path, **properties):
    graph = Graph()
    main = graph.add_node("Main", 0, 0)
    source = graph.add_node("CSV Input", 250, 0)
    source.props.update(path=str(path), **properties)
    graph.connect(main.id, "next", source.id, "run")
    return graph, source


def print_column(graph, source, column="y"):
    output = graph.add_node("Print", 500, 0)
    output.props["label"] = ""
    graph.connect(source.id, "next", output.id, "run")
    graph.connect(source.id, column, output.id, "value")
    return output


def measurement_graph(source_path, destination_path):
    """Electrical measurements become power = voltage * current."""
    graph, source = input_graph(source_path, x_column="voltage_V", y_column="current_A")
    power = graph.add_node("Math", 250, 250)
    power.props.update(operator="*")
    table = graph.add_node("Data Table", 500, 0)
    table.props.update(column_name="power_W")
    line = graph.add_node("Line Plot", 750, 0)
    line.props.update(x_column="time_s", y_column="power_W", title="Measured power",
                      x_label="Time (s)", y_label="Power (W)")
    scatter = graph.add_node("Scatter Plot", 1000, 0)
    scatter.props.update(title="Power samples", x_label="Voltage (V)", y_label="Power (W)")
    destination = graph.add_node("CSV Output", 1250, 0)
    destination.props.update(path=str(destination_path))
    graph.connect(source.id, "x", power.id, "a")
    graph.connect(source.id, "y", power.id, "b")
    graph.connect(source.id, "data", table.id, "data")
    graph.connect(power.id, "result", table.id, "values")
    graph.connect(table.id, "data", line.id, "data")
    graph.connect(source.id, "x", scatter.id, "x")
    graph.connect(power.id, "result", scatter.id, "y")
    graph.connect(table.id, "data", destination.id, "data")
    for before, after in ((source, table), (table, line), (line, scatter), (scatter, destination)):
        graph.connect(before.id, "next", after.id, "run")
    return graph


class DataNodeExportTests(unittest.TestCase):
    def test_column_selection_quotes_commas_and_preserves_exact_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "quoted.csv"
            destination = Path(directory) / "selected.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([" Force, N ", "  spaced  ", "plain"])
                writer.writerow(["12", "sample", "3"])
            graph, incoming = input_graph(source)
            table = graph.add_node("Data Table", 500, 0)
            table.props["columns"] = 'plain, " Force, N ", "  spaced  "'
            output = graph.add_node("CSV Output", 750, 0)
            output.props.update(path=str(destination), columns='"  spaced  ", plain')
            graph.connect(incoming.id, "next", table.id, "run")
            graph.connect(incoming.id, "data", table.id, "data")
            graph.connect(table.id, "next", output.id, "run")
            graph.connect(table.id, "data", output.id, "data")
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(views_from(result)[0]["columns"], ["plain", " Force, N ", "  spaced  "])
            with destination.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.reader(handle)), [["  spaced  ", "plain"], ["sample", "3"]])
            for invalid in ('plain, plain', 'plain,," Force, N "', '"unterminated'):
                with self.subTest(columns=invalid):
                    table.props["columns"] = invalid
                    failed = execute(graph)
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn("Data Table", failed.stderr)
                    self.assertIn("column", failed.stderr.lower())

    def test_all_five_nodes_derive_inspect_plot_and_export_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "measurements.csv"
            destination = Path(directory) / "power.csv"
            source.write_text("time_s,voltage_V,current_A\n0,2,0.1\n1,2.5,0.12\n2,3,0.2\n", encoding="utf-8")
            graph = measurement_graph(source, destination)
            loaded = Graph.from_dict(graph.to_dict())
            self.assertEqual(loaded.to_dict(), graph.to_dict())
            result = execute(loaded)
            self.assertEqual(result.returncode, 0, result.stderr)
            table, line, scatter = views_from(result)
            self.assertEqual(table["kind"], "table")
            self.assertEqual(table["columns"], ["time_s", "voltage_V", "current_A", "power_W"])
            self.assertEqual(table["row_count"], 3)
            self.assertEqual(line["kind"], "line")
            self.assertEqual(line["x"], [0.0, 1.0, 2.0])
            self.assertEqual(line["title"], "Measured power")
            self.assertEqual((line["x_label"], line["y_label"]), ("Time (s)", "Power (W)"))
            self.assertEqual(scatter["kind"], "scatter")
            self.assertEqual(scatter["x"], [2.0, 2.5, 3.0])
            for actual, expected in zip(line["y"], [0.2, 0.3, 0.6]):
                self.assertAlmostEqual(actual, expected)
            self.assertEqual(scatter["y"], line["y"])
            with destination.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["voltage_V"] for row in rows], ["2", "2.5", "3"])
            for row, expected in zip(rows, [0.2, 0.3, 0.6]):
                self.assertAlmostEqual(float(row["power_W"]), expected)

    def test_quoted_unicode_multiline_fields_and_raw_numeric_text_survive_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "input.csv", Path(directory) / "output.csv"
            original = [["label", "reading", "note"], ["alpha \u03b1", "001.20", 'one, "two"'],
                        ["\u03a9", "3.400", "first\nsecond"]]
            with source.open("w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle, delimiter=";").writerows(original)
            graph, incoming = input_graph(source, delimiter=";")
            outgoing = graph.add_node("CSV Output", 500, 0)
            outgoing.props.update(path=str(destination), delimiter=";")
            graph.connect(incoming.id, "next", outgoing.id, "run")
            graph.connect(incoming.id, "data", outgoing.id, "data")
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            with destination.open(newline="", encoding="utf-8-sig") as handle:
                self.assertEqual(list(csv.reader(handle, delimiter=";")), original)

    def test_csv_only_export_resolves_relative_paths_beside_script_without_importing_tk(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as working:
            export_directory = Path(directory)
            source = export_directory / "input.csv"
            source.write_text("time,reading\n0,001.20\n1,3.400\n", encoding="utf-8")
            graph, incoming = input_graph("input.csv")
            outgoing = graph.add_node("CSV Output", 500, 0)
            outgoing.props["path"] = "output.csv"
            graph.connect(incoming.id, "next", outgoing.id, "run")
            graph.connect(incoming.id, "data", outgoing.id, "data")
            script = export_directory / "measurement_export.py"
            script.write_text(compile_graph(graph).code +
                              "\nimport sys\nprint('TK_IMPORTED:' + str('tkinter' in sys.modules))\n",
                              encoding="utf-8")
            result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script)],
                                    cwd=working, capture_output=True, text=True,
                                    encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "TK_IMPORTED:False")
            with (export_directory / "output.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.reader(handle)),
                                 [["time", "reading"], ["0", "001.20"], ["1", "3.400"]])
            self.assertFalse((Path(working) / "output.csv").exists())

    def test_vector_function_derives_calibrated_power_with_scalar_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "measurements.csv"
            source.write_text("voltage_V,current_A\n2,0.1\n2.5,0.12\n3,0.2\n", encoding="utf-8")
            graph, incoming = input_graph(source, x_column="voltage_V", y_column="current_A")
            calculate = graph.add_node("Function", 500, 0)
            calculate.props.update(expression="round(a * b + c, 2)", c="0.03")
            table = graph.add_node("Data Table", 750, 0)
            table.props["column_name"] = "calibrated_power_W"
            graph.connect(incoming.id, "next", calculate.id, "run")
            graph.connect(incoming.id, "x", calculate.id, "a")
            graph.connect(incoming.id, "y", calculate.id, "b")
            graph.connect(calculate.id, "next", table.id, "run")
            graph.connect(incoming.id, "data", table.id, "data")
            graph.connect(calculate.id, "result", table.id, "values")
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            view = views_from(result)[0]
            self.assertEqual(view["columns"][-1], "calibrated_power_W")
            self.assertEqual([row[-1] for row in view["rows"]], [0.23, 0.33, 0.63])

    def test_missing_input_file_reports_path_and_node(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, source = input_graph(Path(directory) / "missing-measurements.csv")
            result = execute(graph, trace=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing-measurements.csv", result.stderr)
            self.assertRegex(result.stderr.lower(), r"not found|does not exist|no such file")
            self.assertIn("PYDROP_ERROR:" + source.id, result.stderr)

    def test_numeric_selection_reports_bad_cell_with_row_and_column(self):
        for invalid in ("broken", "NaN", "inf", ""):
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.csv"
                path.write_text("time,reading\n0,1\n1," + invalid + "\n", encoding="utf-8")
                graph, source = input_graph(path, x_column="time", y_column="reading")
                print_column(graph, source)
                result = execute(graph, trace=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("reading", result.stderr)
                self.assertRegex(result.stderr.lower(), r"data row\s+2|csv (?:row|line)\s+3")
                self.assertRegex(result.stderr.lower(), r"numeric|number|finite")
                self.assertIn("PYDROP_ERROR:" + source.id, result.stderr)

    def test_unselected_text_column_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.csv"
            path.write_text("x,y,note\n0,1,not a number\n", encoding="utf-8")
            graph, source = input_graph(path, x_column="x", y_column="y")
            table = graph.add_node("Data Table", 500, 0)
            graph.connect(source.id, "next", table.id, "run")
            graph.connect(source.id, "data", table.id, "data")
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not a number", json.dumps(views_from(result)))

    def test_ragged_csv_rows_explain_expected_and_actual_lengths(self):
        for row in ("1", "1,2,3"):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ragged.csv"
                path.write_text("x,y\n" + row + "\n", encoding="utf-8")
                graph, _ = input_graph(path)
                result = execute(graph)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr.lower(), r"(?:row|line)\s+2")
                self.assertRegex(result.stderr.lower(), r"column|field|length")
                self.assertIn("2", result.stderr)

    def test_missing_selected_column_names_available_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("time,reading\n0,1\n", encoding="utf-8")
            graph, source = input_graph(path, x_column="absent")
            print_column(graph, source, "x")
            result = execute(graph)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absent", result.stderr)
            self.assertIn("time", result.stderr)
            self.assertIn("reading", result.stderr)

    def test_data_and_calculated_values_must_run_before_consumers_on_each_branch(self):
        graph = Graph()
        main = graph.add_node("Main", 0, 0)
        branch = graph.add_node("Branch", 250, 0)
        source = graph.add_node("CSV Input", 500, 0)
        source.props["path"] = "not-opened-at-compile-time.csv"
        table = graph.add_node("Data Table", 500, 250)
        graph.connect(main.id, "next", branch.id, "run")
        graph.connect(branch.id, "true", source.id, "run")
        graph.connect(branch.id, "false", table.id, "run")
        graph.connect(source.id, "data", table.id, "data")
        with self.assertRaisesRegex(GraphError, "must run before"):
            compile_graph(graph)

    def test_output_existing_file_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "input.csv", Path(directory) / "output.csv"
            source.write_text("x,y\n1,2\n", encoding="utf-8")
            destination.write_text("original\n", encoding="utf-8")
            graph, incoming = input_graph(source)
            outgoing = graph.add_node("CSV Output", 500, 0)
            outgoing.props.update(path=str(destination))
            graph.connect(incoming.id, "next", outgoing.id, "run")
            graph.connect(incoming.id, "data", outgoing.id, "data")
            result = execute(graph)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr.lower(), r"exists|overwrite")
            self.assertEqual(destination.read_text(encoding="utf-8"), "original\n")
            outgoing.props["mode"] = "Overwrite"
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            with destination.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.reader(handle)), [["x", "y"], ["1", "2"]])
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["input.csv", "output.csv"])

    def test_table_and_output_select_columns_in_user_order(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "input.csv", Path(directory) / "output.csv"
            source.write_text("time,reading,note\n0,1,first\n1,2,second\n", encoding="utf-8")
            graph, incoming = input_graph(source)
            table = graph.add_node("Data Table", 500, 0)
            table.props["columns"] = "reading,time"
            outgoing = graph.add_node("CSV Output", 750, 0)
            outgoing.props.update(path=str(destination), columns="time")
            graph.connect(incoming.id, "next", table.id, "run")
            graph.connect(incoming.id, "data", table.id, "data")
            graph.connect(table.id, "next", outgoing.id, "run")
            graph.connect(table.id, "data", outgoing.id, "data")
            result = execute(graph)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(views_from(result)[0]["columns"], ["reading", "time"])
            with destination.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.reader(handle)), [["time"], ["0"], ["1"]])

    def test_math_broadcasts_scalars_on_either_side_of_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("reading\n1\n2\n3\n", encoding="utf-8")
            for port in ("a", "b"):
                with self.subTest(vector_port=port):
                    graph, source = input_graph(path, x_column="reading")
                    calculate = graph.add_node("Math", 250, 250)
                    calculate.props.update(operator="*", a="2", b="2")
                    table = graph.add_node("Data Table", 500, 0)
                    table.props["column_name"] = "twice"
                    graph.connect(source.id, "x", calculate.id, port)
                    graph.connect(source.id, "data", table.id, "data")
                    graph.connect(calculate.id, "result", table.id, "values")
                    graph.connect(source.id, "next", table.id, "run")
                    result = execute(graph)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual([row[-1] for row in views_from(result)[0]["rows"]], [2, 4, 6])

    def test_mismatched_plot_or_derived_column_lengths_explain_counts(self):
        for consumer_kind in ("Line Plot", "Scatter Plot", "Data Table"):
            with self.subTest(consumer=consumer_kind), tempfile.TemporaryDirectory() as directory:
                first, second = Path(directory) / "first.csv", Path(directory) / "second.csv"
                first.write_text("reading\n1\n2\n", encoding="utf-8")
                second.write_text("reading\n3\n4\n5\n", encoding="utf-8")
                graph, one = input_graph(first, x_column="reading")
                two = graph.add_node("CSV Input", 500, 0)
                two.props.update(path=str(second), x_column="reading")
                consumer = graph.add_node(consumer_kind, 750, 0)
                graph.connect(one.id, "next", two.id, "run")
                graph.connect(two.id, "next", consumer.id, "run")
                if consumer_kind == "Data Table":
                    graph.connect(one.id, "data", consumer.id, "data")
                    graph.connect(two.id, "x", consumer.id, "values")
                else:
                    graph.connect(one.id, "x", consumer.id, "x")
                    graph.connect(two.id, "x", consumer.id, "y")
                result = execute(graph, trace=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr.lower(), r"unequal|length|rows")
                self.assertIn("2", result.stderr)
                self.assertIn("3", result.stderr)
                self.assertIn("PYDROP_ERROR:" + consumer.id, result.stderr)

    def test_failed_atomic_export_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "input.csv", Path(directory) / "output.csv"
            source.write_text("x,y\n1,2\n", encoding="utf-8")
            destination.write_text("original\n", encoding="utf-8")
            graph, incoming = input_graph(source)
            outgoing = graph.add_node("CSV Output", 500, 0)
            outgoing.props.update(path=str(destination), mode="Overwrite")
            graph.connect(incoming.id, "next", outgoing.id, "run")
            graph.connect(incoming.id, "data", outgoing.id, "data")
            setup = ("def publication_failure(*args):\n"
                     "    raise OSError('simulated publication failure')\n"
                     "namespace['_data_os'].replace = publication_failure")
            result = execute(graph, setup=setup)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("simulated publication failure", result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["input.csv", "output.csv"])

    def test_output_rejects_unequal_arbitrary_columns_without_truncating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "output.csv"
            destination.write_text("original\n", encoding="utf-8")
            graph, incoming = input_graph("stubbed.csv")
            outgoing = graph.add_node("CSV Output", 500, 0)
            outgoing.props.update(path=str(destination), mode="Overwrite")
            graph.connect(incoming.id, "next", outgoing.id, "run")
            graph.connect(incoming.id, "data", outgoing.id, "data")
            # A library/function may produce arbitrary data rather than CSV's
            # rectangular input. Inject that runtime value at the input boundary.
            setup = ("namespace['_data_read'] = lambda *args, **kwargs: "
                     "{'data': {'time': [0, 1], 'reading': [3]}}")
            result = execute(graph, setup=setup)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unequal column lengths", result.stderr)
            self.assertIn("time", result.stderr)
            self.assertIn("reading", result.stderr)
            self.assertIn("CSV Output", result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "original\n")

    def test_plot_validates_numeric_selected_raw_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("time,reading\n0,1\n1,NaN\n", encoding="utf-8")
            graph, incoming = input_graph(source)
            plot = graph.add_node("Line Plot", 500, 0)
            plot.props.update(x_column="time", y_column="reading")
            graph.connect(incoming.id, "next", plot.id, "run")
            graph.connect(incoming.id, "data", plot.id, "data")
            result = execute(graph, trace=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reading", result.stderr)
            self.assertIn("finite", result.stderr)
            self.assertRegex(result.stderr.lower(), r"data row\s+2")
            self.assertIn("PYDROP_ERROR:" + plot.id, result.stderr)

    def test_large_import_trace_and_table_preview_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "large.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["time", "reading"])
                writer.writerows((i, i * 2) for i in range(50000))
            graph, incoming = input_graph(source, x_column="time", y_column="reading")
            table = graph.add_node("Data Table", 500, 0)
            graph.connect(incoming.id, "next", table.id, "run")
            graph.connect(incoming.id, "data", table.id, "data")
            result = execute(graph, trace=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stderr), 20000)
            view = views_from(result)[-1]
            self.assertEqual(view["row_count"], 50000)
            self.assertLess(len(view["rows"]), 50000)
            self.assertLess(len(result.stdout), 250000)


if __name__ == "__main__":
    unittest.main()
