"""Data-only graph editing operations shared by the editor and its tests.

Clipboard fragments contain node properties and library references, never
installed Python implementations. Every paste is built and checked on a fresh
Graph, so a malformed clipboard cannot partly modify the user's current draft.
"""
from __future__ import annotations

import copy
import math

from graph_core import (Graph, GraphError, MAX_NODES, identifier, ports,
                        validate_function_library)


FRAGMENT_FORMAT = "pydrop-fragment"
FRAGMENT_VERSION = 1
NAMED_KINDS = ("Variable", "Function", "Assign")


def _selected(graph, node_ids):
    """Keep graph order for deterministic clipboard, grouping, and ties."""
    try:
        requested = set(node_ids)
    except (TypeError, ValueError):
        raise GraphError("Select existing nodes first.") from None
    if not requested or any(not isinstance(key, str) or key not in graph.nodes for key in requested):
        raise GraphError("Select existing nodes first.")
    return [node for key, node in graph.nodes.items() if key in requested]


def _height(node, height_fn):
    value = height_fn(node) if height_fn is not None else 54 + max(map(len, ports(node))) * 29 + 48
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise GraphError("Node heights must be positive finite numbers.")
    return value


def _dimensions(node_width):
    if type(node_width) not in (int, float) or not math.isfinite(node_width) or node_width <= 0:
        raise GraphError("Node widths must be positive finite numbers.")


def _definition_closure(nodes, library):
    """Collect only definitions reachable from copied function calls."""
    needed = {}
    pending = [node.props["function"] for node in nodes if node.kind == "Function Call"]
    while pending:
        name = pending.pop()
        if name in needed:
            continue
        if name not in library:
            raise GraphError(f"Reusable function '{name}' is missing; copy it from its owning project.")
        definition = library[name]
        # Normalize through the project's own loader, including body metadata.
        normalized = Graph.from_dict({"format": "nodeforge", "version": 1, "nodes": [], "edges": [],
                                      "functions": {name: definition}}).functions[name]
        needed[name] = normalized
        body = Graph.from_dict(normalized["graph"], _body=True)
        pending.extend(node.props["function"] for node in body.nodes.values() if node.kind == "Function Call")
    validation = Graph()
    validation.functions = needed
    validate_function_library(validation)
    return needed


def _check_call(node, definitions):
    if node.kind != "Function Call":
        return
    name = node.props["function"]
    definition = definitions.get(name)
    if definition is None:
        raise GraphError(f"Reusable function '{name}' is missing.", node.id)
    if ([item["name"] for item in node.props["inputs"]] != definition["inputs"] or
            node.props["returns"] != definition["returns"]):
        raise GraphError(f"The call to '{name}' has an outdated interface.", node.id)


def copy_fragment(graph, node_ids, *, library=None):
    """Copy selected nodes, internal wires/groups, and reusable-call closure.

    Function-body editors provide their project-level ``library`` explicitly.
    Main and Function Inputs are unique structural nodes and cannot be copied.
    """
    source = Graph.from_dict(graph.to_dict())
    selected = _selected(source, node_ids)
    forbidden = [node.kind for node in selected if node.kind in ("Main", "Function Inputs")]
    if forbidden:
        raise GraphError("Main and Function Inputs are unique structural nodes and cannot be copied. Select other nodes.")
    selected_ids = {node.id for node in selected}
    definitions = _definition_closure(selected, source.functions if library is None else library)
    for node in selected:
        _check_call(node, definitions)
    groups = {}
    for group_id, group in source.groups.items():
        # Keep a label only when its complete group is included in the copy.
        if set(group["nodes"]) <= selected_ids:
            groups[group_id] = copy.deepcopy(group)
    result = {"format": FRAGMENT_FORMAT, "version": FRAGMENT_VERSION,
              "nodes": [node.to_dict() for node in selected],
              "edges": [edge.to_dict() for edge in source.edges
                        if edge.source in selected_ids and edge.target in selected_ids]}
    if groups:
        result["groups"] = groups
    if definitions:
        result["functions"] = definitions
    return result


def _unique_name(name, used):
    if name not in used:
        used.add(name)
        return name
    suffix = 2
    while True:
        ending = f"_{suffix}"
        candidate = name[:48-len(ending)] + ending
        if candidate not in used:
            identifier(candidate)
            used.add(candidate)
            return candidate
        suffix += 1


def paste_fragment(graph, fragment, x, y, *, library=None):
    """Return ``(new_graph, added_ids)`` without changing either argument.

    The selected fragment's top-left node starts at ``x, y``. Local names are
    regenerated when needed. Reusable functions retain their identity; an
    existing same-name definition must match exactly. In function-body context
    (``library`` supplied), functions must already exist in the owning project
    and no nested function definitions are attached to the returned body.
    """
    if not isinstance(fragment, dict) or fragment.get("format") != FRAGMENT_FORMAT or fragment.get("version") != FRAGMENT_VERSION:
        raise GraphError("The clipboard does not contain a supported PyDrop node fragment.")
    if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e7 for value in (x, y)):
        raise GraphError("Paste positions must be finite graph coordinates.")
    payload = copy.deepcopy(fragment)
    payload["format"] = "nodeforge"
    source = Graph.from_dict(payload)
    selected = _selected(source, source.nodes)
    if any(node.kind in ("Main", "Function Inputs") for node in selected):
        raise GraphError("Main and Function Inputs are unique structural nodes and cannot be pasted.")
    # Verify both the incoming closure and its call interfaces before touching a
    # clone. Ignore unused definitions instead of importing an entire library.
    incoming = _definition_closure(selected, source.functions)
    for node in selected:
        _check_call(node, incoming)
    result = Graph.from_dict(graph.to_dict())
    if len(result.nodes) + len(source.nodes) > MAX_NODES:
        raise GraphError(f"This prototype supports up to {MAX_NODES} nodes.")
    available = result.functions if library is None else library
    for name, definition in incoming.items():
        if name in available:
            existing = Graph.from_dict({"format": "nodeforge", "version": 1, "nodes": [], "edges": [],
                                        "functions": {name: available[name]}}).functions[name]
            if existing != definition:
                raise GraphError(f"Reusable function '{name}' conflicts with the existing definition. Rename it in its source project before copying.")
        elif library is not None:
            raise GraphError(f"Reusable function '{name}' is missing from this project. Paste into the project graph first.")
        else:
            # Importing cannot silently shadow a name in any existing scope.
            scopes = [result] + [Graph.from_dict(item["graph"], _body=True) for item in result.functions.values()]
            if any(node.props.get("name") == name for scope in scopes for node in scope.nodes.values() if node.kind in NAMED_KINDS):
                raise GraphError(f"Reusable function '{name}' conflicts with a local name in this project.")
            if any(name in item["inputs"] for item in result.functions.values()):
                raise GraphError(f"Reusable function '{name}' conflicts with a function argument in this project.")
            result.functions[name] = copy.deepcopy(definition)
    if len(result.functions) > 32:
        raise GraphError("A project supports up to 32 reusable functions.")
    used_names = {node.props["name"] for node in result.nodes.values() if node.kind in NAMED_KINDS}
    used_names.update(available)
    for node in result.nodes.values():
        if node.kind == "Function Inputs":
            used_names.update(node.props["names"])
    origin_x, origin_y = min(node.x for node in selected), min(node.y for node in selected)
    mapping, added = {}, []
    for node in selected:
        pasted = result.add_node(node.kind, x + node.x-origin_x, y + node.y-origin_y)
        pasted.props = copy.deepcopy(node.props)
        if pasted.kind in NAMED_KINDS:
            pasted.props["name"] = _unique_name(pasted.props["name"], used_names)
        mapping[node.id] = pasted.id
        added.append(pasted.id)
    # The loader checks the whole final edge set in one pass; incrementally
    # connecting it could reject a temporarily incomplete engineering circuit.
    data = result.to_dict()
    data["edges"].extend({**edge.to_dict(), "source": mapping[edge.source], "target": mapping[edge.target]}
                         for edge in source.edges)
    result = Graph.from_dict(data)
    for group in source.groups.values():
        group_id = create_group(result, [mapping[key] for key in group["nodes"]], group["label"])
        result.groups[group_id]["collapsed"] = group["collapsed"]
    # Do not demand a Main or complete execution graph: drafts remain editable.
    result = Graph.from_dict(result.to_dict())
    return result, added


def alignment_positions(graph, node_ids, mode, node_width=270, height_fn=None):
    """Calculate aligned/distributed positions; never move nodes implicitly."""
    selected = _selected(graph, node_ids)
    _dimensions(node_width)
    heights = {node.id: _height(node, height_fn) for node in selected}
    positions = {node.id: (node.x, node.y) for node in selected}
    if mode in ("left", "right", "center_x"):
        left, right = min(node.x for node in selected), max(node.x+node_width for node in selected)
        target = {"left": left, "right": right-node_width, "center_x": (left+right-node_width)/2}[mode]
        return {node.id: (target, node.y) for node in selected}
    if mode in ("top", "bottom", "center_y"):
        top = min(node.y for node in selected)
        bottom = max(node.y+heights[node.id] for node in selected)
        for node in selected:
            target = {"top": top, "bottom": bottom-heights[node.id],
                      "center_y": (top+bottom-heights[node.id])/2}[mode]
            positions[node.id] = node.x, target
        return positions
    if mode in ("distribute_x", "distribute_y"):
        if len(selected) < 3:
            return positions
        axis = 0 if mode == "distribute_x" else 1
        ordered = sorted(selected, key=lambda node: (node.x if axis == 0 else node.y, int(node.id[1:])))
        sizes = {node.id: node_width if axis == 0 else heights[node.id] for node in ordered}
        first, last = ordered[0], ordered[-1]
        start = first.x if axis == 0 else first.y
        end = (last.x if axis == 0 else last.y)+sizes[last.id]
        gap = (end-start-sum(sizes.values()))/(len(ordered)-1)
        cursor = start
        for node in ordered:
            positions[node.id] = (cursor, node.y) if axis == 0 else (node.x, cursor)
            cursor += sizes[node.id]+gap
        return positions
    raise GraphError(f"Unknown alignment tool: {mode}.")


def create_group(graph, node_ids, label):
    """Add one non-overlapping labeled group, returning its new stable ID."""
    selected = _selected(graph, node_ids)
    graph._validate_groups()
    group_id = f"g{max((int(key[1:]) for key in graph.groups), default=0)+1}"
    group = {"label": label, "nodes": [node.id for node in selected], "collapsed": False}
    graph.groups[group_id] = group
    try:
        graph._validate_groups()
    except GraphError:
        del graph.groups[group_id]
        raise
    return group_id


def ungroup(graph, group_id):
    """Remove a group's visual enclosure while preserving nodes and wires."""
    if group_id not in graph.groups:
        raise GraphError("Select an existing group first.")
    del graph.groups[group_id]


def group_bounds(graph, group_id, node_width=270, height_fn=None, padding=20, header=32):
    """Return expanded world bounds including 20px padding and 32px label."""
    _dimensions(node_width)
    graph._validate_groups()
    if group_id not in graph.groups:
        raise GraphError("Select an existing group first.")
    nodes = [graph.nodes[key] for key in graph.groups[group_id]["nodes"]]
    return (min(node.x for node in nodes)-padding,
            min(node.y for node in nodes)-padding-header,
            max(node.x+node_width for node in nodes)+padding,
            max(node.y+_height(node, height_fn) for node in nodes)+padding)
