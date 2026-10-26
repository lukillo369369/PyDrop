"""Conservative dimension inference; unknown function arguments defer to runtime."""
import ast

from engineering_units import (ENGINEERING, UnitError, _UNITLESS, _dim_combine,
                               _dim_power, dimension_name, unit_spec)


def validate_dimensions(graph, error_type, *, partial=False, normalize_formula=None):
    """Check all known constraints, without executing user formulas.

    While wiring, missing Math inputs are unknown so both wires can be added
    independently. Compilation checks their actual unitless defaults instead.
    Unknown dimensions are never assumed to be compatible at runtime.
    """
    cache = {}
    visiting = set()

    def same(a, b, context):
        if a is not None and b is not None and a != b:
            raise UnitError(f"Dimension mismatch in {context}: expected {dimension_name(a)}, received {dimension_name(b)}. Connect compatible quantities; unit conversions cannot change dimensions.")

    def expression(tree, args):
        if isinstance(tree, ast.Name):
            return args.get(tree.id, _UNITLESS)
        if isinstance(tree, ast.Constant):
            return _UNITLESS
        if isinstance(tree, ast.UnaryOp):
            dim = expression(tree.operand, args)
            if isinstance(tree.op, ast.Not):
                same(_UNITLESS, dim, "boolean condition")
                return _UNITLESS
            return dim
        if isinstance(tree, ast.BinOp):
            a, b = expression(tree.left, args), expression(tree.right, args)
            if isinstance(tree.op, (ast.Add, ast.Sub, ast.Mod, ast.FloorDiv)):
                same(a, b, "arithmetic")
                return _UNITLESS if isinstance(tree.op, ast.FloorDiv) else (a if a is not None else b)
            if isinstance(tree.op, ast.Pow):
                same(_UNITLESS, b, "exponent")
                try:
                    exponent = ast.literal_eval(tree.right)
                except (ValueError, TypeError):
                    return _UNITLESS if a == _UNITLESS else None
                return _dim_power(a, exponent) if a is not None else None
            if a is None or b is None:
                return None
            return _dim_combine(a, b, -1 if isinstance(tree.op, ast.Div) else 1)
        if isinstance(tree, ast.Compare):
            dims = [expression(item, args) for item in [tree.left, *tree.comparators]]
            for a, b in zip(dims, dims[1:]):
                same(a, b, "comparison")
            return _UNITLESS
        if isinstance(tree, ast.BoolOp):
            for item in tree.values:
                same(_UNITLESS, expression(item, args), "boolean condition")
            return _UNITLESS
        if isinstance(tree, ast.IfExp):
            same(_UNITLESS, expression(tree.test, args), "condition")
            a, b = expression(tree.body, args), expression(tree.orelse, args)
            same(a, b, "conditional results")
            return a if a is not None else b
        if isinstance(tree, ast.Call):
            dims = [expression(item, args) for item in tree.args]
            name = tree.func.id
            if name in ("abs", "round"):
                return dims[0]
            if name in ("min", "max"):
                known = next((d for d in dims if d is not None), None)
                for dim in dims:
                    same(known, dim, name)
                return known
            if name == "sqrt":
                return _dim_power(dims[0], .5) if dims[0] is not None else None
            for dim in dims:
                same(_UNITLESS, dim, name)
            return _UNITLESS
        return None

    def input_dim(node, pin, default=_UNITLESS):
        edge = graph.input_edge(node.id, pin)
        if edge:
            return infer(edge.source, edge.output)
        return None if partial and node.kind in ("Math", "Compare", "Clamp", "Function", "Custom") else default

    def infer(node_id, output="result"):
        key = node_id, output
        if key in cache:
            return cache[key]
        if key in visiting:
            return None  # Graph's structural validation reports cycles.
        visiting.add(key)
        node = graph.nodes[node_id]
        p, kind = node.props, node.kind
        try:
            result = None
            if kind == "Library":
                for port in p["inputs"]:
                    if port["type"] == "Quantity":
                        expected = unit_spec(port["unit"])[1]
                        same(expected, input_dim(node, port["name"], expected), f"{node.title}.{port['name']}")
                spec = next((port for port in p["outputs"] if port["name"] == output), None)
                if spec and spec["type"] == "Quantity":
                    result = unit_spec(spec["unit"])[1]
                elif spec and spec["type"] in ("Number", "Boolean"):
                    result = _UNITLESS
            elif kind == "Quantity":
                same(_UNITLESS, input_dim(node, "value"), "Quantity magnitude")
                result = unit_spec(p["unit"])[1]
            elif kind == "Unit Convert":
                result = unit_spec(p["unit"])[1]
                same(result, input_dim(node, "value", unit_spec(p["from_unit"])[1]), "Unit Convert")
            elif kind in ENGINEERING:
                inputs, unit, _ = ENGINEERING[kind]
                for pin, expected_unit in inputs.items():
                    expected = unit_spec(expected_unit)[1]
                    same(expected, input_dim(node, pin, expected), f"{kind}.{pin} ({expected_unit})")
                result = unit_spec(unit)[1]
            elif kind == "Variable":
                result = _UNITLESS if p["type"] in ("Number", "Boolean") else None
            elif kind in ("Math", "Compare"):
                tree = ast.parse("a " + p["operator"] + " b", mode="eval").body
                if kind == "Math" and p["operator"] == "**" and not graph.input_edge(node.id, "b"):
                    tree.right = ast.Constant(float(p["b"]))
                result = expression(tree, {pin: input_dim(node, pin) for pin in ("a", "b")})
            elif kind in ("Function", "Custom"):
                source = normalize_formula(node) if normalize_formula else p["expression"]
                pins = ("a", "b", "c") if kind == "Function" else [item["name"] for item in p["inputs"]]
                args = {alias: input_dim(node, pin) for alias, pin in zip(("a", "b", "c"), pins)}
                result = expression(ast.parse(source, mode="eval").body, args)
            elif kind == "Assign":
                result = input_dim(node, "value", None)
            elif kind == "Clamp":
                dims = [input_dim(node, pin) for pin in ("value", "min", "max")]
                result = next((d for d in dims if d is not None), None)
                for dim in dims:
                    same(result, dim, "Clamp bounds")
            elif kind == "Random":
                for pin in ("min", "max"):
                    same(_UNITLESS, input_dim(node, pin), "Random bounds (use Quantity afterward)")
                result = _UNITLESS
            elif kind == "Convert":
                if p["type"] != "Text":
                    same(_UNITLESS, input_dim(node, "value", None), "Convert (cannot discard units)")
                    result = _UNITLESS
            elif kind in ("Branch", "Boolean Logic"):
                pins = ("condition",) if kind == "Branch" else (("a",) if p["operator"] == "NOT" else ("a", "b"))
                for pin in pins:
                    same(_UNITLESS, input_dim(node, pin), "condition (use Compare first)")
                result = _UNITLESS
            cache[key] = result
            return result
        except UnitError as exc:
            raise error_type(f"{node.title} ({node.id}): {exc}", node.id) from None
        finally:
            visiting.remove(key)

    for node in graph.nodes.values():
        infer(node.id)
