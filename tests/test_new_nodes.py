"""Behavioral coverage for conversions, text composition, and interactive input."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph
from example_graphs import interactive_calculator_demo


class NewNodeTests(unittest.TestCase):
    def run_graph(self, graph, text=None, trace=False):
        return subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(graph,trace=trace).code],
                              input=text,capture_output=True,text=True,encoding="utf-8",timeout=5)

    def value_graph(self, kind, **props):
        graph = Graph()
        main = graph.add_node("Main",0,0)
        value = graph.add_node(kind,0,200)
        value.props.update(props)
        out = graph.add_node("Print",350,0)
        out.props.update(label="")
        graph.connect(main.id,"next",out.id,"run")
        graph.connect(value.id,"result",out.id,"value")
        return graph,value

    def test_conversions(self):
        for target,text,expected in (("Number","12.5","12.5"),("Boolean"," False ","False"),
                                     ("Boolean","TRUE","True"),("Text","hello α","hello α")):
            with self.subTest(target=target,text=text):
                graph,_ = self.value_graph("Convert",type=target,value=text)
                result = self.run_graph(graph)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_invalid_conversion_reports_exact_node(self):
        for target,text in (("Number","oops"),("Number","NaN"),("Number","1e101"),("Boolean","maybe")):
            with self.subTest(target=target,text=text):
                graph,node = self.value_graph("Convert",type=target,value=text)
                result = self.run_graph(graph,trace=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn("PYDROP_ERROR:"+node.id,result.stderr)
                self.assertIn("Convert to "+target,result.stderr)

    def test_text_join_with_number_and_unicode(self):
        graph,node = self.value_graph("Text Join",a="Total α:",separator=" ")
        number = graph.add_node("Variable",0,400)
        number.props.update(value="12.5")
        graph.connect(number.id,"value",node.id,"b")
        result = self.run_graph(graph)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),"Total α: 12.5")

    def test_interactive_calculator_export(self):
        result = self.run_graph(interactive_calculator_demo(),"2.5\n4\n")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("Sum: 6.5",result.stdout)

    def test_interactive_command_line_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory)/"calculator.nodeforge.json"
            interactive_calculator_demo().save(graph_path)
            app = Path(__file__).resolve().parents[1]/"nodeforge.py"
            result = subprocess.run([sys.executable,str(app),str(graph_path),"--run"],
                                    input="2\n3\n",capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn("Sum: 5.0",result.stdout)

    def test_trace_input_protocol_handles_unicode_and_empty_text(self):
        import json
        graph = Graph()
        main = graph.add_node("Main",0,0)
        ask = graph.add_node("Input",300,0)
        out = graph.add_node("Print",600,0)
        out.props.update(label="")
        graph.connect(main.id,"next",ask.id,"run")
        graph.connect(ask.id,"next",out.id,"run")
        graph.connect(ask.id,"result",out.id,"value")
        for text in ("", "α\nsecond line"):
            with self.subTest(text=text):
                result = self.run_graph(graph,json.dumps(text)+"\n",trace=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,text+"\n")
                self.assertIn("PYDROP_INPUT:",result.stderr)
        result = self.run_graph(graph,"",trace=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Input cancelled",result.stderr)

    def test_input_result_requires_execution_first(self):
        graph = interactive_calculator_demo()
        graph.connect("n1","next","n8","run")
        with self.assertRaisesRegex(GraphError,"must run before"):
            compile_graph(graph)

    def test_new_property_validation(self):
        graph,_ = self.value_graph("Convert",type="invalid")
        with self.assertRaises(GraphError):
            compile_graph(graph)
