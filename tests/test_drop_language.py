"""Drop syntax, diagnostics, string preservation, and executable exports."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from graph_core import Graph,GraphError,compile_graph
from code_to_nodes import connect_drop
from drop_language import SAMPLE,to_python,translate_drop,KEYWORDS


class DropLanguageTests(unittest.TestCase):
    def execute(self,source,reply=None):
        original = Graph()
        original.add_node("Main",0,0)
        plan = connect_drop(translate_drop(source,original,100,100),"After Main")
        result = subprocess.run([sys.executable,"-I","-X","utf8","-c",compile_graph(plan.graph).code],
                                input=reply,capture_output=True,text=True,encoding="utf-8",timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def test_starter_program_input_and_assignments(self):
        self.assertEqual(self.execute(SAMPLE,"Ada\n"),"Your name? Hello Ada\n37.5\n")

    def test_operator_precedence_and_parentheses(self):
        self.assertEqual(self.execute("say 2 plus 3 times 4\nsay (2 plus 3) times 4\nsay 8 divided by 2\nsay 2 power of 3\nsay 7 modulo 3\nsay 5 minus 8"),
                         "14.0\n20.0\n4.0\n8.0\n1.0\n-3.0\n")

    def test_boolean_and_comparison_phrases(self):
        source = "\n".join(("say 8 greater than 3","say 8 greater than or equal to 8",
                            "say 2 less than 3","say 2 less than or equal to 2",
                            "say 2 equal to 2","say 2 not equal to 3",
                            "say true and false","say true or false","say not false"))
        self.assertEqual(self.execute(source),"True\nTrue\nTrue\nTrue\nTrue\nTrue\nFalse\nTrue\nTrue\n")

    def test_strings_comments_and_unicode_are_preserved(self):
        source = '# header\nlet greeting be "say times plus be # α" # comment\nsay greeting\nsay "it\\\"s not equal to"'
        self.assertEqual(self.execute(source),'say times plus be # α\nit"s not equal to\n')

    def test_case_insensitive_keywords_preserve_names(self):
        self.assertEqual(self.execute("LET Price BE 3\nSAY Price TIMES 2\nSAY TRUE"),"6.0\nTrue\n")

    def test_diagnostics_keep_original_line_numbers_and_graph(self):
        original = Graph()
        original.add_node("Main",0,0)
        before = original.to_dict()
        for source,line in (("\n# comment\nlet total be missing times 2",3),
                            ("let x be 1\nlet y 2",2),("ask 10 into answer",1),
                            ("let x be 2\nsay x plus",2),
                            ('say "hello"\nimport os',2),("let times be 3",1),
                            ("say",1),("say (1 plus 2",1)):
            with self.subTest(source=source):
                with self.assertRaisesRegex(GraphError,f"Line {line}"):
                    translate_drop(source,original,0,0)
                self.assertEqual(original.to_dict(),before)

    def test_arbitrary_calls_are_not_executed(self):
        with self.assertRaises(GraphError):
            translate_drop('say __import__("os").getcwd()',Graph(),0,0)
        with self.assertRaises(GraphError):
            to_python('say 1; say 2')

    def test_customizable_statement_keywords(self):
        with patch.dict(KEYWORDS,{"define":"set","output":"show"}):
            self.assertEqual(self.execute("set amount be 4\nshow amount"),"4.0\n")

    def test_cli_export_and_run_drop_files(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"program.py"
            source = root/"examples"/"logic.drop"
            result = subprocess.run([sys.executable,str(root/"nodeforge.py"),str(source),"--export",str(output)],
                                    capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([sys.executable,str(output)],capture_output=True,text=True,timeout=5,cwd=directory)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,"True\nFalse\n")
            source = root/"examples"/"shopping.drop"
            original = source.read_bytes()
            result = subprocess.run([sys.executable,str(root/"nodeforge.py"),str(source),"--run"],
                                    input="Ada\n",capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn("Hello Ada\n37.5",result.stdout)
            self.assertEqual(source.read_bytes(),original)
