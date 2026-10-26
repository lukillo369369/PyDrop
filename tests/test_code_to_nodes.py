"""Statement parsing, transactional insertion, and exported graph behavior."""
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, demo_graph, MAX_NODES
from code_to_nodes import translate_code, looks_like_code


class CodeDropTests(unittest.TestCase):
    def graph(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        return graph

    def execute(self, plan, reply=None):
        graph = plan.graph
        entries = [n for n in graph.nodes.values() if n.id in plan.added
                   and n.kind in ("Print","Input","Random") and graph.input_edge(n.id,"run") is None]
        if entries:
            graph.connect("n1","next",entries[0].id,"run")
        return subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(graph).code],
                              input=reply,capture_output=True,text=True,encoding="utf-8",timeout=5)

    def test_constant_assignments_and_coordinates(self):
        for code,name,kind,value in (('speed = 10',"speed","Number","10"),
                                    ('offset = -2.5',"offset","Number","-2.5"),
                                    ('enabled = False',"enabled","Boolean","False"),
                                    ('greeting = "Hello α"',"greeting","Text","Hello α")):
            with self.subTest(code=code):
                original = self.graph()
                before = original.to_dict()
                plan = translate_code(code,original,123,-45)
                node = plan.graph.nodes[plan.primary]
                self.assertEqual(node.props,dict(name=name,type=kind,value=value))
                self.assertEqual((node.x,node.y),(123,-45))
                self.assertEqual(original.to_dict(),before)

    def test_nested_expressions_export_with_expected_output(self):
        for code,expected in (('print("Hello")',"Hello\n"),('print()',"\n"),
                              ('print(2 + 3 * 4)',"14.0\n"),('print("a" + "b")',"ab\n"),
                              ('print(float("12.5") + 1)',"13.5\n"),('print(str(10))',"10\n"),
                              ('print(not False)',"True\n"),('print(True and False or True)',"True\n"),
                              ('print(True and True and False)',"False\n"),
                              ('print(10 >= 5)',"True\n"),('print(random.Random(42).randint(1,6))',"6\n")):
            with self.subTest(code=code):
                plan = translate_code(code,self.graph(),100,100)
                result = self.execute(plan)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,expected)

    def test_nested_input_is_wired_before_print(self):
        plan = translate_code('print("Hello " + input("Name: "))',self.graph(),100,100)
        result = self.execute(plan,"Ada\n")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,"Name: Hello Ada\n")

    def test_existing_variables_are_wired_without_changing_original(self):
        graph = demo_graph()
        before = graph.to_dict()
        plan = translate_code('print(mass_kg)',graph,100,100)
        self.assertEqual(len(plan.added),1)
        edge = plan.graph.input_edge(plan.primary,"value")
        self.assertEqual(edge.source,"n2")
        self.assertEqual(graph.to_dict(),before)

    def test_invalid_or_unsupported_code_is_atomic(self):
        for code in ('print(', 'x = 1; x = 2', 'import os', '__import__("os")',
                     'open("file.txt", "w")', 'print(unknown_value)', 'x = open("file")',
                     'print("hi", end="")', 'x = 1e999', 'random.Random("bad").randint(1,6)'):
            with self.subTest(code=code):
                graph = self.graph()
                before = graph.to_dict()
                with self.assertRaises(GraphError):
                    translate_code(code,graph,0,0)
                self.assertEqual(graph.to_dict(),before)

    def test_duplicate_and_reserved_names_are_rejected(self):
        for code in ('mass_kg = 4', 'print = 3'):
            with self.subTest(code=code),self.assertRaises(GraphError):
                translate_code(code,demo_graph(),0,0)

    def test_node_limit_does_not_partially_insert(self):
        graph = self.graph()
        for i in range(MAX_NODES-2):
            graph.add_node("Variable",0,i*200)
        before = graph.to_dict()
        with self.assertRaises(GraphError):
            translate_code('print(2 + 3)',graph,0,0)
        self.assertEqual(graph.to_dict(),before)

    def test_code_detection_preserves_node_name_search(self):
        for text in ('Math','Text Join','Boolean Logic','console','not_a_node'):
            self.assertFalse(looks_like_code(text))
        for text in ('print("hi")','x = 1','not False','import os','10'):
            self.assertTrue(looks_like_code(text))
