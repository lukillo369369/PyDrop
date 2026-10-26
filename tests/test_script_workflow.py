"""Full script imports, execution insertion, layout, and captured values."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph, ports
from code_to_nodes import translate_code, connect_drop


class ScriptWorkflowTests(unittest.TestCase):
    def original(self):
        graph = Graph()
        graph.add_node("Main",0,0)
        return graph

    def plan(self, source, original=None, mode="After Main", selected=None):
        plan = translate_code(source,original if original is not None else self.original(),100,100)
        return connect_drop(plan,mode,selected)

    def execute(self, plan, reply=None, trace=False):
        return subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(plan.graph,trace=trace).code],
                              input=reply,capture_output=True,text=True,encoding="utf-8",timeout=5)

    def test_expression_assignment_and_named_references(self):
        plan = self.plan("price = 12.5\nquantity = 3\ntotal = price * quantity\ncopy_total = total\nprint(copy_total)")
        result = self.execute(plan)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,"37.5\n")
        self.assertEqual(sum(n.kind == "Assign" for n in plan.graph.nodes.values()),2)

    def test_input_assignment_and_text_inference(self):
        plan = self.plan('name = input("Your name? ")\ngreeting = "Hello " + name\nprint(greeting)')
        result = self.execute(plan,"Ada\n")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,"Your name? Hello Ada\n")
        self.assertTrue(any(n.kind == "Text Join" for n in plan.graph.nodes.values()))

    def test_after_main_preserves_existing_successor(self):
        original = self.original()
        old = original.add_node("Print",400,0)
        old.props.update(label="",value="old")
        original.connect("n1","next",old.id,"run")
        before = original.to_dict()
        plan = self.plan('print("new")',original)
        self.assertEqual(original.to_dict(),before)
        self.assertEqual(plan.graph.next_node(plan.last_action),old.id)
        self.assertEqual(self.execute(plan).stdout,"new\nold\n")

    def test_after_selected_preserves_both_sides(self):
        original = self.original()
        first = original.add_node("Print",330,0)
        first.props.update(label="",value="first")
        last = original.add_node("Print",660,0)
        last.props.update(label="",value="last")
        original.connect("n1","next",first.id,"run")
        original.connect(first.id,"next",last.id,"run")
        plan = self.plan('print("middle")',original,"After selected",first.id)
        self.assertEqual(self.execute(plan).stdout,"first\nmiddle\nlast\n")

    def test_disconnected_and_invalid_anchor(self):
        original = self.original()
        value = original.add_node("Variable",400,0)
        plan = self.plan('print("new")',original,"Disconnected")
        self.assertIsNone(plan.graph.next_node("n1"))
        before = original.to_dict()
        with self.assertRaises(GraphError):
            self.plan('print("new")',original,"After selected",value.id)
        self.assertEqual(original.to_dict(),before)

    def test_empty_draft_gets_main_and_round_trips(self):
        plan = self.plan("a = 2\nb = 3\ntotal = a + b\nprint(total)",Graph())
        self.assertEqual(self.execute(plan).stdout,"5.0\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"script.nodeforge.json"
            plan.graph.save(path)
            self.assertEqual(Graph.load(path).to_dict(),plan.graph.to_dict())
        self.assert_nonoverlapping(plan)

    def assert_nonoverlapping(self, plan):
        nodes = [plan.graph.nodes[node_id] for node_id in plan.added]
        for index,a in enumerate(nodes):
            ah = 54+max(map(len,ports(a)))*29+48
            for b in nodes[index+1:]:
                bh = 54+max(map(len,ports(b)))*29+48
                overlap = a.x < b.x+270 and b.x < a.x+270 and a.y < b.y+bh and b.y < a.y+ah
                self.assertFalse(overlap,f"{a.id} overlaps {b.id}")

    def test_nested_group_layout_does_not_overlap(self):
        plan = self.plan('a = 3\nb = 4\nc = (a + b) * (a - b)\nlabel = "Result " + str(c)\nprint(label)')
        self.assert_nonoverlapping(plan)
        self.assertEqual(self.execute(plan).stdout,"Result -7.0\n")

    def test_line_error_leaves_original_unchanged(self):
        original = self.original()
        before = original.to_dict()
        with self.assertRaisesRegex(GraphError,"Line 2"):
            self.plan("price = 2\ntotal = missing * price",original)
        self.assertEqual(original.to_dict(),before)
        with self.assertRaisesRegex(GraphError,"reassignment"):
            self.plan("price = 2\nprice = 3",original)

    def test_assign_result_requires_execution(self):
        plan = self.plan("price = 2\ntotal = price * 3\nprint(total)")
        plan.graph.connect("n1","next",plan.primary,"run")
        with self.assertRaisesRegex(GraphError,"must run before"):
            compile_graph(plan.graph)

    def test_trace_values_include_assignment_and_expression(self):
        plan = self.plan("price = 2\ntotal = price * 3\nprint(total)")
        result = self.execute(plan,trace=True)
        self.assertEqual(result.returncode,0,result.stderr)
        values = {data[0]:data[1:] for line in result.stderr.splitlines() if line.startswith("PYDROP_VALUE:")
                  for data in [json.loads(line.split(":",1)[1])]}
        total = next(n for n in plan.graph.nodes.values() if n.kind == "Assign")
        math = next(n for n in plan.graph.nodes.values() if n.kind == "Math")
        self.assertEqual(values[total.id],["float","6.0"])
        self.assertEqual(values[math.id],["float","6.0"])
        self.assertEqual(values[plan.primary],["float","6.0"])
