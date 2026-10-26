"""Truth tables, range boundaries, and seeded random execution behavior."""
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from graph_core import Graph, GraphError, compile_graph
from example_graphs import controlled_temperature_demo


class LogicRangeRandomTests(unittest.TestCase):
    def graph_with(self, kind, **props):
        g = Graph()
        main = g.add_node("Main",0,0)
        value = g.add_node(kind,300,0)
        value.props.update(props)
        out = g.add_node("Print",600,0)
        out.props.update(label="")
        if kind == "Random":
            g.connect(main.id,"next",value.id,"run")
            g.connect(value.id,"next",out.id,"run")
        else:
            g.connect(main.id,"next",out.id,"run")
        g.connect(value.id,"result",out.id,"value")
        return g,value,out

    def run_graph(self,g,trace=False):
        return subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(g,trace=trace).code],
                              capture_output=True,text=True,encoding="utf-8",timeout=5)

    def test_boolean_truth_tables(self):
        for operator in ("AND","OR","NOT"):
            for a in (False,True):
                for b in (False,True):
                    with self.subTest(operator=operator,a=a,b=b):
                        g,_,_ = self.graph_with("Boolean Logic",operator=operator,a=str(a),b=str(b))
                        expected = not a if operator == "NOT" else ((a and b) if operator == "AND" else (a or b))
                        result = self.run_graph(g)
                        self.assertEqual(result.returncode,0,result.stderr)
                        self.assertEqual(result.stdout.strip(),str(expected))

    def test_not_does_not_evaluate_unused_b(self):
        g,logic,_ = self.graph_with("Boolean Logic",operator="NOT",a="True")
        broken = g.add_node("Math",0,200)
        broken.props.update(operator="/",a="1",b="0")
        g.connect(broken.id,"result",logic.id,"b")
        result = self.run_graph(g)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),"False")

    def test_boolean_rejects_text_and_highlights_logic(self):
        g,logic,_ = self.graph_with("Boolean Logic")
        text = g.add_node("Variable",0,200)
        text.props.update(type="Text",value="False")
        g.connect(text.id,"value",logic.id,"a")
        result = self.run_graph(g,trace=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Boolean Logic inputs must be True or False",result.stderr)
        self.assertIn("PYDROP_ERROR:"+logic.id,result.stderr)

    def test_controller_both_conditions(self):
        for temperature,enabled,expected in (("85","True","Fan ON"),("85","False","Fan OFF"),("79","True","Fan OFF"),("80","True","Fan ON")):
            with self.subTest(temperature=temperature,enabled=enabled):
                g = controlled_temperature_demo()
                g.nodes["n2"].props["value"] = temperature
                g.nodes["n7"].props["value"] = enabled
                result = self.run_graph(g)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue(result.stdout.startswith(expected))

    def test_clamp_boundaries(self):
        for value,minimum,maximum,expected in (("-1","0","10",0),("0","0","10",0),("5","0","10",5),
                                               ("10","0","10",10),("11","0","10",10),("99","3","3",3),
                                               ("-5","-10","-2",-5)):
            with self.subTest(value=value,minimum=minimum,maximum=maximum):
                g,_,_ = self.graph_with("Clamp",value=value,min=minimum,max=maximum)
                result = self.run_graph(g)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(float(result.stdout),expected)

    def test_wired_bounds_override_defaults(self):
        g,node,_ = self.graph_with("Clamp",value="5",min="100",max="0")
        for port,value in (("min","1"),("max","10")):
            source = g.add_node("Variable",0,200)
            source.props.update(value=value)
            g.connect(source.id,"value",node.id,port)
        result = self.run_graph(g)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(float(result.stdout),5)

    def test_invalid_bounds_and_integer_fraction_report_node(self):
        for kind,props,message in (("Clamp",dict(min="10",max="1"),"minimum cannot exceed maximum"),
                                   ("Random",dict(min="10",max="1"),"minimum cannot exceed maximum"),
                                   ("Random",dict(min="1.5",max="6"),"bounds must be whole numbers")):
            with self.subTest(kind=kind,props=props):
                g,node,_ = self.graph_with(kind,**props)
                result = self.run_graph(g,trace=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(message,result.stderr)
                self.assertIn("PYDROP_ERROR:"+node.id,result.stderr)

    def test_random_seed_and_range_both_modes(self):
        for mode,minimum,maximum in (("Integer","-6","6"),("Decimal","-1.25","2.5")):
            with self.subTest(mode=mode):
                g,node,_ = self.graph_with("Random",mode=mode,min=minimum,max=maximum,seed="42")
                first = self.run_graph(g)
                second = self.run_graph(g)
                self.assertEqual(first.returncode,0,first.stderr)
                self.assertEqual(first.stdout,second.stdout)
                value = float(first.stdout)
                self.assertGreaterEqual(value,float(minimum))
                self.assertLessEqual(value,float(maximum))
                if mode == "Integer":
                    self.assertEqual(value,int(value))
                node.props["seed"] = ""
                for _ in range(4):
                    result = self.run_graph(g)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertGreaterEqual(float(result.stdout),float(minimum))
                    self.assertLessEqual(float(result.stdout),float(maximum))

    def test_random_shared_result_is_generated_once(self):
        g,node,first = self.graph_with("Random",mode="Decimal")
        second = g.add_node("Print",900,0)
        second.props.update(label="")
        g.connect(first.id,"next",second.id,"run")
        g.connect(node.id,"result",second.id,"value")
        result = self.run_graph(g)
        self.assertEqual(result.returncode,0,result.stderr)
        a,b = result.stdout.splitlines()
        self.assertEqual(a,b)

    def test_random_requires_execution(self):
        g,node,out = self.graph_with("Random")
        g.connect("n1","next",out.id,"run")
        with self.assertRaisesRegex(GraphError,"must run before") as error:
            compile_graph(g)
        self.assertEqual(error.exception.node_id,node.id)

    def test_random_equal_bounds(self):
        for mode in ("Integer","Decimal"):
            g,_,_ = self.graph_with("Random",mode=mode,min="3",max="3")
            result = self.run_graph(g)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(float(result.stdout),3)

    def test_new_properties_reject_invalid_values(self):
        for kind,props in (("Random",dict(seed="not an integer")),("Random",dict(mode="Unknown")),
                           ("Random",dict(seed="1"*50)),("Clamp",dict(value="NaN")),
                           ("Boolean Logic",dict(operator="XOR"))):
            with self.subTest(kind=kind,props=props):
                g,node,_ = self.graph_with(kind,**props)
                with self.assertRaises(GraphError) as error:
                    compile_graph(g)
                self.assertEqual(error.exception.node_id,node.id)
