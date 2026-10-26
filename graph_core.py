"""NodeForge's graph model and Python compiler. No GUI dependencies.

Execution wires form a tree rooted at Main; data wires form an acyclic graph.
Function results become available only after their execution node has run.
"""
from __future__ import annotations

import ast
import builtins
import copy
import json
import keyword
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import engineering_units
import library_runtime
import data_runtime
import serial_runtime
from node_libraries import (LibraryCatalog, LibraryError, default_value, validate_reference,
                            implementation_name, implementation_source, reference_uses_units, validate_port_types)
from engineering_units import ENGINEERING, UnitError, unit_spec
from unit_validation import validate_dimensions

VERSION = 1
MAX_NODES = 120
MAX_FILE_BYTES = 2_000_000
KINDS = ("Main", "Variable", "Function", "Math", "Compare", "Branch", "Print", "Convert", "Text Join", "Input",
         "Boolean Logic", "Clamp", "Random", "Assign", "Custom", "Function Call", "Function Inputs", "Return",
         "Quantity", "Unit Convert", "Force", "Torque", "Ohm's Law", "Electrical Power", "Library",
         "CSV Input", "Data Table", "Line Plot", "Scatter Plot", "CSV Output",
         "Serial Connect", "Serial Send", "Serial Read Line", "Parse Value", "Serial Disconnect")
DATA_KINDS = ("CSV Input", "Data Table", "Line Plot", "Scatter Plot", "CSV Output")
SERIAL_KINDS = ("Serial Connect", "Serial Send", "Serial Read Line", "Parse Value", "Serial Disconnect")
SERIAL_EXEC_KINDS = tuple(kind for kind in SERIAL_KINDS if kind != "Parse Value")
COLORS = {
    "Main": "#e9b563", "Variable": "#50caa1", "Function": "#a793f5",
    "Math": "#61b9f5", "Compare": "#e58bbc", "Branch": "#edb86c", "Print": "#64c9da",
    "Convert": "#b4a0f5", "Text Join": "#e5a5d0", "Input": "#7dd8e8",
    "Boolean Logic": "#e5a5d0", "Clamp": "#79c8ef", "Random": "#f3bc78",
    "Assign": "#8dd8ab",
    "Custom": "#bda4f5",
    "Library": "#d8a4ef",
    "Function Call": "#bda4f5", "Function Inputs": "#66e0bb", "Return": "#bda4f5",
    **{kind: "#69b9dd" for kind in DATA_KINDS},
    **{kind: "#e5ae68" for kind in SERIAL_KINDS},
    **{kind: "#71cbd4" for kind in ("Quantity", "Unit Convert", *ENGINEERING)},
}
DESCRIPTIONS = {
    "Main": "The entry point. Follow its white wire to run your program.",
    "Variable": "A named number, text, or boolean. Its green pin supplies a value.",
    "Function": "A numeric formula with inputs a, b, and c. Run it before using its result.",
    "Math": "Combine two numbers. Evaluated when another node needs its result.",
    "Compare": "Compare two values to produce True or False.",
    "Branch": "Follow True or False using Python truthiness (0 and empty text are false).",
    "Print": "Display a value in the output panel.",
    "Convert": "Convert a value to Number, Text, or Boolean. Boolean text must be True or False.",
    "Text Join": "Combine two values as text, with an optional separator.",
    "Input": "Ask a question when execution reaches this node. Its result is text; use Convert for numbers.",
    "Boolean Logic": "Combine True/False values with AND or OR. NOT flips a and ignores b. Use Compare or Convert for other values.",
    "Clamp": "Keep a number between minimum and maximum. The minimum must not exceed the maximum.",
    "Random": "Generate one integer or decimal when executed. Blank seed varies each run; an integer seed repeats the result.",
    "Assign": "Capture a value under a name when executed. Its result is available to later actions.",
    "Custom": "Your saved numeric calculation. Connect its white execution pins before using its result.",
    "Library": "Executable Python from an explicitly installed, versioned library. Runs when reached by an execution wire.",
    "Function Call": "Run a reusable function graph. Double-click to edit its body. Results are available after it runs.",
    "Function Inputs": "Named arguments supplied by the caller. Wire these outputs into this function's calculations.",
    "Return": "End this function and send its named values back to the caller.",
    "Quantity": "Attach units to a numeric magnitude. Use 1 for a unitless quantity; compound units such as m/s^2 are supported.",
    "Unit Convert": "Convert a quantity to compatible units without changing its dimension. Connected values override the default and its source unit.",
    "Force": "F = mass × acceleration. Requires mass and acceleration quantities; returns newtons (N).",
    "Torque": "Torque = force × perpendicular distance. Returns N*m; assumes a perpendicular lever arm.",
    "Ohm's Law": "V = current × resistance. Requires current and resistance quantities; returns volts (V).",
    "Electrical Power": "P = voltage × current. Returns watts (W); use for DC or instantaneous power.",
    "CSV Input": "Read UTF-8 CSV measurement columns. data preserves text; selected x/y outputs supply numeric vectors. Relative paths start at the project folder.",
    "Data Table": "Inspect a table inside PyDrop. Select columns and optionally append calculated values as a named derived column; data passes the full selected table onward.",
    "Line Plot": "Plot numeric columns as a connected line. Select table columns or connect x/y vectors; set a title and axis labels in Properties.",
    "Scatter Plot": "Plot numeric columns as individual points. Select table columns or connect x/y vectors; set a title and axis labels in Properties.",
    "CSV Output": "Write selected table columns to UTF-8 CSV with a header. Existing files require explicit Overwrite; writes publish atomically and never append or truncate rows.",
    "Serial Connect": "Explicitly open the selected port and baud rate, or connect to the built-in Arduino simulation. One connection is shared by this run.",
    "Serial Send": "Send a command over the connection opened by Serial Connect. Choose LF, CRLF, or no line ending; this node never connects automatically.",
    "Serial Read Line": "Read one line within the timeout. received is false on timeout; connection status explains a missing or unplugged device.",
    "Parse Value": "Extract a finite number from a sensor line such as temperature=23.5,humidity=48. Set the key and field separator, or leave the key blank for a plain number.",
    "Serial Disconnect": "Close the current serial connection. The connection also closes when the program ends or fails.",
}
MATH_FUNCTIONS = {"abs", "min", "max", "round", "sqrt", "sin", "cos", "tan", "log", "exp"}
RESERVED = MATH_FUNCTIONS | set(dir(builtins)) | {"isfinite", "main", "print", "float", "int", "bool", "str", "math", "pi", "e"}
RESERVED |= {"ENGINEERING", "UNIT_NAMES", "UnitError", "unit_spec", "dimension_name"}
RESERVED |= {"LibraryNodeError"}
RESERVED |= {"DataError"}
RESERVED |= {"SerialError"}


class GraphError(ValueError):
    """An error that can be shown directly to the graph's author."""

    def __init__(self, message, node_id=None):
        super().__init__(message)
        self.node_id = node_id


@dataclass
class Node:
    id: str
    kind: str
    x: float
    y: float
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        if self.kind == "Library":
            return self.props.get("name", "Library node")
        if self.kind == "Function Call":
            return self.props.get("function", "Function Call")
        if self.kind == "Custom":
            return self.props.get("template_name","Custom calculation")
        if self.kind in ("Variable", "Function", "Assign"):
            return str(self.props.get("name", self.kind))
        return {"Math": "Calculate", "Compare": "Compare", "Branch": "If / Else"}.get(self.kind, self.kind)

    def to_dict(self) -> dict:
        return dict(id=self.id, kind=self.kind, x=self.x, y=self.y, props=copy.deepcopy(self.props))


@dataclass(frozen=True)
class Edge:
    source: str
    output: str
    target: str
    input: str

    def to_dict(self) -> dict:
        return dict(source=self.source, output=self.output, target=self.target, input=self.input)


def ports(node: Node) -> tuple[dict[str, str], dict[str, str]]:
    """Return ordered input/output maps with types 'exec' or 'data'."""
    if node.kind == "Library":
        return ({"run": "exec", **{port["name"]: "data" for port in node.props["inputs"]}},
                {"next": "exec", **{port["name"]: "data" for port in node.props["outputs"]}})
    if node.kind in ("Quantity", "Unit Convert"):
        return {"value": "data"}, {"result": "data"}
    if node.kind in ENGINEERING:
        return {pin: "data" for pin in ENGINEERING[node.kind][0]}, {"result": "data"}
    if node.kind == "Function Inputs":
        return {}, {name: "data" for name in node.props["names"]}
    if node.kind == "Return":
        return {"run": "exec", **{item["name"]: "data" for item in node.props["values"]}}, {}
    if node.kind == "Function Call":
        return ({"run": "exec", **{item["name"]: "data" for item in node.props["inputs"]}},
                {"next": "exec", **{name: "data" for name in node.props["returns"]}})
    if node.kind == "Custom":
        return ({"run":"exec",**{item["name"]:"data" for item in node.props["inputs"]}},
                {"next":"exec","result":"data"})
    if node.kind == "CSV Input":
        return {"run": "exec"}, {"next": "exec", "data": "data", "x": "data", "y": "data"}
    if node.kind == "Data Table":
        return {"run": "exec", "data": "data", "values": "data"}, {"next": "exec", "data": "data"}
    if node.kind in ("Line Plot", "Scatter Plot"):
        return {"run": "exec", "data": "data", "x": "data", "y": "data"}, {"next": "exec"}
    if node.kind == "CSV Output":
        return {"run": "exec", "data": "data"}, {"next": "exec"}
    if node.kind == "Serial Connect":
        return {"run": "exec"}, {"next": "exec", "connected": "data", "status": "data"}
    if node.kind == "Serial Send":
        return {"run": "exec", "command": "data"}, {"next": "exec", "sent": "data", "status": "data"}
    if node.kind == "Serial Read Line":
        return {"run": "exec"}, {"next": "exec", "line": "data", "received": "data", "status": "data"}
    if node.kind == "Parse Value":
        return {"line": "data"}, {"result": "data"}
    if node.kind == "Serial Disconnect":
        return {"run": "exec"}, {"next": "exec", "connected": "data", "status": "data"}
    return {
        "Main": ({}, {"next": "exec"}),
        "Variable": ({}, {"value": "data"}),
        "Function": ({"run": "exec", "a": "data", "b": "data", "c": "data"}, {"next": "exec", "result": "data"}),
        "Math": ({"a": "data", "b": "data"}, {"result": "data"}),
        "Compare": ({"a": "data", "b": "data"}, {"result": "data"}),
        "Branch": ({"run": "exec", "condition": "data"}, {"true": "exec", "false": "exec"}),
        "Print": ({"run": "exec", "value": "data"}, {"next": "exec"}),
        "Convert": ({"value": "data"}, {"result": "data"}),
        "Text Join": ({"a": "data", "b": "data"}, {"result": "data"}),
        "Input": ({"run": "exec"}, {"next": "exec", "result": "data"}),
        "Boolean Logic": ({"a": "data", "b": "data"}, {"result": "data"}),
        "Clamp": ({"value": "data", "min": "data", "max": "data"}, {"result": "data"}),
        "Random": ({"run": "exec", "min": "data", "max": "data"}, {"next": "exec", "result": "data"}),
        "Assign": ({"run": "exec", "value": "data"}, {"next": "exec", "result": "data"}),
    }[node.kind]


def identifier(name: Any) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name):
        raise GraphError("Names must begin with a letter and contain only letters, numbers, or underscores (48 characters max).")
    if keyword.iskeyword(name) or name in RESERVED:
        raise GraphError(f"'{name}' is reserved. Choose another name.")
    return name


def literal(value: Any, value_type: str = "Number") -> Any:
    if not isinstance(value, str) or len(value) > 2000:
        raise GraphError("A value must be text no longer than 2,000 characters.")
    if value_type == "Text":
        return value
    if value_type == "Boolean":
        if value.strip().lower() not in ("true", "false"):
            raise GraphError("A Boolean value must be True or False.")
        return value.strip().lower() == "true"
    if value_type != "Number":
        raise GraphError(f"Unknown value type: {value_type}")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise GraphError(f"'{value[:40]}' is not a number.") from None
    if not math.isfinite(result) or abs(result) > 1e100:
        raise GraphError("Numbers must be finite and no larger than 1e100 in magnitude.")
    return result


def formula(text: Any) -> str:
    """Validate numeric expressions, then normalize literals to floats.

    No attributes, indexing, imports, comprehensions, assignment, or arbitrary
    calls. Float literals and numeric inputs prevent huge integer powers and
    accidental string repetition. This is a formula language, not arbitrary
    Python execution or a general security sandbox.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise GraphError("Enter a formula of 1 to 300 characters using a, b, and c.")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise GraphError("Invalid formula. Example: a * b + c") from None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
               ast.IfExp, ast.Name, ast.Load, ast.Constant, ast.Call,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
               ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
               ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise GraphError("Formula is too complex. Split it into several nodes.")
    for part in nodes:
        if not isinstance(part, allowed):
            raise GraphError("Only arithmetic, comparisons, a/b/c, and the listed math functions are allowed.")
        if isinstance(part, ast.Name) and part.id not in {"a", "b", "c", "pi", "e"} | MATH_FUNCTIONS:
            raise GraphError(f"Unknown formula name '{part.id}'. Use a, b, or c for inputs.")
        if isinstance(part, ast.Constant):
            if type(part.value) not in (int, float, bool):
                raise GraphError("Function formulas accept numeric and boolean constants only.")
            try:
                finite = math.isfinite(float(part.value)) and abs(part.value) <= 1e100
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                raise GraphError("Formula constants must be finite and at most 1e100 in magnitude.")
        if isinstance(part, ast.Call):
            if not isinstance(part.func, ast.Name) or part.func.id not in MATH_FUNCTIONS or part.keywords:
                raise GraphError("Use only the listed math functions with positional arguments.")
            name, count = part.func.id, len(part.args)
            limits = (2, 8) if name in ("min", "max") else ((1, 2) if name in ("round", "log") else (1, 1))
            if not limits[0] <= count <= limits[1]:
                raise GraphError(f"'{name}' accepts {limits[0]} to {limits[1]} inputs here.")
            if name == "round" and count == 2:
                ndigits = part.args[1]
                if not isinstance(ndigits, ast.Constant) or type(ndigits.value) is not int or not 0 <= ndigits.value <= 12:
                    raise GraphError("round(value, digits) requires a literal integer from 0 to 12.")

    class Floats(ast.NodeTransformer):
        def visit_Constant(self, node):
            if type(node.value) in (int, float):
                return ast.copy_location(ast.Constant(float(node.value)), node)
            return node

        def visit_Call(self, node):
            # Keep round's precision argument an integer.
            digits = node.args[1].value if node.func.id == "round" and len(node.args) == 2 else None
            self.generic_visit(node)
            if digits is not None:
                node.args[1] = ast.Constant(digits)
            return node

    return ast.unparse(ast.fix_missing_locations(Floats().visit(tree)))


def custom_expression(props):
    name = props.get("template_name")
    if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _]{0,47}",name) or name != name.strip():
        raise GraphError("Node name must start with a letter; use letters, numbers, spaces, or underscores (48 characters max).")
    description = props.get("description","")
    if not isinstance(description,str) or len(description) > 500:
        raise GraphError("Keep the description to 500 characters or fewer.")
    inputs = props.get("inputs")
    if not isinstance(inputs,list) or not 1 <= len(inputs) <= 3:
        raise GraphError("Add one to three inputs, each with a name and a default number.")
    names = []
    for item in inputs:
        if not isinstance(item,dict):
            raise GraphError("Each input needs a name and a default number.")
        key = identifier(item.get("name"))
        if key in ("run","next","result") or len(key) > 16:
            raise GraphError("Use input names of up to 16 characters; run, next, and result are reserved pins.")
        if key in names:
            raise GraphError("Each input needs a different name.")
        names.append(key)
        literal(item.get("default"))
    expression = props.get("expression")
    if not isinstance(expression,str) or not expression.strip() or len(expression) > 300:
        raise GraphError("Enter a formula of up to 300 characters using your input names.")
    try:
        tree = ast.parse(expression.strip(),mode="eval")
    except (SyntaxError,ValueError,RecursionError):
        raise GraphError("Check the formula. Example: price * quantity") from None
    for part in ast.walk(tree):
        if isinstance(part,ast.Name) and part.id not in set(names) | MATH_FUNCTIONS | {"pi","e"}:
            raise GraphError(f"Unknown input '{part.id}'. Use: {', '.join(names)}.")
    mapping = dict(zip(names,("a","b","c")))
    class Rename(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.copy_location(ast.Name(id=mapping.get(node.id,node.id),ctx=node.ctx),node)
    return formula(ast.unparse(ast.fix_missing_locations(Rename().visit(tree))))


def function_pin_names(names, allow_empty=False):
    if not isinstance(names, list) or not (0 if allow_empty else 1) <= len(names) <= 8:
        raise GraphError("Use up to eight named inputs and one to eight named return values.")
    for name in names:
        identifier(name)
        if name in ("run", "next"):
            raise GraphError("run and next are reserved execution pins.")
    if len(set(names)) != len(names):
        raise GraphError("Each function pin needs a unique name.")


def input_default(node, port):
    if node.kind == "Library":
        return node.props["defaults"][port]
    if node.kind in ("Custom", "Function Call", "Return"):
        key = "values" if node.kind == "Return" else "inputs"
        return next(item["default"] for item in node.props[key] if item["name"] == port)
    return node.props.get(port,"")


def validate_props(node: Node) -> None:
    p = node.props
    if node.kind in SERIAL_KINDS:
        fields = {
            "Serial Connect": ("port", "baud", "timeout", "mode"),
            "Serial Send": ("command", "newline"),
            "Serial Read Line": ("timeout",),
            "Parse Value": ("line", "key", "separator"),
            "Serial Disconnect": (),
        }[node.kind]
        for key in fields:
            literal(p.get(key), "Text")
        if node.kind == "Serial Connect":
            if p["mode"] not in ("Simulation", "Hardware"):
                raise GraphError("Choose Simulation or Hardware for the serial connection.")
            if p["mode"] == "Hardware" and not p["port"].strip():
                raise GraphError("Choose or enter a serial port for Hardware mode.")
            if not re.fullmatch(r"[0-9]{1,7}", p["baud"].strip()) or not 1 <= int(p["baud"]) <= 4_000_000:
                raise GraphError("Baud rate must be a whole number from 1 to 4,000,000.")
        if node.kind in ("Serial Connect", "Serial Read Line"):
            timeout = literal(p["timeout"])
            if not 0 <= timeout <= 30:
                raise GraphError("Serial timeout must be from 0 to 30 seconds.")
        if node.kind == "Serial Send" and p["newline"] not in ("LF", "CRLF", "None"):
            raise GraphError("Choose LF, CRLF, or None for the serial line ending.")
        if node.kind == "Parse Value" and not p["separator"]:
            raise GraphError("Enter a nonempty separator for the sensor fields.")
    if node.kind in DATA_KINDS:
        fields = {
            "CSV Input": ("path", "delimiter", "x_column", "y_column"),
            "Data Table": ("columns", "column_name"),
            "Line Plot": ("x_column", "y_column", "title", "x_label", "y_label"),
            "Scatter Plot": ("x_column", "y_column", "title", "x_label", "y_label"),
            "CSV Output": ("path", "columns", "delimiter", "mode"),
        }[node.kind]
        for key in fields:
            literal(p.get(key), "Text")
        if node.kind in ("CSV Input", "CSV Output"):
            try:
                data_runtime._data_delimiter(p["delimiter"])
            except data_runtime.DataError as exc:
                raise GraphError(str(exc), node.id) from None
        if node.kind == "CSV Output" and p["mode"] not in ("Error if exists", "Overwrite"):
            raise GraphError("Choose Error if exists or Overwrite for CSV Output.", node.id)
    if node.kind == "Library":
        try:
            validate_reference(p)
        except LibraryError as exc:
            raise GraphError(str(exc), node.id) from None
    if node.kind in ("Quantity", "Unit Convert", *ENGINEERING):
        try:
            if node.kind in ENGINEERING:
                for pin in ENGINEERING[node.kind][0]:
                    literal(p.get(pin))
            else:
                literal(p.get("value"))
                target = unit_spec(p.get("unit"))[1]
                if node.kind == "Unit Convert" and unit_spec(p.get("from_unit"))[1] != target:
                    raise UnitError("Unit Convert default source and target units must have compatible dimensions.")
        except UnitError as exc:
            raise GraphError(str(exc), node.id) from None
    if node.kind == "Function Inputs":
        function_pin_names(p.get("names"), allow_empty=True)
    if node.kind in ("Function Call", "Return"):
        key = "values" if node.kind == "Return" else "inputs"
        items = p.get(key)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise GraphError("Function pins need names and default numbers.")
        function_pin_names([item.get("name") for item in items], allow_empty=node.kind == "Function Call")
        for item in items:
            literal(item.get("default"))
        if node.kind == "Function Call":
            identifier(p.get("function"))
            function_pin_names(p.get("returns"))
    if node.kind == "Custom":
        custom_expression(p)
    if node.kind in ("Variable", "Function", "Assign"):
        identifier(p.get("name"))
    if node.kind == "Variable":
        literal(p.get("value"), p.get("type"))
    if node.kind == "Function":
        formula(p.get("expression"))
        for key in ("a", "b", "c"):
            literal(p.get(key))
    if node.kind in ("Math", "Compare"):
        ops = ("+", "-", "*", "/", "//", "%", "**") if node.kind == "Math" else (">", ">=", "<", "<=", "==", "!=")
        if p.get("operator") not in ops:
            raise GraphError("Choose a valid operator.")
        for key in ("a", "b"):
            literal(p.get(key))
    if node.kind == "Branch":
        literal(p.get("condition"), "Boolean")
    if node.kind == "Print":
        literal(p.get("value"), "Text")
        literal(p.get("label"), "Text")
    if node.kind == "Convert":
        if p.get("type") not in ("Number", "Text", "Boolean"):
            raise GraphError("Choose Number, Text, or Boolean as the conversion type.")
        literal(p.get("value"), "Text")
    if node.kind == "Text Join":
        for key in ("a", "b", "separator"):
            literal(p.get(key), "Text")
    if node.kind == "Input":
        literal(p.get("prompt"), "Text")
    if node.kind == "Assign":
        literal(p.get("value"), "Text")
    if node.kind == "Boolean Logic":
        if p.get("operator") not in ("AND", "OR", "NOT"):
            raise GraphError("Choose AND, OR, or NOT.")
        for key in ("a", "b"):
            literal(p.get(key), "Boolean")
    if node.kind in ("Clamp", "Random"):
        for key in (("value", "min", "max") if node.kind == "Clamp" else ("min", "max")):
            literal(p.get(key))
    if node.kind == "Random":
        if p.get("mode") not in ("Integer", "Decimal"):
            raise GraphError("Choose Integer or Decimal for Random.")
        seed = p.get("seed")
        if not isinstance(seed, str) or len(seed) > 49 or (seed.strip() and not re.fullmatch(r"[+-]?[0-9]{1,48}", seed.strip())):
            raise GraphError("Random seed must be blank or an integer of up to 48 digits.")


class Graph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.functions: dict[str, dict] = {}
        # Groups are presentation metadata. They never change execution or data
        # dependencies, and are kept in the same snapshots as the graph itself.
        self.groups: dict[str, dict] = {}

    def add_function(self, name, inputs, returns):
        identifier(name)
        function_pin_names(inputs, allow_empty=True)
        function_pin_names(returns)
        if name in self.functions or len(self.functions) >= 32:
            raise GraphError("Choose a unique function name (32 functions maximum).")
        scopes = [self] + [Graph.from_dict(definition["graph"], _body=True) for definition in self.functions.values()]
        if any(node.props.get("name") == name for graph in scopes for node in graph.nodes.values()):
            raise GraphError("That name is already used by a local variable or formula.")
        if set(inputs) & (set(self.functions) | {name}) or any(name in definition["inputs"] for definition in self.functions.values()):
            raise GraphError("Argument names must differ from reusable function names.")
        body = Graph()
        main = body.add_node("Main", 40, 40)
        arguments = body.add_node("Function Inputs", 40, 230)
        arguments.props = {"names": list(inputs)}
        result = body.add_node("Return", 700, 40)
        result.props = {"values": [{"name": key, "default": "0"} for key in returns]}
        body.connect(main.id, "next", result.id, "run")
        definition = {"inputs": list(inputs), "returns": list(returns), "graph": body.to_dict()}
        self.functions[name] = definition
        return definition

    def add_call(self, name, x, y, library=None):
        definitions = self.functions if library is None else library
        if name not in definitions:
            raise GraphError(f"Unknown reusable function '{name}'.")
        definition = definitions[name]
        node = self.add_node("Function Call", x, y)
        node.props = {"function": name, "inputs": [{"name": key, "default": "0"} for key in definition["inputs"]],
                      "returns": list(definition["returns"])}
        return node

    def add_library_node(self, reference, x, y):
        try:
            props = validate_reference(reference)
        except LibraryError as exc:
            raise GraphError(str(exc)) from None
        node = self.add_node("Library", x, y)
        node.props = props
        return node

    def dependency_issues(self, catalog=None):
        """Report unresolved references without executing installed or project code."""
        catalog = catalog if catalog is not None else LibraryCatalog()
        issues = []
        scopes = [("Project", self)] + [(f"Function {name}", Graph.from_dict(definition["graph"], _body=True))
                                        for name, definition in self.functions.items()]
        for scope, graph in scopes:
            for node in graph.nodes.values():
                if node.kind == "Library":
                    try:
                        catalog.resolve(node.props)
                    except LibraryError as exc:
                        issues.append(f"{scope}: {node.title} ({node.id}): {exc}")
        return list(dict.fromkeys(issues))

    def add_node(self, kind: str, x: float, y: float) -> Node:
        if kind not in KINDS:
            raise GraphError(f"Unknown node kind: {kind}")
        if len(self.nodes) >= MAX_NODES:
            raise GraphError(f"This prototype supports up to {MAX_NODES} nodes.")
        if kind == "Main" and any(n.kind == "Main" for n in self.nodes.values()):
            raise GraphError("There is already a Main node. A program has one entry point.")
        index = max((int(n[1:]) for n in self.nodes), default=0) + 1
        defaults = {
            "Main": {},
            "Variable": {"name": f"value_{index}", "type": "Number", "value": "10"},
            "Function": {"name": f"calculate_{index}", "expression": "a * b + c", "a": "0", "b": "0", "c": "0"},
            "Math": {"operator": "+", "a": "0", "b": "0"},
            "Compare": {"operator": ">", "a": "0", "b": "0"},
            "Branch": {"condition": "True"},
            "Print": {"label": "Result: ", "value": "Hello from NodeForge!"},
            "Convert": {"type": "Number", "value": "0"},
            "Text Join": {"a": "Hello", "b": "pydrop!", "separator": " "},
            "Input": {"prompt": "Enter a value: "},
            "Boolean Logic": {"operator": "AND", "a": "True", "b": "False"},
            "Clamp": {"value": "50", "min": "0", "max": "100"},
            "Random": {"mode": "Integer", "min": "1", "max": "6", "seed": ""},
            "Assign": {"name": f"assigned_{index}", "value": ""},
            "Custom": {"template_name":"Custom calculation","description":"",
                       "inputs":[{"name":"a","default":"0"},{"name":"b","default":"0"}],"expression":"a + b"},
            "Function Call": {"function": "function_1", "inputs": [], "returns": ["result"]},
            "Function Inputs": {"names": []},
            "Return": {"values": [{"name": "result", "default": "0"}]},
            "Library": {},  # add_library_node fills the validated reference.
            "CSV Input": {"path": "", "delimiter": ",", "x_column": "", "y_column": ""},
            "Data Table": {"columns": "", "column_name": "derived"},
            "Line Plot": {"x_column": "", "y_column": "", "title": "", "x_label": "", "y_label": ""},
            "Scatter Plot": {"x_column": "", "y_column": "", "title": "", "x_label": "", "y_label": ""},
            "CSV Output": {"path": "", "columns": "", "delimiter": ",", "mode": "Error if exists"},
            "Serial Connect": {"port": "SIMULATED", "baud": "9600", "timeout": "1", "mode": "Simulation"},
            "Serial Send": {"command": "READ", "newline": "LF"},
            "Serial Read Line": {"timeout": "1"},
            "Parse Value": {"line": "", "key": "temperature", "separator": ","},
            "Serial Disconnect": {},
            "Quantity": {"value": "1", "unit": "m"},
            "Unit Convert": {"value": "1", "from_unit": "m", "unit": "cm"},
            **{name: {pin: "1" for pin in spec[0]} for name, spec in ENGINEERING.items()},
        }
        if "name" in defaults[kind]:
            base = defaults[kind]["name"]
            used_names = {n.props.get("name") for n in self.nodes.values() if n.kind in ("Variable", "Function", "Assign")}
            suffix = 2
            while defaults[kind]["name"] in used_names:
                defaults[kind]["name"] = f"{base}_{suffix}"
                suffix += 1
        node = Node(f"n{index}", kind, float(x), float(y), defaults[kind])
        self.nodes[node.id] = node
        return node

    def remove_node(self, node_id: str) -> None:
        if node_id in self.nodes:
            self.nodes.pop(node_id)
            self.edges = [e for e in self.edges if node_id not in (e.source, e.target)]
            for group_id, group in list(self.groups.items()):
                group["nodes"] = [member for member in group["nodes"] if member != node_id]
                if not group["nodes"]:
                    del self.groups[group_id]

    def _validate_groups(self) -> None:
        """Bound untrusted editor metadata and keep memberships unambiguous."""
        if not isinstance(self.groups, dict) or len(self.groups) > MAX_NODES:
            raise GraphError(f"Use up to {MAX_NODES} labeled groups.")
        grouped = set()
        for group_id, group in self.groups.items():
            if not isinstance(group_id, str) or not re.fullmatch(r"g[1-9][0-9]{0,6}", group_id):
                raise GraphError("Invalid group ID.")
            if not isinstance(group, dict) or set(group) != {"label", "nodes", "collapsed"}:
                raise GraphError("A group needs a label, node membership, and collapsed state.")
            label, members = group["label"], group["nodes"]
            if not isinstance(label, str) or not label.strip() or len(label) > 120 or any(ord(char) < 32 for char in label):
                raise GraphError("Group labels must contain 1 to 120 characters on a single line.")
            if type(group["collapsed"]) is not bool:
                raise GraphError("A group's collapsed state must be True or False.")
            if not isinstance(members, list) or not 1 <= len(members) <= MAX_NODES:
                raise GraphError("A group must contain existing nodes.")
            for member in members:
                if not isinstance(member, str) or member not in self.nodes:
                    raise GraphError("A group refers to a missing node.")
                if member in grouped:
                    raise GraphError("A node can belong to only one group, without duplicate membership.")
                grouped.add(member)

    def input_edge(self, node_id: str, port: str) -> Edge | None:
        return next((e for e in self.edges if e.target == node_id and e.input == port), None)

    def next_node(self, node_id: str, port: str = "next") -> str | None:
        return next((e.target for e in self.edges if e.source == node_id and e.output == port), None)

    def connect(self, source: str, output: str, target: str, input_port: str) -> None:
        if source not in self.nodes or target not in self.nodes:
            raise GraphError("Both ends of a wire must belong to existing nodes.")
        if source == target:
            raise GraphError("A node cannot connect to itself.")
        out_type = ports(self.nodes[source])[1].get(output)
        in_type = ports(self.nodes[target])[0].get(input_port)
        if not out_type or not in_type:
            raise GraphError("That pin does not exist.")
        if out_type != in_type:
            raise GraphError("Connect execution to execution (white), or data to data (green).")
        old = self.edges[:]
        # Replacing an occupied input is intentional. An execution output has
        # one destination; a data output may feed any number of consumers.
        self.edges = [e for e in old if not (e.target == target and e.input == input_port)
                      and not (out_type == "exec" and e.source == source and e.output == output)]
        self.edges.append(Edge(source, output, target, input_port))
        try:
            self._check_cycles(out_type)
            validate_port_types(self, GraphError)
            self.check_dimensions(partial=True)
        except GraphError:
            self.edges = old
            raise

    def check_dimensions(self, *, partial=False):
        # Existing unitless projects retain their original validation timing.
        if not any(node.kind in ("Quantity", "Unit Convert", *ENGINEERING) or
                   (node.kind == "Library" and reference_uses_units(node.props)) for node in self.nodes.values()):
            return
        validate_dimensions(self, GraphError, partial=partial,
                            normalize_formula=lambda node: custom_expression(node.props) if node.kind == "Custom" else formula(node.props["expression"]))

    def _check_cycles(self, kind: str) -> None:
        adjacency = {n: [] for n in self.nodes}
        for edge in self.edges:
            if ports(self.nodes[edge.source])[1][edge.output] == kind:
                adjacency[edge.source].append(edge.target)
        visiting, done = set(), set()

        def visit(node_id):
            if node_id in visiting:
                raise GraphError(f"This would create a {kind} cycle. Loops are not supported in 0.1.")
            if node_id not in done:
                visiting.add(node_id)
                for target in adjacency[node_id]:
                    visit(target)
                visiting.remove(node_id)
                done.add(node_id)
        for node_id in self.nodes:
            visit(node_id)

    def validate(self) -> None:
        if len(self.nodes) > MAX_NODES:
            raise GraphError("Too many nodes.")
        self._validate_groups()
        if sum(n.kind == "Main" for n in self.nodes.values()) != 1:
            raise GraphError("Add exactly one Main node to start the program.")
        names = set()
        for node in self.nodes.values():
            try:
                validate_props(node)
            except GraphError as exc:
                raise GraphError(f"{node.title}: {exc}", node.id) from None
            if node.kind in ("Variable", "Function", "Assign"):
                name = node.props["name"]
                if name in names:
                    raise GraphError(f"The name '{name}' is used twice. Give each variable and function a unique name.", node.id)
                names.add(name)
        seen_inputs, seen_exec = set(), set()
        for edge in self.edges:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise GraphError("A wire refers to a missing node.")
            if edge.source == edge.target:
                raise GraphError("A node cannot connect to itself.")
            out_type = ports(self.nodes[edge.source])[1].get(edge.output)
            in_type = ports(self.nodes[edge.target])[0].get(edge.input)
            if not out_type or out_type != in_type:
                raise GraphError("A wire has missing or incompatible pins.")
            destination = (edge.target, edge.input)
            if destination in seen_inputs:
                raise GraphError("An input can receive only one wire.")
            seen_inputs.add(destination)
            if out_type == "exec":
                origin = (edge.source, edge.output)
                if origin in seen_exec:
                    raise GraphError("An execution output can have only one wire.")
                seen_exec.add(origin)
        self._check_cycles("exec")
        self._check_cycles("data")

        validate_port_types(self, GraphError)
        self.check_dimensions()

    def to_dict(self) -> dict:
        data = {"format": "nodeforge", "version": VERSION,
                "nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [e.to_dict() for e in self.edges]}
        if self.functions:
            data["functions"] = copy.deepcopy(self.functions)
        if self.groups:
            data["groups"] = copy.deepcopy(self.groups)
        return data

    @classmethod
    def from_dict(cls, data: dict, _body=False) -> Graph:
        if not isinstance(data, dict) or data.get("format") != "nodeforge" or data.get("version") != VERSION:
            raise GraphError("This is not a supported NodeForge project.")
        nodes, edges = data.get("nodes"), data.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > MAX_NODES or len(edges) > 500:
            raise GraphError("Invalid or oversized project.")
        graph = cls()
        definitions = data.get("functions", {})
        if not isinstance(definitions, dict) or len(definitions) > 32 or (_body and definitions):
            raise GraphError("Invalid function library. Define up to 32 functions at project level.")
        for name, definition in definitions.items():
            identifier(name)
            if not isinstance(definition, dict):
                raise GraphError("Invalid reusable function definition.")
            function_pin_names(definition.get("inputs"), allow_empty=True)
            function_pin_names(definition.get("returns"))
            body = cls.from_dict(definition.get("graph"), _body=True)
            graph.functions[name] = {"inputs": list(definition["inputs"]), "returns": list(definition["returns"]),
                                     "graph": body.to_dict()}
        try:
            for item in nodes:
                node = Node(**copy.deepcopy(item))
                if not isinstance(node.id, str) or not re.fullmatch(r"n[1-9][0-9]{0,6}", node.id) or node.id in graph.nodes:
                    raise GraphError("Invalid or duplicate node ID.")
                if node.kind not in KINDS or not isinstance(node.props, dict):
                    raise GraphError("Invalid node type or properties.")
                if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e7 for v in (node.x, node.y)):
                    raise GraphError("Invalid node position.")
                validate_props(node)
                graph.nodes[node.id] = node
            graph.edges = [Edge(**item) for item in edges]
            graph.groups = copy.deepcopy(data.get("groups", {}))
            graph._validate_groups()
            # Preserve incomplete drafts: no Main is permitted when loading.
            # All wiring is still checked. Full compilation checks Main/names.
            seen_inputs, seen_outputs = set(), set()
            for edge in graph.edges:
                if any(not isinstance(v, str) for v in (edge.source, edge.output, edge.target, edge.input)):
                    raise GraphError("Invalid wire fields.")
                if edge.source not in graph.nodes or edge.target not in graph.nodes or edge.source == edge.target:
                    raise GraphError("Invalid wire endpoints.")
                out_kind = ports(graph.nodes[edge.source])[1].get(edge.output)
                if out_kind is None or ports(graph.nodes[edge.target])[0].get(edge.input) != out_kind:
                    raise GraphError("Invalid wire pins.")
                dest, origin = (edge.target, edge.input), (edge.source, edge.output)
                if dest in seen_inputs or (out_kind == "exec" and origin in seen_outputs):
                    raise GraphError("Duplicate wiring is not permitted.")
                seen_inputs.add(dest)
                if out_kind == "exec":
                    seen_outputs.add(origin)
            graph._check_cycles("exec")
            graph._check_cycles("data")
            validate_port_types(graph, GraphError)
            graph.check_dimensions(partial=True)
        except (TypeError, KeyError, AttributeError, OverflowError) as exc:
            raise GraphError(f"Malformed project: {exc}") from None
        return graph

    def save(self, path: str | Path) -> None:
        # Write beside the destination then replace, preserving a previous save
        # if serialization or writing fails.
        path = Path(path)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @classmethod
    def load(cls, path: str | Path) -> Graph:
        path = Path(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise GraphError("Project is too large (maximum 2 MB).")
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
            raise GraphError(f"Cannot read project: {exc}") from None


@dataclass
class Compilation:
    code: str
    warnings: list[str]
    executed_nodes: set[str]


def validate_function_library(graph):
    """Check interfaces and the call graph before generating any Python."""
    bodies = {name: Graph.from_dict(definition["graph"], _body=True)
              for name, definition in graph.functions.items()}
    for name, definition in graph.functions.items():
        if set(definition["inputs"]) & set(graph.functions):
            raise GraphError(f"Function '{name}': argument names must differ from reusable function names.")
    dependencies = {}
    for scope, current in [(None, graph), *bodies.items()]:
        definition = graph.functions.get(scope)
        dependencies[scope] = set()
        for node in current.nodes.values():
            if node.kind in ("Variable", "Function", "Assign"):
                if node.props["name"] in graph.functions or (definition and node.props["name"] in definition["inputs"]):
                    raise GraphError("Local names must differ from reusable function and argument names.", node.id)
            if node.kind == "Function Call":
                name = node.props["function"]
                target = graph.functions.get(name)
                if target is None:
                    raise GraphError(f"Reusable function '{name}' is missing.", node.id)
                if [item["name"] for item in node.props["inputs"]] != target["inputs"] or node.props["returns"] != target["returns"]:
                    raise GraphError(f"The call to '{name}' has an outdated interface.", node.id)
                dependencies[scope].add(name)
            if node.kind == "Function Inputs":
                if definition is None or node.props["names"] != definition["inputs"]:
                    raise GraphError("Function Inputs must match this function's named inputs.", node.id)
            if node.kind == "Return":
                if definition is None or [item["name"] for item in node.props["values"]] != definition["returns"]:
                    raise GraphError("Return pins must match this function's named return values.", node.id)
        if definition and sum(n.kind == "Function Inputs" for n in current.nodes.values()) != 1:
            raise GraphError(f"Function '{scope}' needs exactly one Function Inputs node.")
    visiting, done = set(), set()
    def visit(name):
        if name in visiting:
            raise GraphError("Recursive function calls are not supported. Remove the call cycle.")
        if name in done:
            return
        visiting.add(name)
        for target in dependencies[name]:
            visit(target)
        visiting.remove(name)
        done.add(name)
    for name in dependencies:
        visit(name)
    return bodies


def compile_graph(graph: Graph, trace: bool = False, *, library_catalog=None, data_base=None, _function=None, _library=None, _scope="", _units=None, _data=None, _serial=None) -> Compilation:
    if _library is None:
        bodies = validate_function_library(graph)
    else:
        bodies = {}
    graph.validate()
    if _units is None:
        _units = any(node.kind in ("Quantity", "Unit Convert", *ENGINEERING) or
                     (node.kind == "Library" and reference_uses_units(node.props))
                     for current in (graph, *bodies.values()) for node in current.nodes.values())
    if _data is None:
        _data = any(node.kind in DATA_KINDS for current in (graph, *bodies.values()) for node in current.nodes.values())
    if _serial is None:
        _serial = any(node.kind in SERIAL_KINDS for current in (graph, *bodies.values()) for node in current.nodes.values())
    data_views = any(node.kind in ("Data Table", "Line Plot", "Scatter Plot")
                     for current in (graph, *bodies.values()) for node in current.nodes.values())
    library_nodes = [node for current in (graph, *bodies.values()) for node in current.nodes.values() if node.kind == "Library"]
    definitions = {}
    if library_nodes:
        # Always re-read the approved store: deleting or changing an installed
        # file revokes use even when an editor has an older catalog in memory.
        library_catalog = LibraryCatalog(library_catalog.store if library_catalog is not None else None)
        for node in library_nodes:
            try:
                definitions[implementation_name(node.props)] = library_catalog.resolve(node.props)
            except LibraryError as exc:
                raise GraphError(str(exc), node.id) from None
    main = next(n for n in graph.nodes.values() if n.kind == "Main")
    used = {main.id}
    executed = set()
    lines = ["# Generated by NodeForge 0.1", "# Edit the graph to regenerate this file.",
             "from math import sqrt, sin, cos, tan, log, exp, pi, e, isfinite", ""]
    # Embed the exact runtime once. Reusable function bodies share these classes,
    # preserving quantity identity across calls and requiring no local imports.
    if _units and _library is None:
        lines += [Path(engineering_units.__file__).read_text(encoding="utf-8"), ""]
    if _serial and _library is None:
        lines += [Path(serial_runtime.__file__).read_text(encoding="utf-8"), ""]
    if _data and _library is None:
        lines += [Path(data_runtime.__file__).read_text(encoding="utf-8"), ""]
        if data_base is not None:
            lines += [f"_DATA_BASE = _DataPath({str(Path(data_base).resolve())!r})", ""]
        if trace:
            lines += ["def _data_present(node_id, payload):",
                      "    print('PYDROP_DATA:' + _data_json.dumps([node_id, payload]), file=_sys.stderr, flush=True)", ""]
        elif data_views:
            # Exports carry their own dependency-free Tk viewers. The source is
            # embedded so moving a generated .py file requires no PyDrop modules.
            lines += [Path(__file__).with_name("data_ui.py").read_text(encoding="utf-8"), "",
                      "def _data_present(node_id, payload):", "    _data_display(payload)", ""]
        else:
            lines += ["def _data_present(node_id, payload):", "    pass", ""]
    if _units:
        lines += ["sqrt, sin, cos, tan, log, exp = _unit_sqrt, _unit_sin, _unit_cos, _unit_tan, _unit_log, _unit_exp", ""]
    if definitions and _library is None:
        lines += ["# EXECUTABLE PYTHON: implementations from explicitly installed node libraries.",
                  Path(library_runtime.__file__).read_text(encoding="utf-8"), ""]
        for function_name, definition in definitions.items():
            lines += [implementation_source(definition, function_name), ""]
    if trace:
        lines += ["import sys as _sys, json as _json", "def _trace(node_id):", "    print('NODEFORGE_TRACE:' + node_id, file=_sys.stderr, flush=True)", "",
                  "def _inspect(node_id, value):",
                  "    shown = " + ("_data_summary(value)[:240]" if _data else "repr(value[:200] if isinstance(value, str) else value)[:240]"),
                  "    shown_type = " + ("'Quantity' if isinstance(value, _Quantity) else type(value).__name__" if _units else "type(value).__name__"),
                  "    print('PYDROP_VALUE:' + _json.dumps([node_id, shown_type, shown]), file=_sys.stderr, flush=True)",
                  "    return value", ""]
        lines += ["def _node_value(node_id, evaluate):", "    try:", "        return _inspect(node_id, evaluate())",
                  "    except Exception:", "        print('PYDROP_ERROR:' + node_id, file=_sys.stderr, flush=True)",
                  "        raise", ""]
        if _serial and _library is None:
            lines += ["def _serial_status(status, connected, mode, port):",
                      "    print('PYDROP_SERIAL:' + _json.dumps([connected, status, mode, port]), file=_sys.stderr, flush=True)", ""]
    lines += ["def _number(value):"]
    if _units:
        lines += ["    if isinstance(value, _Quantity):", "        _unit_finite(value.si)", "        return value"]
    lines += ["    if not isinstance(value, (int, float)):",
              "        raise TypeError('Numeric inputs must be numbers (Math, Function, Clamp, and Random).')",
              "    value = float(value)", "    if not isfinite(value) or abs(value) > 1e100:",
              "        raise ValueError('Numeric result is non-finite or exceeds 1e100.')",
              "    return value", ""]
    if any(n.kind == "Convert" for n in graph.nodes.values()):
        lines += ["def _convert(value, target):",
                  "    if target == 'Text':", "        return str(value)",
                  "    if target == 'Number':",
                  *(["        if isinstance(value, _Quantity):", "            return float(value)"] if _units else []),
                  "        try:", "            return _number(float(value))",
                  "        except (ValueError, TypeError, OverflowError):",
                  "            raise ValueError('Convert to Number: enter finite numeric text such as 12.5 (magnitude at most 1e100).') from None",
                  "    if isinstance(value, str):", "        text = value.strip().lower()",
                  "        if text not in ('true', 'false'):",
                  "            raise ValueError('Convert to Boolean: enter True or False.')",
                  "        return text == 'true'", "    return bool(value)", ""]
    if any(n.kind == "Boolean Logic" for n in graph.nodes.values()):
        lines += ["def _logic(operation, a, b=False):",
                  "    if not isinstance(a, bool) or (operation != 'NOT' and not isinstance(b, bool)):",
                  "        raise TypeError('Boolean Logic inputs must be True or False. Use Compare or Convert to Boolean.')",
                  "    if operation == 'NOT':", "        return not a",
                  "    return (a and b) if operation == 'AND' else (a or b)", ""]
    if any(n.kind == "Clamp" for n in graph.nodes.values()):
        lines += ["def _clamp(value, minimum, maximum):",
                  "    value, minimum, maximum = _number(value), _number(minimum), _number(maximum)",
                  "    if minimum > maximum:",
                  "        raise ValueError('Clamp minimum cannot exceed maximum. Check the bounds or their connected values.')",
                  "    return min(max(value, minimum), maximum)", ""]
    if any(n.kind == "Random" for n in graph.nodes.values()):
        lines += ["import random as _random", "def _random_value(mode, minimum, maximum, seed):",
                  "    minimum, maximum = float(_number(minimum)), float(_number(maximum))",
                  "    if minimum > maximum:",
                  "        raise ValueError('Random minimum cannot exceed maximum. Check the bounds or their connected values.')",
                  "    generator = _random.Random(seed)", "    if mode == 'Integer':",
                  "        if not minimum.is_integer() or not maximum.is_integer():",
                  "            raise ValueError('Random Integer bounds must be whole numbers. Use Decimal mode for fractional bounds.')",
                  "        return generator.randint(int(minimum), int(maximum))",
                  "    return _number(generator.uniform(minimum, maximum))", ""]
    if any(n.kind == "Input" for n in graph.nodes.values()):
        if trace:
            lines += ["def _ask(node_id, prompt):",
                      "    print('PYDROP_INPUT:' + _json.dumps([node_id, prompt]), file=_sys.stderr, flush=True)",
                      "    reply = _sys.stdin.readline()", "    if not reply:",
                      "        raise EOFError('Input cancelled. Run again to answer the question.')",
                      "    return _json.loads(reply)", ""]
        else:
            lines += ["def _ask(node_id, prompt):", "    return input(prompt)", ""]
    for node in graph.nodes.values():
        if node.kind in ("Function","Custom"):
            p = node.props
            function_name = p['name'] if node.kind == "Function" else f"_custom_{node.id}"
            expression = formula(p['expression']) if node.kind == "Function" else custom_expression(p)
            if _data:
                lines += [f"def {function_name}(a, b, c):", f"    return _data_formula(lambda a, b, c: {expression}, a, b, c)", ""]
            else:
                lines += [f"def {function_name}(a, b, c):", "    a, b, c = _number(a), _number(b), _number(c)",
                          f"    return {expression}", ""]
    if _library is None:
        for name, body in bodies.items():
            definition = graph.functions[name]
            try:
                compiled = compile_graph(body, trace, library_catalog=library_catalog, data_base=data_base, _function=definition, _library=graph.functions, _scope=name+"/", _units=_units, _data=_data, _serial=_serial)
            except GraphError as exc:
                raise GraphError(f"Function '{name}': {exc}") from None
            lines += [f"def {name}({', '.join(definition['inputs'])}):"]
            lines += ["    "+line if line else "" for line in compiled.code.splitlines()]
            lines += ["    return main()", ""]
    lines += ["def main():"]
    body_depth = 2 if _serial and _function is None else 1
    body_indent = "    " * body_depth
    if body_depth == 2:
        lines.append("    try:")
    for node in graph.nodes.values():
        if node.kind == "Variable":
            lines.append(body_indent + f"{node.props['name']} = {literal(node.props['value'], node.props['type'])!r}")
            if trace:
                lines.append(body_indent + f"_inspect({_scope+node.id!r}, {node.props['name']})")

    def data_input(node: Node, port: str, available: set[str], stack: set[str], depth: int) -> str:
        edge = graph.input_edge(node.id, port)
        if edge:
            return value(edge.source, available, stack, depth, edge.output)
        if node.kind in DATA_KINDS:
            required = node.kind in ("Data Table", "CSV Output") and port == "data"
            required |= node.kind in ("Line Plot", "Scatter Plot") and port == "data" and not (graph.input_edge(node.id, "x") and graph.input_edge(node.id, "y"))
            if required:
                raise GraphError(f"{node.title}: connect the data pin to CSV Input.data or Data Table.data. Plots may instead use both x and y vector pins.", node.id)
            return "None"
        if node.kind == "Library":
            spec = next(port_spec for port_spec in node.props["inputs"] if port_spec["name"] == port)
            default = default_value(spec, node.props["defaults"][port])
            return f"_Quantity({default!r}, {spec['unit']!r})" if spec["type"] == "Quantity" else repr(default)
        if node.kind in ENGINEERING:
            return f"_Quantity({literal(node.props[port])!r}, {ENGINEERING[node.kind][0][port]!r})"
        if node.kind == "Unit Convert":
            return f"_Quantity({literal(node.props['value'])!r}, {node.props['from_unit']!r})"
        kind = "Text" if node.kind in ("Print", "Convert", "Text Join", "Assign", "Serial Send", "Parse Value") else ("Boolean" if node.kind in ("Branch", "Boolean Logic") else "Number")
        return repr(literal(input_default(node,port), kind))

    def value(node_id: str, available: set[str], stack: set[str], depth: int, output=None) -> str:
        node = graph.nodes[node_id]
        used.add(node_id)
        if node_id in stack:
            raise GraphError("Data wires form a cycle.")
        stack = stack | {node_id}
        if node.kind == "Variable":
            return node.props["name"]
        if node.kind == "Function Inputs":
            if _function is None or output not in _function["inputs"]:
                raise GraphError("Function Inputs can only be used inside their function.", node.id)
            return output
        if node.kind in ("Function", "Input", "Random", "Assign", "Custom", "Function Call", "Library", "CSV Input", "Data Table", *SERIAL_EXEC_KINDS):
            if node_id not in available:
                raise GraphError(f"'{node.title}' must run before its result is used. Connect its white execution pins first.", node.id)
            suffix = (f"[{output!r}]" if node.kind in ("Library", "CSV Input", *SERIAL_EXEC_KINDS) else
                      (f"[{node.props['returns'].index(output)}]" if node.kind == "Function Call" else ""))
            return f"_result_{node_id}"+suffix
        if node.kind in ("Math", "Compare", "Convert", "Text Join", "Boolean Logic", "Clamp", "Quantity", "Unit Convert", "Parse Value", *ENGINEERING):
            if node_id in available:
                return f"_value_{node_id}"
            if node.kind in ("Quantity", "Unit Convert"):
                data = data_input(node, "value", available, stack, depth)
                helper = "_make_quantity" if node.kind == "Quantity" else "_convert_unit"
                expression = f"{helper}({data}, {node.props['unit']!r})"
            elif node.kind in ENGINEERING:
                args = ", ".join(data_input(node, pin, available, stack, depth) for pin in ENGINEERING[node.kind][0])
                expression = f"_engineering({node.kind!r}, {args})"
            elif node.kind == "Convert":
                data = data_input(node, "value", available, stack, depth)
                expression = f"_convert({data}, {node.props['type']!r})"
            elif node.kind == "Parse Value":
                data = data_input(node, "line", available, stack, depth)
                expression = f"_serial_parse_value({data}, {node.props['key']!r}, {node.props['separator']!r})"
            elif node.kind == "Clamp":
                args = ", ".join(data_input(node, p, available, stack, depth) for p in ("value", "min", "max"))
                expression = f"_clamp({args})"
            elif node.kind == "Boolean Logic":
                a = data_input(node, "a", available, stack, depth)
                b = "False" if node.props["operator"] == "NOT" else data_input(node, "b", available, stack, depth)
                expression = f"_logic({node.props['operator']!r}, {a}, {b})"
            else:
                a = data_input(node, "a", available, stack, depth)
                b = data_input(node, "b", available, stack, depth)
                if node.kind == "Math":
                    expression = (f"_data_math({node.props['operator']!r}, {a}, {b})" if _data else
                                  f"_number(_number({a}) {node.props['operator']} _number({b}))")
                elif node.kind == "Text Join":
                    expression = f"(str({a}) + {node.props['separator']!r} + str({b}))"
                else:
                    expression = f"({a} {node.props['operator']} {b})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append("    " * depth + f"_value_{node_id} = {expression}")
            available.add(node_id)
            return f"_value_{node_id}"
        raise GraphError(f"'{node.title}' does not supply a value.")

    def emit(node_id: str | None, depth: int, available: set[str]) -> None:
        indent = "    " * depth
        if node_id is None:
            if _function is not None:
                raise GraphError("Every execution path in a function must end at a Return node.")
            lines.append(indent + "pass")
            return
        node = graph.nodes[node_id]
        executed.add(node_id)
        used.add(node_id)
        lines.append(indent + f"# {node.kind}: {node.title}")
        if trace:
            lines.append(indent + f"_trace({_scope+node_id!r})")
        if node.kind == "Main":
            emit(graph.next_node(node_id), depth, available)
        elif node.kind == "Return":
            if _function is None:
                raise GraphError("Return belongs inside a reusable function.", node.id)
            args = [data_input(node, item["name"], available, set(), depth) for item in node.props["values"]]
            lines.append(indent + "return (" + ", ".join(args) + ",)")
        elif node.kind == "Function Call":
            args = [data_input(node, item["name"], available, set(), depth) for item in node.props["inputs"]]
            expression = f"{node.props['function']}({', '.join(args)})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "Library":
            args = [data_input(node, spec["name"], available, set(), depth) for spec in node.props["inputs"]]
            label = f"{node.title} [{node.props['library_id']}@{node.props['library_version']}/{node.props['definition_id']}@{node.props['definition_version']}]"
            expression = f"_library_call({implementation_name(node.props)}, {node.props['inputs']!r}, {node.props['outputs']!r}, {label!r}, [{', '.join(args)}])"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind in SERIAL_EXEC_KINDS:
            p = node.props
            if node.kind == "Serial Connect":
                expression = f"_serial_connect({p['port']!r}, {int(p['baud'])!r}, {float(p['timeout'])!r}, {p['mode']!r})"
            elif node.kind == "Serial Send":
                command = data_input(node, "command", available, set(), depth)
                expression = f"_serial_send({command}, {p['newline']!r})"
            elif node.kind == "Serial Read Line":
                expression = f"_serial_read_line({float(p['timeout'])!r})"
            else:
                expression = "_serial_disconnect()"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "CSV Input":
            p = node.props
            need_x = any(edge.source == node_id and edge.output == "x" for edge in graph.edges)
            need_y = any(edge.source == node_id and edge.output == "y" for edge in graph.edges)
            expression = f"_data_read({p['path']!r}, {p['delimiter']!r}, {p['x_column']!r}, {p['y_column']!r}, {need_x!r}, {need_y!r})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            if trace:
                lines.append(indent + f"_data_present({_scope+node_id!r}, _data_preview(_result_{node_id}['data'], 'CSV Input'))")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "Data Table":
            data = data_input(node, "data", available, set(), depth)
            values = data_input(node, "values", available, set(), depth)
            expression = f"_data_table({_scope+node_id!r}, {data}, {node.props['columns']!r}, {node.props['column_name']!r}, {values})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind in ("Line Plot", "Scatter Plot"):
            data = data_input(node, "data", available, set(), depth)
            x = data_input(node, "x", available, set(), depth)
            y = data_input(node, "y", available, set(), depth)
            p = node.props
            kind = "line" if node.kind == "Line Plot" else "scatter"
            expression = f"_data_plot({_scope+node_id!r}, {kind!r}, {data}, {x}, {y}, {p['x_column']!r}, {p['y_column']!r}, {p['title']!r}, {p['x_label']!r}, {p['y_label']!r})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + expression)
            emit(graph.next_node(node_id), depth, available)
        elif node.kind == "CSV Output":
            data = data_input(node, "data", available, set(), depth)
            p = node.props
            expression = f"_data_write({p['path']!r}, {data}, {p['columns']!r}, {p['delimiter']!r}, {p['mode']!r})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + expression)
            emit(graph.next_node(node_id), depth, available)
        elif node.kind in ("Function","Custom"):
            input_names = ("a","b","c") if node.kind == "Function" else [item["name"] for item in node.props["inputs"]]
            arguments = [data_input(node,p,available,set(),depth) for p in input_names]
            arguments += ["0.0"] * (3-len(arguments))
            function_name = node.props['name'] if node.kind == "Function" else f"_custom_{node.id}"
            expression = f"{'_data_result' if _data else '_number'}({function_name}({', '.join(arguments)}))"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "Print":
            data = data_input(node, "value", available, set(), depth)
            if trace:
                data = f"_node_value({_scope+node_id!r}, lambda: {data})"
            lines.append(indent + f"print({node.props['label']!r}, {data}, sep='')")
            emit(graph.next_node(node_id), depth, available)
        elif node.kind == "Assign":
            data = data_input(node,"value",available,set(),depth)
            if trace:
                data = f"_node_value({_scope+node_id!r}, lambda: {data})"
            lines.append(indent + f"_result_{node_id} = {data}")
            emit(graph.next_node(node_id),depth,available | {node_id})
        elif node.kind == "Input":
            expression = f"_ask({_scope+node_id!r}, {node.props['prompt']!r})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "Random":
            args = ", ".join(data_input(node, p, available, set(), depth) for p in ("min", "max"))
            seed = int(node.props["seed"].strip()) if node.props["seed"].strip() else None
            expression = f"_random_value({node.props['mode']!r}, {args}, {seed!r})"
            if trace:
                expression = f"_node_value({_scope+node_id!r}, lambda: {expression})"
            lines.append(indent + f"_result_{node_id} = {expression}")
            emit(graph.next_node(node_id), depth, available | {node_id})
        elif node.kind == "Branch":
            data = data_input(node, "condition", available, set(), depth)
            if trace:
                data = f"_node_value({_scope+node_id!r}, lambda: {data})"
            lines.append(indent + f"if {data}:")
            emit(graph.next_node(node_id, "true"), depth + 1, available.copy())
            lines.append(indent + "else:")
            emit(graph.next_node(node_id, "false"), depth + 1, available.copy())
        else:
            raise GraphError("Execution wires must connect to executable nodes.")

    emit(main.id, body_depth, set())
    if body_depth == 2:
        lines += ["    finally:", "        _serial_cleanup()"]
    if _function is None:
        lines += ["", "if __name__ == '__main__':", "    main()", ""]
        if data_views and not trace:
            lines += ["    _data_show()", ""]
    warnings = [f"Unused node: {n.title}" for n in graph.nodes.values() if n.id not in used]
    if graph.next_node(main.id) is None:
        warnings.insert(0, "Main is not connected. Connect its white output pin to a Function, Print, or Branch.")
    code = "\n".join(lines)
    try:
        compile(code, "<NodeForge>", "exec")
    except (SyntaxError, RecursionError) as exc:
        raise GraphError(f"Graph is too deeply nested or cannot be compiled: {exc}") from None
    return Compilation(code, warnings, executed)


def demo_graph() -> Graph:
    """The user's Main -> function sketch, with three variables and output."""
    g = Graph()
    main = g.add_node("Main", 60, 60)
    mass = g.add_node("Variable", 60, 210)
    mass.props.update(name="mass_kg", value="2.5")
    accel = g.add_node("Variable", 60, 370)
    accel.props.update(name="acceleration", value="9.81")
    offset = g.add_node("Variable", 350, 410)
    offset.props.update(name="offset_n", value="0")
    fn = g.add_node("Function", 370, 100)
    fn.props.update(name="calculate_force", expression="round(a * b + c, 3)")
    output = g.add_node("Print", 690, 100)
    output.props.update(label="Force (N): ")
    g.connect(main.id, "next", fn.id, "run")
    g.connect(mass.id, "value", fn.id, "a")
    g.connect(accel.id, "value", fn.id, "b")
    g.connect(offset.id, "value", fn.id, "c")
    g.connect(fn.id, "next", output.id, "run")
    g.connect(fn.id, "result", output.id, "value")
    return g


def branch_demo() -> Graph:
    g = Graph()
    main = g.add_node("Main", 40, 60)
    temp = g.add_node("Variable", 40, 230)
    temp.props.update(name="temperature_c", value="85")
    compare = g.add_node("Compare", 330, 260)
    compare.props.update(operator=">=", b="80")
    branch = g.add_node("Branch", 620, 60)
    yes = g.add_node("Print", 930, 60)
    yes.props.update(label="", value="Fan ON")
    no = g.add_node("Print", 930, 290)
    no.props.update(label="", value="Fan OFF")
    g.connect(main.id, "next", branch.id, "run")
    g.connect(temp.id, "value", compare.id, "a")
    g.connect(compare.id, "result", branch.id, "condition")
    g.connect(branch.id, "true", yes.id, "run")
    g.connect(branch.id, "false", no.id, "run")
    return g
