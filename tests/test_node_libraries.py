"""Installation boundaries, versioning, typed ports, dependencies, and exports."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, ports
from node_libraries import (LibraryCatalog, LibraryError, content_digest, dependency_issues,
                            install_library, load_library, make_reference, save_library, validate_library)


def sample_library():
    return {"format":"pydrop-node-library", "schema_version":1, "id":"test-tools", "name":"Test tools", "version":"1.0.0",
            "requirements":[], "nodes":[{"id":"scale", "version":"1.0.0", "name":"Scale and describe", "category":"Calculations",
            "description":"Scale a numeric value and provide a message.",
            "inputs":[{"name":"value", "type":"Number", "default":"3"}, {"name":"factor", "type":"Number", "default":"2"}],
            "outputs":[{"name":"scaled", "type":"Number"}, {"name":"message", "type":"Text"}],
            "implementation":'scaled = value * factor\nreturn {"scaled": scaled, "message": f"Scaled: {scaled:g}"}'}]}


def make_graph(reference, output="message"):
    graph = Graph()
    main = graph.add_node("Main", 0, 0)
    node = graph.add_library_node(reference, 300, 0)
    out = graph.add_node("Print", 600, 0)
    out.props["label"] = ""
    graph.connect(main.id, "next", node.id, "run")
    graph.connect(node.id, "next", out.id, "run")
    graph.connect(node.id, output, out.id, "value")
    return graph, node, out


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "installed"
        self.path = self.directory / "tools.pydrop-lib.json"

    def install(self, data=None):
        library = save_library(self.path, data or sample_library())
        catalog = install_library(self.path, self.store, allow_executable=True, expected_digest=content_digest(library))
        return library, catalog, make_reference(library, library["nodes"][0])

    def run_export(self, graph, catalog, trace=False):
        source = compile_graph(graph, trace=trace, library_catalog=catalog).code
        destination = self.directory / "export.py"
        destination.write_text(source, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-X", "utf8", str(destination)], cwd=self.directory,
                              capture_output=True, text=True, encoding="utf-8", timeout=5)

    def test_browse_save_install_compile_and_load_do_not_execute_implementation(self):
        marker = self.directory / "executed.txt"
        data = sample_library()
        data["nodes"][0]["implementation"] = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n" + data["nodes"][0]["implementation"]
        library = save_library(self.path, data)
        load_library(self.path)
        dependency_issues(library)
        self.assertFalse(marker.exists())
        self.assertFalse(self.store.exists())
        catalog = install_library(self.path, self.store, allow_executable=True)
        graph, _, _ = make_graph(make_reference(library, library["nodes"][0]))
        loaded = Graph.from_dict(graph.to_dict())
        self.assertEqual(loaded.dependency_issues(catalog), [])
        compile_graph(loaded, library_catalog=catalog)
        self.assertFalse(marker.exists())
        result = self.run_export(loaded, catalog)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "ran")

    def test_installation_requires_explicit_consent(self):
        save_library(self.path, sample_library())
        with self.assertRaisesRegex(LibraryError, "explicit installation consent"):
            install_library(self.path, self.store)
        self.assertFalse(self.store.exists())

    def test_file_changed_after_review_requires_new_review(self):
        library = save_library(self.path, sample_library())
        reviewed = content_digest(library)
        library["nodes"][0]["implementation"] += "\n# changed"
        save_library(self.path, library)
        with self.assertRaisesRegex(LibraryError, "changed after preview"):
            install_library(self.path, self.store, allow_executable=True, expected_digest=reviewed)
        self.assertFalse(self.store.exists())

    def test_loose_files_are_not_implicitly_installed(self):
        self.store.mkdir()
        library = save_library(self.store / "loose.pydrop-lib.json", sample_library())
        self.assertEqual(LibraryCatalog(self.store).palette(), {})
        graph, _, _ = make_graph(make_reference(library, library["nodes"][0]))
        with self.assertRaisesRegex(GraphError, "Missing library test-tools@1.0.0"):
            compile_graph(graph, library_catalog=LibraryCatalog(self.store))

    def test_missing_library_project_opens_with_ports_and_reports_versions(self):
        library = validate_library(sample_library())
        graph, node, _ = make_graph(make_reference(library, library["nodes"][0]))
        path = self.directory / "project.drop.json"
        graph.save(path)
        loaded = Graph.load(path)
        self.assertEqual(ports(loaded.nodes[node.id]), ports(node))
        self.assertEqual(loaded.to_dict(), graph.to_dict())
        issues = loaded.dependency_issues(LibraryCatalog(self.store))
        self.assertIn("test-tools@1.0.0 / scale@1.0.0", issues[0])
        self.assertIn("explicitly install", issues[0])

    def test_multiple_typed_outputs_and_portable_export(self):
        library, catalog, reference = self.install()
        graph, node, out = make_graph(reference)
        self.assertEqual(ports(node)[1], {"next":"exec", "scaled":"data", "message":"data"})
        result = self.run_export(graph, catalog, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Scaled: 6\n")
        self.assertIn("PYDROP_VALUE:", result.stderr)
        graph.connect(node.id, "scaled", out.id, "value")
        self.assertEqual(self.run_export(graph, catalog).stdout, "6.0\n")

    def test_known_type_mismatch_rejects_wire_and_preserves_old_connection(self):
        _, _, reference = self.install()
        graph, node, _ = make_graph(reference)
        number = graph.add_node("Variable", 0, 200)
        text = graph.add_node("Variable", 0, 400)
        text.props.update(type="Text", value="wrong")
        graph.connect(number.id, "value", node.id, "value")
        before = graph.edges[:]
        with self.assertRaisesRegex(GraphError, "expects Number; received Text"):
            graph.connect(text.id, "value", node.id, "value")
        self.assertEqual(graph.edges, before)

    def test_library_output_types_check_builtin_consumers(self):
        _, _, reference = self.install()
        graph, node, _ = make_graph(reference)
        math = graph.add_node("Math", 0, 0)
        with self.assertRaisesRegex(GraphError, "received Text"):
            graph.connect(node.id, "message", math.id, "a")

    def test_runtime_output_type_and_shape_errors_identify_node(self):
        for source, message in [('return {"scaled": "wrong", "message":"ok"}', "expects Number"),
                                ('return {"scaled": 1}', "exactly these outputs"),
                                ('return {"scaled": True, "message":"ok"}', "expects Number"),
                                ('return {"scaled": float("nan"), "message":"ok"}', "finite Number")]:
            with self.subTest(source=source):
                # Separate installations avoid mutating an already-approved version.
                self.store = self.directory / content_digest(source)
                data = sample_library()
                data["nodes"][0]["implementation"] = source
                _, catalog, reference = self.install(data)
                graph, node, _ = make_graph(reference)
                result = self.run_export(graph, catalog, trace=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("LibraryNodeError:", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertIn("PYDROP_ERROR:" + node.id, result.stderr)

    def test_any_input_defers_to_runtime_checks(self):
        data = sample_library()
        source = copy.deepcopy(data["nodes"][0])
        source.update(id="dynamic", name="Dynamic", inputs=[], outputs=[{"name":"result", "type":"Any"}], implementation='return {"result":"not a number"}')
        data["nodes"].append(source)
        library, catalog, reference = self.install(data)
        graph, node, _ = make_graph(reference)
        dynamic = graph.add_library_node(make_reference(library, library["nodes"][1]), 0, 0)
        graph.connect("n1", "next", dynamic.id, "run")
        graph.connect(dynamic.id, "next", node.id, "run")
        graph.connect(dynamic.id, "result", node.id, "value")
        result = self.run_export(graph, catalog)
        self.assertIn("expects Number; received str", result.stderr)

    def test_boolean_text_defaults_and_quantity_dimensions(self):
        data = sample_library()
        node = data["nodes"][0]
        node.update(inputs=[{"name":"length", "type":"Quantity", "unit":"cm", "default":"100"},
                            {"name":"enabled", "type":"Boolean", "default":"True"},
                            {"name":"label", "type":"Text", "default":"Area"}],
                    outputs=[{"name":"area", "type":"Quantity", "unit":"m^2"}, {"name":"message", "type":"Text"}],
                    implementation='return {"area": length * length, "message": label if enabled else "off"}')
        _, catalog, reference = self.install(data)
        graph, placed, out = make_graph(reference, "area")
        self.assertEqual(self.run_export(graph, catalog).stdout, "1 m^2\n")
        wrong = graph.add_node("Quantity", 0, 0)
        wrong.props["unit"] = "s"
        with self.assertRaisesRegex(GraphError, "expected length, received time"):
            graph.connect(wrong.id, "result", placed.id, "length")
        graph.connect(placed.id, "message", out.id, "value")
        self.assertEqual(self.run_export(graph, catalog).stdout, "Area\n")

    def test_versions_coexist_and_changed_definition_requires_bump(self):
        first, _, first_ref = self.install()
        second = copy.deepcopy(first)
        second["version"] = "2.0.0"
        second["nodes"][0]["implementation"] = 'return {"scaled": 99, "message":"new"}'
        save_library(self.path, second)
        with self.assertRaisesRegex(LibraryError, "new definition version"):
            install_library(self.path, self.store, allow_executable=True)
        second["nodes"][0]["version"] = "2.0.0"
        second, catalog, second_ref = self.install(second)
        self.assertEqual(len(catalog.palette()), 2)
        self.assertEqual(self.run_export(make_graph(first_ref)[0], catalog).stdout, "Scaled: 6\n")
        self.assertEqual(self.run_export(make_graph(second_ref)[0], catalog).stdout, "new\n")
        changed = copy.deepcopy(first)
        changed["name"] = "Changed same version"
        save_library(self.path, changed)
        with self.assertRaisesRegex(LibraryError, "different contents"):
            install_library(self.path, self.store, allow_executable=True)

    def test_fingerprint_schema_and_installed_file_tampering_block_execution(self):
        library, catalog, reference = self.install()
        graph, node, _ = make_graph(reference)
        node.props["name"] = "Faked name"
        with self.assertRaisesRegex(GraphError, "metadata mismatch"):
            compile_graph(graph, library_catalog=catalog)
        node.props = copy.deepcopy(reference)
        installed_file = self.store / (content_digest(library) + ".pydrop-lib.json")
        changed = copy.deepcopy(library)
        changed["nodes"][0]["implementation"] = 'raise RuntimeError("tampered")'
        save_library(installed_file, changed)
        self.assertTrue(LibraryCatalog(self.store).issues)
        with self.assertRaisesRegex(GraphError, "Missing library"):
            compile_graph(graph, library_catalog=catalog)  # Old in-memory catalog grants no authority.

    def test_project_cannot_embed_or_install_code(self):
        library = validate_library(sample_library())
        graph, node, _ = make_graph(make_reference(library, library["nodes"][0]))
        node.props["implementation"] = 'raise RuntimeError("project code")'
        with self.assertRaisesRegex(GraphError, "cannot install or embed"):
            Graph.from_dict(graph.to_dict())
        self.assertFalse(self.store.exists())

    def test_dependency_scan_reports_imports_and_declared_packages(self):
        data = sample_library()
        data["nodes"][0]["implementation"] = 'import pydrop_missing_test_package\nreturn {"scaled": 1, "message":"ok"}'
        library, catalog, reference = self.install(data)
        issues = make_graph(reference)[0].dependency_issues(catalog)
        self.assertIn("pydrop_missing_test_package", issues[0])
        with self.assertRaisesRegex(GraphError, "Missing Python module"):
            compile_graph(make_graph(reference)[0], library_catalog=catalog)
        library["requirements"] = [{"module":"math", "package":"some-dist", "version":"2.0"}]
        with patch("node_libraries.importlib.metadata.version", return_value="1.0"):
            self.assertTrue(any("requires version 2.0" in issue for issue in dependency_issues(library)))

    def test_dependency_scan_does_not_import_parent_packages(self):
        marker = self.directory / "imported.txt"
        package = self.directory / "test_side_effect_package"
        package.mkdir()
        (package / "__init__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')", encoding="utf-8")
        (package / "child.py").write_text("value = 1", encoding="utf-8")
        library = validate_library(sample_library())
        library["requirements"] = [{"module":"test_side_effect_package.child"}]
        with patch.object(sys, "path", [str(self.directory), *sys.path]):
            self.assertEqual(dependency_issues(library), [])
        self.assertFalse(marker.exists())
        self.assertNotIn("test_side_effect_package", sys.modules)

    def test_library_inside_reusable_function_uses_installed_code(self):
        _, catalog, reference = self.install()
        graph = Graph()
        definition = graph.add_function("describe_value", ["item"], ["message"])
        body = Graph.from_dict(definition["graph"])
        lib = body.add_library_node(reference, 0, 0)
        body.connect("n1", "next", lib.id, "run")
        body.connect(lib.id, "next", "n3", "run")
        body.connect("n2", "item", lib.id, "value")
        body.connect(lib.id, "message", "n3", "message")
        definition["graph"] = body.to_dict()
        main = graph.add_node("Main", 0, 0)
        call = graph.add_call("describe_value", 300, 0)
        call.props["inputs"][0]["default"] = "5"
        out = graph.add_node("Print", 600, 0)
        out.props["label"] = ""
        graph.connect(main.id, "next", call.id, "run")
        graph.connect(call.id, "next", out.id, "run")
        graph.connect(call.id, "message", out.id, "value")
        self.assertEqual(self.run_export(graph, catalog).stdout, "Scaled: 10\n")
        self.assertIn("Function describe_value", graph.dependency_issues(LibraryCatalog(self.directory / "empty"))[0])

    def test_invalid_schema_versions_ports_and_python_are_rejected(self):
        mutations = [lambda d: d.update(version="latest"), lambda d: d["nodes"][0].update(version="1"),
                     lambda d: d["nodes"][0].update(implementation="import os\nreturn {"),
                     lambda d: d["nodes"][0]["inputs"][0].update(name="run"),
                     lambda d: d["nodes"][0]["inputs"][0].update(type="Unknown"),
                     lambda d: d["nodes"][0]["inputs"].append(copy.deepcopy(d["nodes"][0]["inputs"][0])),
                     lambda d: d["nodes"][0].update(implementation="from .helper import calculate\nreturn {}"),
                     lambda d: d["nodes"][0].update(implementation="pass\n\fprint('escape function body')")]
        for mutate in mutations:
            data = sample_library()
            mutate(data)
            with self.subTest(data=data), self.assertRaises(LibraryError):
                validate_library(data)

    def test_cli_inspect_install_export_and_run_obey_execution_boundary(self):
        marker = self.directory / "cli-ran.txt"
        data = sample_library()
        data["nodes"][0]["implementation"] = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n" + data["nodes"][0]["implementation"]
        library = save_library(self.path, data)
        script = Path(__file__).resolve().parents[1] / "nodeforge.py"

        def cli(*arguments):
            return subprocess.run([sys.executable, str(script), "--library-store", str(self.store), *map(str, arguments)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=10)

        result = cli("--inspect-library", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EXECUTABLE PYTHON", result.stdout)
        self.assertFalse(self.store.exists())
        self.assertFalse(marker.exists())
        result = cli("--install-library", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        graph, _, _ = make_graph(make_reference(library, library["nodes"][0]))
        path = self.directory / "project.drop.json"
        graph.save(path)
        result = cli(path, "--export", self.directory / "cli-export.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        result = cli(path, "--run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Scaled: 6\n")
        self.assertEqual(marker.read_text(), "ran")


if __name__ == "__main__":
    unittest.main()
