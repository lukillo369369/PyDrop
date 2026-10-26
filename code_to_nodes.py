"""Translate short Python scripts into editable node groups without execution."""
from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

from graph_core import Graph, GraphError, identifier, validate_props, ports


@dataclass
class CodeDrop:
    graph: Graph
    primary: str
    added: list[str]
    first_action: str | None = None
    last_action: str | None = None


def looks_like_code(text: str) -> bool:
    text = text.strip()
    return (any(c in text for c in "=()+-*/%<>!\"'[]{}") or text in ("True", "False")
            or (bool(text) and text[0].isdigit()) or text.startswith("not ")
            or " and " in text or " or " in text
            or text.startswith(("import ","from ","for ","while ","if ","def ","class ","return ")))


def translate_code(source: str, original: Graph, x: float, y: float) -> CodeDrop:
    """Return a changed copy. Any parse or validation failure leaves original intact.

    Supports constant/expression assignments, arithmetic/comparisons/boolean expressions,
    print, input, float, str, and random.randint/uniform (including Random(seed)).
    Statements run in order; imports, control flow, and reassignment are excluded.
    """
    if not isinstance(source, str) or not source.strip() or len(source) > 12000:
        raise GraphError("Enter a short script of up to 12,000 characters.")
    try:
        tree = ast.parse(source.strip(), mode="exec")
    except (SyntaxError, ValueError, RecursionError):
        raise GraphError('Finish the Python statement. Try print("Hello") or speed = 10.') from None
    if not 1 <= len(tree.body) <= 50 or sum(1 for _ in ast.walk(tree)) > 600:
        raise GraphError("Use up to 50 statements and 600 syntax elements per import.")
    g = copy.deepcopy(original)
    added = []
    executed = []
    row = 0
    declared_names = {target.id for statement in tree.body if isinstance(statement,ast.Assign)
                      for target in statement.targets if isinstance(target,ast.Name)}

    def new(kind, depth):
        nonlocal row
        node = g.add_node(kind, x-depth*310, y+(row*200 if depth else 0))
        if depth:
            row += 1
        added.append(node.id)
        return node

    def constant(part):
        if isinstance(part, ast.Constant) and type(part.value) in (str, int, float, bool):
            return part.value
        if isinstance(part, ast.UnaryOp) and isinstance(part.op, (ast.USub, ast.UAdd)):
            value = constant(part.operand)
            if type(value) in (int, float):
                return -value if isinstance(part.op, ast.USub) else value
        raise GraphError("Expected a text, number, or True/False constant.")

    def variable(value, depth):
        node = new("Variable", depth)
        node.props.update(type="Boolean" if type(value) is bool else ("Text" if type(value) is str else "Number"), value=str(value))
        if node.props["name"] in declared_names:
            candidate = f"literal_{node.id}"
            while candidate in declared_names or any(n.props.get("name") == candidate for n in g.nodes.values()):
                candidate += "x"
            node.props["name"] = candidate
        return node

    def reference(name):
        matches = [n for n in g.nodes.values() if n.kind in ("Variable", "Function", "Assign") and n.props.get("name") == name]
        if len(matches) != 1:
            raise GraphError(f"Unknown or ambiguous value '{name}'. Add a named Variable first.")
        return matches[0]

    def is_text(part):
        if isinstance(part, ast.Constant):
            return type(part.value) is str
        if isinstance(part, ast.Name):
            node = reference(part.id)
            def text_source(node):
                if node.kind == "Assign":
                    edge = g.input_edge(node.id,"value")
                    return text_source(g.nodes[edge.source]) if edge else True
                return (node.kind == "Variable" and node.props["type"] == "Text") or node.kind in ("Input","Text Join") or (node.kind == "Convert" and node.props["type"] == "Text")
            return text_source(node)
        return (isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
                and part.func.id in ("str", "input")) or (
                    isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add)
                    and is_text(part.left) and is_text(part.right))

    def feed(node, port, part, depth, default_type="Number"):
        try:
            value = constant(part)
        except GraphError:
            value = None
        if value is not None and (default_type == "Text" or
                                 (default_type == "Number" and type(value) in (int, float)) or
                                 (default_type == "Boolean" and type(value) is bool)):
            node.props[port] = str(value)
            return
        source_node = expression(part, depth+1)
        output = "value" if source_node.kind == "Variable" else "result"
        g.connect(source_node.id, output, node.id, port)

    def expression(part, depth=0):
        if depth > 12:
            raise GraphError("Expression is too deeply nested. Add it in smaller pieces.")
        try:
            value = constant(part)
        except GraphError:
            value = None
        if value is not None:
            return variable(value, depth)
        if isinstance(part, ast.Name):
            return reference(part.id)
        if isinstance(part, ast.BinOp):
            operators = {ast.Add:"+", ast.Sub:"-", ast.Mult:"*", ast.Div:"/", ast.FloorDiv:"//", ast.Mod:"%", ast.Pow:"**"}
            op = operators.get(type(part.op))
            if op is None:
                raise GraphError("That arithmetic operator is not supported yet.")
            text = isinstance(part.op, ast.Add) and is_text(part.left) and is_text(part.right)
            node = new("Text Join" if text else "Math", depth)
            if text:
                node.props["separator"] = ""
            else:
                node.props["operator"] = op
            feed(node,"a",part.left,depth,"Text" if text else "Number")
            feed(node,"b",part.right,depth,"Text" if text else "Number")
            return node
        if isinstance(part, ast.Compare) and len(part.ops) == 1:
            op = {ast.Eq:"==", ast.NotEq:"!=", ast.Lt:"<", ast.LtE:"<=", ast.Gt:">", ast.GtE:">="}.get(type(part.ops[0]))
            if op:
                node = new("Compare",depth)
                node.props["operator"] = op
                feed(node,"a",part.left,depth)
                feed(node,"b",part.comparators[0],depth)
                return node
        if isinstance(part, ast.UnaryOp) and isinstance(part.op, ast.Not):
            node = new("Boolean Logic",depth)
            node.props["operator"] = "NOT"
            feed(node,"a",part.operand,depth,"Boolean")
            return node
        if isinstance(part, ast.BoolOp) and len(part.values) >= 2:
            node = new("Boolean Logic",depth)
            node.props["operator"] = "AND" if isinstance(part.op,ast.And) else "OR"
            left = part.values[0] if len(part.values) == 2 else ast.BoolOp(op=part.op,values=part.values[:-1])
            feed(node,"a",left,depth,"Boolean")
            feed(node,"b",part.values[-1],depth,"Boolean")
            return node
        if isinstance(part, ast.Call) and not part.keywords:
            name = part.func.id if isinstance(part.func,ast.Name) else None
            if name == "print" and len(part.args) <= 1:
                node = new("Print",depth)
                node.props.update(label="",value="")
                if part.args:
                    feed(node,"value",part.args[0],depth,"Text")
                executed.append(node)
                return node
            if name == "input" and len(part.args) <= 1:
                node = new("Input",depth)
                prompt = constant(part.args[0]) if part.args else ""
                if type(prompt) is not str:
                    raise GraphError("Input prompts must be text constants, such as input(\"Your name?\").")
                node.props["prompt"] = prompt
                executed.append(node)
                return node
            if name in ("float","str") and len(part.args) == 1:
                node = new("Convert",depth)
                node.props["type"] = "Number" if name == "float" else "Text"
                try:
                    value = constant(part.args[0])
                except GraphError:
                    value = None
                if value is not None and (type(value) is not bool or name == "str"):
                    node.props["value"] = str(value)
                else:
                    # Preserve argument types: float(True) must receive a Boolean.
                    source_node = expression(part.args[0],depth+1)
                    g.connect(source_node.id,"value" if source_node.kind == "Variable" else "result",node.id,"value")
                return node
            if isinstance(part.func,ast.Attribute) and part.func.attr in ("randint","uniform") and len(part.args) == 2:
                owner = part.func.value
                seed = ""
                if isinstance(owner,ast.Call) and isinstance(owner.func,ast.Attribute) and owner.func.attr == "Random" and isinstance(owner.func.value,ast.Name) and owner.func.value.id == "random" and not owner.keywords and len(owner.args) == 1:
                    number = constant(owner.args[0])
                    if type(number) is not int:
                        raise GraphError("Use an integer seed, such as random.Random(42).randint(1, 6).")
                    seed = str(number)
                elif not (isinstance(owner,ast.Name) and owner.id == "random"):
                    raise GraphError("Use random.randint, random.uniform, or random.Random(integer_seed).")
                node = new("Random",depth)
                node.props.update(mode="Integer" if part.func.attr == "randint" else "Decimal",seed=seed)
                feed(node,"min",part.args[0],depth)
                feed(node,"max",part.args[1],depth)
                executed.append(node)
                return node
        raise GraphError("Supported: constant assignments, print/input, arithmetic, comparisons, Boolean logic, float/str, and random.randint/uniform.")

    for statement in tree.body:
        try:
            if isinstance(statement,ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0],ast.Name):
                name = identifier(statement.targets[0].id)
                if any(n.kind in ("Variable","Function","Assign") and n.props.get("name") == name for n in g.nodes.values()):
                    raise GraphError(f"'{name}' already exists. Edit that node or choose another name; reassignment is not supported.")
                try:
                    value = constant(statement.value)
                except GraphError:
                    source_node = expression(statement.value)
                    if source_node.kind in ("Print","Main","Branch"):
                        raise GraphError("This action does not return a value to assign.")
                    primary = new("Assign",0)
                    primary.props["name"] = name
                    g.connect(source_node.id,"value" if source_node.kind == "Variable" else "result",primary.id,"value")
                    executed.append(primary)
                else:
                    primary = variable(value,0)
                    primary.props["name"] = name
            elif isinstance(statement,ast.Expr):
                if isinstance(statement.value,ast.Name):
                    raise GraphError("Use print(value_name) to add an output for an existing value.")
                primary = expression(statement.value)
            else:
                raise GraphError("Use assignments or supported expressions/calls. Imports, loops, and control-flow statements are not supported here.")
        except GraphError as exc:
            raise GraphError(f"Line {statement.lineno}: {exc}") from None
    if not added:
        raise GraphError("That value already exists. Try print(value_name) to add an output node.")
    for node_id in added:
        validate_props(g.nodes[node_id])
    # Nested executed expressions run before their consumer, in argument order.
    for first, second in zip(executed,executed[1:]):
        g.connect(first.id,"next",second.id,"run")
    plan = CodeDrop(g,primary.id,added,executed[0].id if executed else None,executed[-1].id if executed else None)
    layout_drop(plan,x,y)
    return plan


def layout_drop(plan: CodeDrop, x: float, y: float):
    """Arrange the imported dependency graph in columns without card overlap."""
    g = plan.graph
    imported = set(plan.added)
    ranks = {}
    visiting = set()
    def rank(node_id):
        if node_id in ranks:
            return ranks[node_id]
        if node_id in visiting:
            raise GraphError("Execution and value dependencies conflict. Split the script into smaller groups.")
        visiting.add(node_id)
        parents = [e.source for e in g.edges if e.target == node_id and e.source in imported]
        ranks[node_id] = max((rank(parent)+1 for parent in parents),default=0)
        visiting.remove(node_id)
        return ranks[node_id]
    for node_id in plan.added:
        rank(node_id)
    columns = {}
    for node_id in plan.added:
        columns.setdefault(ranks[node_id],[]).append(node_id)
    for column, members in columns.items():
        offset = 0
        positions = {}
        for node_id in members:
            positions[node_id] = offset
            ins,outs = ports(g.nodes[node_id])
            offset += 54+max(len(ins),len(outs))*29+48+60
        anchor = positions.get(plan.primary,0)
        for node_id in members:
            node = g.nodes[node_id]
            node.x = x+(column-ranks[plan.primary])*330
            node.y = y+positions[node_id]-anchor


def connect_drop(plan: CodeDrop, mode="Disconnected", selected=None):
    """Insert a new action chain, preserving the anchor's existing successor."""
    if mode == "Disconnected" or plan.first_action is None:
        return plan
    g = plan.graph
    if mode == "After Main":
        mains = [n for n in g.nodes.values() if n.kind == "Main"]
        if not mains:
            first = g.nodes[plan.first_action]
            main = g.add_node("Main",first.x-330,first.y)
            plan.added.append(main.id)
        else:
            main = mains[0]
        anchor = main.id
    elif mode == "After selected" and selected in g.nodes:
        anchor = selected
    else:
        raise GraphError("Select an existing action to connect after, or choose After Main/Disconnected.")
    if "next" not in ports(g.nodes[anchor])[1]:
        raise GraphError("Select an action with a next execution output. Choose Branch paths manually.")
    successor = g.next_node(anchor)
    g.connect(anchor,"next",plan.first_action,"run")
    if successor is not None:
        g.connect(plan.last_action,"next",successor,"run")
    primary = g.nodes[plan.primary]
    layout_drop(plan,primary.x,primary.y)
    return plan
