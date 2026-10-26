"""User-defined named inputs, portable definitions, and reusable template storage."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from graph_core import Graph,GraphError,compile_graph,ports,validate_props
from custom_nodes import load_templates,save_template,validate_template


def tax_definition():
    return dict(template_name="Add tax",description="Add a percentage to a price.",
                inputs=[dict(name="price",default="100"),dict(name="rate",default="10")],
                expression="round(price * (1 + rate / 100), 2)")


class CustomNodeTests(unittest.TestCase):
    def graph(self,props=None):
        graph = Graph()
        main = graph.add_node("Main",0,0)
        node = graph.add_node("Custom",330,0)
        node.props = copy.deepcopy(props or tax_definition())
        out = graph.add_node("Print",660,0)
        out.props["label"] = ""
        graph.connect(main.id,"next",node.id,"run")
        graph.connect(node.id,"next",out.id,"run")
        graph.connect(node.id,"result",out.id,"value")
        return graph,node

    def run_graph(self,graph,trace=False):
        return subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(graph,trace=trace).code],
                              capture_output=True,text=True,timeout=5)

    def test_named_inputs_defaults_and_wiring(self):
        graph,node = self.graph()
        self.assertEqual(list(ports(node)[0]),["run","price","rate"])
        self.assertEqual(self.run_graph(graph).stdout,"110.0\n")
        price = graph.add_node("Variable",0,250)
        price.props["value"] = "200"
        graph.connect(price.id,"value",node.id,"price")
        self.assertEqual(self.run_graph(graph).stdout,"220.0\n")

    def test_one_and_three_inputs_and_renaming(self):
        for inputs,expression,expected in (([dict(name="x",default="3")],"x * x","9.0"),
                                          ([dict(name="b",default="2"),dict(name="price",default="3"),dict(name="offset",default="4")],"round(b * price + offset, 2)","10.0")):
            with self.subTest(expression=expression):
                props = tax_definition()
                props.update(inputs=inputs,expression=expression)
                graph,_ = self.graph(props)
                result = self.run_graph(graph)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_runtime_error_identifies_custom_node(self):
        props = tax_definition()
        props["expression"] = "price / rate"
        props["inputs"][1]["default"] = "0"
        graph,node = self.graph(props)
        result = self.run_graph(graph,trace=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("PYDROP_ERROR:"+node.id,result.stderr)

    def test_invalid_definitions_are_rejected(self):
        mutations = [lambda p:p.update(inputs=[]),lambda p:p.update(expression="unknown + 1"),
                     lambda p:p["inputs"][1].update(name="price"),lambda p:p["inputs"][0].update(name="run"),
                     lambda p:p.update(expression="__import__('os')"),lambda p:p["inputs"][0].update(default="NaN"),
                     lambda p:p.update(template_name="Print")]
        for mutate in mutations:
            props = tax_definition()
            mutate(props)
            with self.subTest(props=props),self.assertRaises(GraphError):
                validate_template(props)

    def test_library_save_reload_duplicates_and_external_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"custom_nodes.json"
            self.assertEqual(load_templates(path),{})
            templates = save_template(path,{},tax_definition())
            self.assertEqual(load_templates(path),templates)
            before = path.read_bytes()
            duplicate = tax_definition()
            duplicate["template_name"] = "add TAX"
            with self.assertRaises(GraphError):
                save_template(path,templates,duplicate)
            self.assertEqual(path.read_bytes(),before)
            second = tax_definition()
            second["template_name"] = "Another tax"
            templates = save_template(path,{},second)
            self.assertEqual(set(templates),{"Add tax","Another tax"})
            self.assertFalse(path.with_name(path.name+".tmp").exists())

    def test_graph_export_is_independent_of_library(self):
        graph,_ = self.graph()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"shared.nodeforge.json"
            graph.save(path)
            loaded = Graph.load(path)
            self.assertEqual(loaded.to_dict(),graph.to_dict())
            exported = Path(directory)/"standalone.py"
            exported.write_text(compile_graph(loaded).code,encoding="utf-8")
            result = subprocess.run([sys.executable,str(exported)],cwd=directory,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,"110.0\n")

    def test_malformed_library_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"custom_nodes.json"
            for data in ("not json",json.dumps({"version":99,"nodes":[]})):
                path.write_text(data,encoding="utf-8")
                with self.assertRaises(GraphError):
                    load_templates(path)
