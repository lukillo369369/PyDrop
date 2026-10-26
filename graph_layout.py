"""Explicit, deterministic graph layout without changing the graph model.

The editor applies the returned positions as one undoable operation. Importing
this module, opening a project, and creating a node never arrange anything.
Only the standard library is used.
"""
from __future__ import annotations

from bisect import bisect_right, insort
from collections import defaultdict, deque
import math
from statistics import median

from graph_core import ports


def _height(node):
    inputs, outputs = ports(node)
    return 54 + max(len(inputs), len(outputs)) * 29 + 48


def _crossings(layers, links):
    """Count crossings between adjacent layers, excluding shared endpoints."""
    ranks = [{vertex: i for i, vertex in enumerate(layer)} for layer in layers]
    total = 0
    for level, segments in enumerate(links):
        by_source = defaultdict(list)
        for source, target in segments:
            by_source[ranks[level][source]].append(ranks[level + 1][target])
        previous = []
        for source in sorted(by_source):
            targets = by_source[source]
            # Insert only after examining the whole fan-out: wires sharing a
            # source (or target) do not cross each other.
            total += sum(len(previous) - bisect_right(previous, target) for target in targets)
            for target in targets:
                insort(previous, target)
    return total


def _arrange_component(graph, ids, edges, width, heights):
    nodes = graph.nodes
    stable = lambda node_id: (nodes[node_id].y, nodes[node_id].x, node_id)
    ordered = sorted(ids, key=stable)
    successors = {node_id: set() for node_id in ids}
    predecessors = {node_id: set() for node_id in ids}
    exec_children = defaultdict(list)
    exec_parents = set()

    # Execution must run left to right even in a draft whose data and execution
    # constraints, considered together, contain a use-before-run cycle.
    execution, data = [], []
    for edge in edges:
        kind = ports(nodes[edge.source])[1].get(edge.output)
        if kind == "exec":
            execution.append(edge)
            exec_children[edge.source].append(edge)
            exec_parents.add(edge.target)
        else:
            data.append(edge)
    for edge in execution:
        successors[edge.source].add(edge.target)
        predecessors[edge.target].add(edge.source)

    def reaches(start, goal):
        pending, visited = [start], set()
        while pending:
            current = pending.pop()
            if current == goal:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(successors[current] - visited)
        return False

    for edge in sorted(data, key=lambda edge: (edge.source, edge.target, edge.output, edge.input)):
        if not reaches(edge.target, edge.source):
            successors[edge.source].add(edge.target)
            predecessors[edge.target].add(edge.source)

    indegree = {node_id: len(predecessors[node_id]) for node_id in ids}
    ready = deque(node_id for node_id in ordered if not indegree[node_id])
    rank = dict.fromkeys(ids, 0)
    visited = set()
    while ready:
        source = ready.popleft()
        visited.add(source)
        for target in sorted(successors[source], key=stable):
            rank[target] = max(rank[target], rank[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    # Valid graphs cannot have execution cycles. Keep this pure helper usable
    # while a caller is constructing a draft rather than recursing forever.
    if len(visited) != len(ids):
        raise ValueError("Automatic layout requires execution wires without cycles.")

    # Give execution leaves separate lanes. Branch port order keeps True above
    # False, and continuing one branch does not collapse it onto the other.
    executable = {node_id for node_id in ids
                  if "exec" in (*ports(nodes[node_id])[0].values(), *ports(nodes[node_id])[1].values())}
    lanes, next_lane = {}, 0

    def lane(node_id):
        nonlocal next_lane
        if node_id in lanes:
            return lanes[node_id]
        output_order = list(ports(nodes[node_id])[1])
        children = sorted(exec_children[node_id], key=lambda edge: (output_order.index(edge.output), stable(edge.target)))
        if children:
            values = [lane(edge.target) for edge in children]
            lanes[node_id] = (min(values) + max(values)) / 2
        else:
            lanes[node_id] = float(next_lane)
            next_lane += 1
        return lanes[node_id]

    roots = sorted(executable - exec_parents, key=lambda node_id: (nodes[node_id].kind != "Main", stable(node_id)))
    for root in roots:
        lane(root)

    layers = [[] for _ in range(max(rank.values()) + 1)]
    actual = {node_id: ("node", node_id) for node_id in ids}
    vertex_lane = {actual[node_id]: value for node_id, value in lanes.items()}
    for node_id in ordered:
        layers[rank[node_id]].append(actual[node_id])
    links = [[] for _ in layers[:-1]]
    incoming, outgoing = defaultdict(list), defaultdict(list)
    # Long wires get invisible ordering vertices in intermediate columns. This
    # lets crossing reduction account for shared values and skipped columns.
    for index, edge in enumerate(edges):
        start, end = rank[edge.source], rank[edge.target]
        if end <= start:
            continue
        source = actual[edge.source]
        is_exec = edge in execution
        for level in range(start + 1, end + 1):
            target = actual[edge.target] if level == end else ("wire", index, level)
            if level != end:
                layers[level].append(target)
                if is_exec:
                    fraction = (level - start) / (end - start)
                    vertex_lane[target] = lanes[edge.source] * (1 - fraction) + lanes[edge.target] * fraction
            links[level - 1].append((source, target))
            outgoing[source].append(target)
            incoming[target].append(source)
            source = target

    def keep_execution_lanes(layer):
        indices = [i for i, vertex in enumerate(layer) if vertex in vertex_lane]
        values = sorted((layer[i] for i in indices), key=lambda vertex: vertex_lane[vertex])
        for i, vertex in zip(indices, values):
            layer[i] = vertex

    for layer in layers:
        keep_execution_lanes(layer)
    best = [layer[:] for layer in layers]
    best_score = _crossings(layers, links)
    # Alternate forward/backward barycentric sweeps, retaining the best complete
    # ordering because a later sweep can improve one side and worsen the other.
    for sweep in range(12):
        forward = sweep % 2 == 0
        levels = range(1, len(layers)) if forward else range(len(layers) - 2, -1, -1)
        neighbors = incoming if forward else outgoing
        for level in levels:
            adjacent = layers[level - 1 if forward else level + 1]
            positions = {vertex: i for i, vertex in enumerate(adjacent)}
            previous = {vertex: i for i, vertex in enumerate(layers[level])}
            scale = max(1, len(adjacent) - 1)

            def barycenter(vertex):
                values = [positions[neighbor] / scale for neighbor in neighbors[vertex] if neighbor in positions]
                return (sum(values) / len(values) if values else
                        previous[vertex] / max(1, len(layers[level]) - 1), previous[vertex])

            layers[level].sort(key=barycenter)
            keep_execution_lanes(layers[level])
        score = _crossings(layers, links)
        if score < best_score:
            best, best_score = [layer[:] for layer in layers], score
    layers = best

    # Data nodes inherit their consumers' lane, so a shared value sits between
    # its consumers rather than being duplicated. Execution lanes remain fixed.
    hints = dict(vertex_lane)
    for layer in reversed(layers):
        for vertex in layer:
            if vertex not in hints:
                values = [hints[target] for target in outgoing[vertex] if target in hints]
                if values:
                    hints[vertex] = sum(values) / len(values)
    for layer in layers:
        for vertex in layer:
            if vertex not in hints:
                values = [hints[source] for source in incoming[vertex] if source in hints]
                hints[vertex] = sum(values) / len(values) if values else 0

    positions = {}
    lane_spacing = max(heights[node_id] for node_id in ids) + 64
    for level, layer in enumerate(layers):
        bottom = -math.inf
        column = []
        for vertex in layer:
            if vertex[0] != "node":
                continue
            node_id = vertex[1]
            preferred = hints[vertex] * lane_spacing - heights[node_id] / 2
            y = max(preferred, bottom + 64)
            positions[node_id] = (level * (width + 110), y)
            bottom = y + heights[node_id]
            column.append((node_id, preferred - y))
        # A column's packing need not push all of its nodes downward. Center its
        # unavoidable displacement to keep long execution chains readable.
        if column:
            shift = median(delta for _, delta in column)
            for node_id, _ in column:
                x, y = positions[node_id]
                positions[node_id] = (x, y + shift)
    left = min(x for x, _ in positions.values())
    top = min(y for _, y in positions.values())
    anchor_x = min(nodes[node_id].x for node_id in ids)
    anchor_y = min(nodes[node_id].y for node_id in ids)
    return {node_id: (x - left + anchor_x, y - top + anchor_y) for node_id, (x, y) in positions.items()}


def layout_positions(graph, *, node_width=270, height_fn=None, selected=None):
    """Return ``{node_id: (x, y)}`` for an explicit layout request.

    All execution wires advance left to right. Data predecessors occupy earlier
    columns wherever this does not contradict execution. Disconnected components
    retain their original top-left anchors when space permits; colliding
    components are moved down. ``selected`` limits work to the induced subgraph,
    leaving other nodes, edges, function graphs, and group metadata untouched.
    """
    if not math.isfinite(node_width) or node_width <= 0:
        raise ValueError("Node width must be a positive finite number.")
    ids = set(graph.nodes) if selected is None else set(selected) & graph.nodes.keys()
    if not ids:
        return {}
    measure = height_fn or _height
    heights = {node_id: float(measure(graph.nodes[node_id])) for node_id in ids}
    if any(not math.isfinite(value) or value <= 0 for value in heights.values()):
        raise ValueError("Node heights must be positive finite numbers.")
    edges = sorted((edge for edge in graph.edges if edge.source in ids and edge.target in ids),
                   key=lambda edge: (edge.source, edge.output, edge.target, edge.input))
    adjacent = {node_id: set() for node_id in ids}
    for edge in edges:
        adjacent[edge.source].add(edge.target)
        adjacent[edge.target].add(edge.source)
    components, pending = [], set(ids)
    while pending:
        first = min(pending)
        component, stack = set(), [first]
        while stack:
            node_id = stack.pop()
            if node_id not in component:
                component.add(node_id)
                stack.extend(adjacent[node_id] - component)
        pending -= component
        components.append(component)
    components.sort(key=lambda component: (min(graph.nodes[node_id].y for node_id in component),
                                            min(graph.nodes[node_id].x for node_id in component), min(component)))
    # Selection-only layout treats the untouched nodes as fixed obstacles.
    result = {}
    occupied = [(node.x, node.y, node.x+node_width, node.y+float(measure(node)))
                for node_id, node in graph.nodes.items() if node_id not in ids]
    for component in components:
        component_edges = [edge for edge in edges if edge.source in component]
        positions = _arrange_component(graph, component, component_edges, node_width, heights)
        left = min(x for x, _ in positions.values())
        right = max(x + node_width for x, _ in positions.values())
        top = min(y for _, y in positions.values())
        bottom = max(y + heights[node_id] for node_id, (_, y) in positions.items())
        shift = 0
        while True:
            collisions = [(x1, y1, x2, y2) for x1, y1, x2, y2 in occupied
                          if left < x2 + 64 and right + 64 > x1 and top + shift < y2 + 90 and bottom + shift + 90 > y1]
            if not collisions:
                break
            shift = max(y2 + 90 - top for _, _, _, y2 in collisions)
        result.update({node_id: (x, y + shift) for node_id, (x, y) in positions.items()})
        occupied.append((left, top + shift, right, bottom + shift))
    return result
