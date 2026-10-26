"""Selection, clipboard, arrangement, and labeled-group tools for the Tk editor."""
import json
import math

from graph_core import GraphError, ports
from graph_editing import (alignment_positions, copy_fragment, create_group,
                           group_bounds, paste_fragment, ungroup)
from graph_layout import layout_positions


def _height(node):
    inputs, outputs = ports(node)
    return 54 + max(len(inputs), len(outputs)) * 29 + 48


class EditorTools:
    def current_selection(self):
        # Keep compatibility with code that assigns the legacy primary ID.
        if self.selected not in self.graph.nodes:
            return set()
        if self.selected not in self.selected_nodes:
            return {self.selected}
        return self.selected_nodes.intersection(self.graph.nodes)

    def set_selection(self, ids, primary=None, group_id=None):
        if not self.apply_properties():
            return False
        chosen = set(ids).intersection(self.graph.nodes)
        self.selected_nodes = chosen
        self.selected = primary if primary in chosen else next((key for key in self.graph.nodes if key in chosen), None)
        self.selected_edge = None
        self.selected_group = group_id if group_id in self.graph.groups else None
        self.show_inspector()
        self.draw()
        return True

    def select_all(self):
        if not self.busy:
            self.set_selection(self.graph.nodes)
            self.status_var.set(f"Selected {len(self.current_selection())} nodes. Shift-click toggles selection.")

    def open_space_picker(self):
        if self.busy or self.pending:
            return
        if self.search_anchor is None:
            x, y = self.pointer_canvas or (self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
            self.search_anchor = self.inverse(x, y)
        self.open_node_picker()

    def begin_selection_drag(self, event):
        if self.selected is None:
            return
        self.drag_node = self.selected
        primary = self.graph.nodes[self.selected]
        self.drag_origin = (event.x, event.y, primary.x, primary.y)
        self.drag_positions = {key: (self.graph.nodes[key].x, self.graph.nodes[key].y) for key in self.current_selection()}
        self.drag_snapshot = self.snapshot()
        self.drag_moved = False

    def canvas_double_click(self, event):
        node_id = self.hit_node(event.x, event.y)
        key = self.hit_group(event.x, event.y) if node_id is None else None
        # Every press can start a drag. Open/collapse only after a stationary
        # release, so a quick second press cannot steal movement from a card.
        self.canvas_press(event)
        if self.drag_node:
            if key is not None:
                self.double_click_action = ("group", key)
            elif node_id is not None and self.graph.nodes[node_id].kind == "Function Call":
                self.double_click_action = ("function", node_id)
        return "break"

    def _visual_edit_finished(self):
        # Position/group edits do not invalidate computed values or run Python.
        self.update_title()
        self.show_inspector()
        self.draw()

    def copy_selected(self):
        if not self.apply_properties():
            return False
        chosen = self.current_selection()
        copyable = {key for key in chosen if self.graph.nodes[key].kind not in ("Main", "Function Inputs")}
        if not copyable:
            self.status_var.set("Select nodes to copy. Main and Function Inputs are unique entry nodes.")
            return False
        try:
            fragment = copy_fragment(self.graph, copyable, library=self.function_library() if self.function_owner else None)
            self.clipboard_payload = json.dumps(fragment, ensure_ascii=True)
            self.root.clipboard_clear()
            self.root.clipboard_append(self.clipboard_payload)
            self.paste_count = 0
            self.copied_ids = copyable
            notice = " Unique entry nodes were excluded." if chosen != copyable else ""
            self.status_var.set(f"Copied {len(copyable)} nodes with their internal wires.{notice}")
            return True
        except (GraphError, self.tk.TclError) as exc:
            self.status_var.set(str(exc))
            return False

    def paste_selected(self):
        if self.busy or not self.apply_properties():
            return
        try:
            try:
                payload = self.root.clipboard_get()
            except self.tk.TclError:
                payload = self.clipboard_payload
            if not payload or len(payload) > 2_000_000:
                raise GraphError("Clipboard does not contain a supported PyDrop node selection (2 MB maximum).")
            fragment = json.loads(payload)
            if self.search_anchor is not None:
                x, y = self.search_anchor
            else:
                point = self.pointer_canvas or (self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
                x, y = self.inverse(*point)
            x, y = x + self.paste_count*32, y + self.paste_count*32
            proposed, ids = paste_fragment(self.graph, fragment, x, y,
                                           library=self.function_library() if self.function_owner else None)
            self.remember()
            self.graph = proposed
            self.selected_nodes = set(ids)
            self.selected, self.selected_edge, self.selected_group = ids[-1], None, None
            self.paste_count += 1
            self.form_dirty = False
            self.refresh()
            self.reveal_nodes(ids)
            self.status_var.set(f"Pasted {len(ids)} nodes. Ctrl+Z undoes the entire paste.")
        except (GraphError, ValueError, TypeError, RecursionError) as exc:
            self.status_var.set("Cannot paste: " + str(exc))

    def cut_selected(self):
        if self.busy:
            return
        if self.copy_selected():
            self.set_selection(self.copied_ids)
            self.delete_selected()

    def duplicate_selection(self):
        if self.busy or not self.apply_properties():
            return
        chosen = {key for key in self.current_selection() if self.graph.nodes[key].kind not in ("Main", "Function Inputs")}
        if not chosen:
            return
        try:
            context = self.function_library() if self.function_owner else None
            fragment = copy_fragment(self.graph, chosen, library=context)
            x, y = min(self.graph.nodes[key].x for key in chosen)+40, min(self.graph.nodes[key].y for key in chosen)+45
            proposed, ids = paste_fragment(self.graph, fragment, x, y, library=context)
            self.remember()
            self.graph = proposed
            self.selected_nodes = set(ids)
            self.selected, self.selected_edge, self.selected_group = ids[-1], None, None
            self.form_dirty = False
            self.refresh()
            self.status_var.set(f"Duplicated {len(ids)} nodes and their internal wires.")
        except GraphError as exc:
            self.status_var.set(str(exc))

    def _apply_positions(self, positions, label):
        checked = {key: (float(x), float(y)) for key, (x, y) in positions.items() if key in self.graph.nodes}
        if any(not math.isfinite(value) or abs(value) > 1e7 for point in checked.values() for value in point):
            raise GraphError("Positions must be finite and within the supported canvas range.")
        if not any((self.graph.nodes[key].x, self.graph.nodes[key].y) != point for key, point in checked.items()):
            self.status_var.set("Positions already match this arrangement.")
            return False
        self.remember()
        for key, (x, y) in checked.items():
            self.graph.nodes[key].x, self.graph.nodes[key].y = x, y
        self._visual_edit_finished()
        self.status_var.set(label + " Ctrl+Z restores the previous positions.")
        return True

    def align_selected(self, mode="left"):
        if self.busy or not self.apply_properties():
            return
        if len(self.current_selection()) < 2:
            self.status_var.set("Select at least two nodes to align, or three to distribute.")
            return
        try:
            positions = alignment_positions(self.graph, self.current_selection(), mode, height_fn=_height)
            self._apply_positions(positions, "Arranged selected nodes.")
        except GraphError as exc:
            self.status_var.set(str(exc))

    def auto_layout(self, selected_only=False):
        if self.busy or not self.apply_properties():
            return
        chosen = self.current_selection() if selected_only else None
        if selected_only and not chosen:
            self.status_var.set("Select nodes before laying out a selection.")
            return
        try:
            positions = layout_positions(self.graph, height_fn=_height, selected=chosen)
            if self._apply_positions(positions, "Applied automatic layout."):
                self.fit_view()
        except GraphError as exc:
            self.status_var.set(str(exc))

    def create_selection_group(self, label=None):
        if self.busy or not self.apply_properties():
            return
        chosen = self.current_selection()
        if not chosen:
            self.status_var.set("Select nodes to place in a labeled group.")
            return
        if label is None:
            from tkinter import simpledialog
            label = simpledialog.askstring("Group selected nodes", "Group label:", initialvalue="Group", parent=self.root)
        if label is None:
            return
        try:
            before = self.snapshot()
            group_id = create_group(self.graph, chosen, label)
            self.remember(before)
            self.selected_group = group_id
            self._visual_edit_finished()
            self.status_var.set("Created group. Click its − button to collapse; drag its label to move every member.")
            return group_id
        except GraphError as exc:
            self.status_var.set(str(exc))

    def _group_choice(self, group_id=None):
        if group_id in self.graph.groups:
            return group_id
        if self.selected_group in self.graph.groups:
            return self.selected_group
        chosen = self.current_selection()
        return next((key for key, group in self.graph.groups.items() if chosen and chosen.issubset(group["nodes"])), None)

    def toggle_group(self, group_id=None):
        if self.busy or not self.apply_properties():
            return
        key = self._group_choice(group_id)
        if key is None:
            self.status_var.set("Select a group label or a node inside a group.")
            return
        self.remember()
        group = self.graph.groups[key]
        group["collapsed"] = not group["collapsed"]
        self.selected_nodes = set(group["nodes"])
        self.selected = group["nodes"][0]
        self.selected_group, self.selected_edge = key, None
        self._visual_edit_finished()
        self.status_var.set("Group collapsed; internal node positions are preserved." if group["collapsed"] else "Group expanded at its original node positions.")

    def rename_group(self, group_id=None, label=None):
        if self.busy or not self.apply_properties():
            return
        key = self._group_choice(group_id)
        if key is None:
            return
        if label is None:
            from tkinter import simpledialog
            label = simpledialog.askstring("Rename group", "Group label:", initialvalue=self.graph.groups[key]["label"], parent=self.root)
        if label is None:
            return
        label = label.strip()
        if not label or len(label) > 120 or any(ord(char) < 32 for char in label):
            self.status_var.set("Use a single-line group label of 1 to 120 characters.")
            return
        if label != self.graph.groups[key]["label"]:
            self.remember()
            self.graph.groups[key]["label"] = label
            self._visual_edit_finished()

    def ungroup_selected(self):
        if self.busy or not self.apply_properties():
            return
        chosen = self.current_selection()
        keys = [key for key, group in self.graph.groups.items() if set(group["nodes"]).issubset(chosen)]
        if not keys:
            key = self._group_choice()
            keys = [key] if key is not None else []
        if keys:
            self.remember()
            for key in keys:
                ungroup(self.graph, key)
            self.selected_group = None
            self._visual_edit_finished()
            self.status_var.set("Removed group labels; member nodes and wires were preserved.")

    def show_selection_inspector(self):
        chosen = self.current_selection()
        group = self.graph.groups.get(self.selected_group)
        if group is None and len(chosen) < 2:
            return False
        for child in self.inspector_content.winfo_children():
            child.destroy()
        self.inspector_canvas.yview_moveto(0)
        self.form_vars, self.form_dirty, self.inspector_node = {}, False, None
        parent = self.inspector_content
        self.label(parent, group["label"] if group else f"{len(chosen)} nodes selected", font=(self.ui_font,17,"bold"),
                   wraplength=240, pady=10).pack(fill="x")
        self.label(parent, f"{len(chosen)} nodes · drag any selected card to move them together.",
                   wraplength=240, justify="left", fg="#94a7c2").pack(fill="x", pady=8)
        if group:
            self.button(parent, "Expand group" if group["collapsed"] else "Collapse group", self.toggle_group).pack(fill="x", pady=4)
            self.button(parent, "Rename group…", self.rename_group).pack(fill="x", pady=4)
            self.button(parent, "Ungroup · Ctrl+Shift+G", self.ungroup_selected).pack(fill="x", pady=4)
        else:
            self.button(parent, "Group selection · Ctrl+G", self.create_selection_group).pack(fill="x", pady=4)
        self.button(parent, "Copy · Ctrl+C", self.copy_selected).pack(fill="x", pady=4)
        self.button(parent, "Align / distribute…", self.alignment_menu).pack(fill="x", pady=4)
        self.button(parent, "Layout selection", lambda: self.auto_layout(True)).pack(fill="x", pady=4)
        self.button(parent, "Delete selection", self.delete_selected, fg="#ff8f9d").pack(fill="x", pady=12)
        return True

    def alignment_menu(self):
        menu = self.tk.Menu(self.root, tearoff=False, bg="#1b2738", fg="#edf3fc")
        for mode, title in (("left","Align left"), ("center_x","Align horizontal centers"), ("right","Align right"),
                            ("top","Align top"), ("center_y","Align vertical centers"), ("bottom","Align bottom"),
                            ("distribute_x","Distribute horizontally"), ("distribute_y","Distribute vertically")):
            menu.add_command(label=title, command=lambda mode=mode: self.align_selected(mode))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def editing_menu(self):
        menu = self.tk.Menu(self.root, tearoff=False, bg="#1b2738", fg="#edf3fc")
        for title, accelerator, callback in (
            ("Add / search nodes…", "Space", self.open_space_picker), ("Select all", "Ctrl+A", self.select_all),
            ("Copy", "Ctrl+C", self.copy_selected), ("Cut", "Ctrl+X", self.cut_selected), ("Paste", "Ctrl+V", self.paste_selected),
            ("Duplicate", "Ctrl+D", self.duplicate_selected), ("Delete", "Delete", self.delete_selected),
            ("Group selection…", "Ctrl+G", self.create_selection_group), ("Ungroup", "Ctrl+Shift+G", self.ungroup_selected),
            ("Collapse / expand group", "Ctrl+Shift+C", self.toggle_group),
            ("Align / distribute…", "", self.alignment_menu),
            ("Auto layout whole graph", "Ctrl+L", self.auto_layout), ("Auto layout selection", "Ctrl+Shift+L", lambda: self.auto_layout(True)),
            ("Keyboard shortcuts…", "F1", self.show_shortcuts)):
            menu.add_command(label=title, accelerator=accelerator, command=callback)
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def show_shortcuts(self):
        from tkinter import messagebox
        messagebox.showinfo("Graph editing shortcuts", "Space: search and add a node at the pointer\n"
            "Drag empty canvas: pan · Shift-drag: box select and add to selection\nShift/Ctrl-click: toggle a node\n"
            "Left-drag a node: move it (or its selection) · Middle/Alt-drag also pans\nWheel: zoom · F: fit · Click minimap: navigate\n\n"
            "Ctrl+A: select all · Ctrl+C/X/V: copy/cut/paste\nCtrl+D: duplicate · Delete: remove selection\n"
            "Ctrl+G: labeled group · Ctrl+Shift+G: ungroup\nCtrl+Shift+C: collapse/expand · Double-click group label: toggle\n"
            "Ctrl+L: auto layout graph · Ctrl+Shift+L: layout selection\nEdit menu: align edges/centers and distribute nodes\n\n"
            "Ctrl+Z / Ctrl+Y: undo / redo\nGraph shortcuts leave Entry, Text, and other text fields to their native editing commands.\n"
            "Layout runs only when requested; loading, grouping, and navigation preserve manual positions.", parent=self.root)

    def prepare_groups(self):
        self.collapsed_members = {node_id: key for key, group in self.graph.groups.items() if group["collapsed"] for node_id in group["nodes"]}
        self.group_ports = {key: {"in": [], "out": []} for key in self.graph.groups}
        for edge in self.graph.edges:
            source, target = self.collapsed_members.get(edge.source), self.collapsed_members.get(edge.target)
            if source == target:
                continue
            for key, direction, endpoint in ((source, "out", (edge.source, "out", edge.output)), (target, "in", (edge.target, "in", edge.input))):
                if key and endpoint not in self.group_ports[key][direction]:
                    self.group_ports[key][direction].append(endpoint)
        self.group_boxes = {}
        self.group_hits = []
        for key, group in self.graph.groups.items():
            box = group_bounds(self.graph, key, height_fn=_height)
            if group["collapsed"]:
                rows = max(len(self.group_ports[key]["in"]), len(self.group_ports[key]["out"]))
                box = box[0], box[1], box[0]+310, box[1]+max(68, 42+rows*18)
            self.group_boxes[key] = box

    def projected_pin(self, node_id, direction, name):
        key = self.collapsed_members.get(node_id)
        endpoint = node_id, direction, name
        if key is None or endpoint not in self.group_ports.get(key, {}).get(direction, []):
            return None
        index = self.group_ports[key][direction].index(endpoint)
        x1, y1, x2, y2 = self.group_boxes[key]
        return (x1 if direction == "in" else x2), y1+42+index*18

    def draw_group_cards(self, collapsed=False):
        c, z = self.canvas, self.zoom
        for key, group in self.graph.groups.items():
            if group["collapsed"] != collapsed:
                continue
            bx1, by1, bx2, by2 = self.group_boxes[key]
            x1, y1 = self.transform(bx1, by1)
            x2, y2 = self.transform(bx2, by2)
            selected = set(group["nodes"]).issubset(self.current_selection())
            color = "#ff8f9d" if self.error_node in group["nodes"] else ("#f5c578" if self.active_node in group["nodes"] else ("#66e0bb" if selected else "#536b8a"))
            self.roundrect(x1,y1,x2,y2,10*z,fill="#142132" if collapsed else "#101a28",outline=color,width=2 if selected else 1)
            c.create_text(x1+14*z,y1+16*z,text="+" if collapsed else "−",fill=color,font=(self.ui_font,max(8,round(13*z)),"bold"))
            title = f"{group['label']} ({len(group['nodes'])})"
            c.create_text(x1+30*z,y1+16*z,text=title,anchor="w",fill="#d0dff2",width=max(30,(bx2-bx1-38)*z),
                          font=(self.ui_font,max(7,round(10*z)),"bold"))
            self.group_hits.append((x1,y1,x2,y2 if collapsed else y1+32*z,key))
            if collapsed:
                for direction in ("in", "out"):
                    for node_id, _, port in self.group_ports[key][direction]:
                        px, py = self.transform(*self.projected_pin(node_id, direction, port))
                        kind = ports(self.graph.nodes[node_id])[0 if direction == "in" else 1][port]
                        r = max(3,4*z)
                        fill = "#edf3fc" if kind == "exec" else "#66e0bb"
                        if kind == "exec":
                            c.create_polygon(px,py-r,px+r,py,px,py+r,px-r,py,fill=fill,outline="")
                        else:
                            c.create_oval(px-r,py-r,px+r,py+r,fill=fill,outline="")
                        label = self.graph.nodes[node_id].title[:12] + "." + port[:10]
                        c.create_text(px+(8*z if direction == "in" else -8*z),py,text=label,anchor="w" if direction == "in" else "e",
                                      fill="#94a7c2",font=(self.ui_font,max(6,round(7*z))))
                        self.port_hits.append((px,py,node_id,direction,port))

    def hit_group(self, x, y):
        return next((key for x1,y1,x2,y2,key in reversed(self.group_hits) if x1 <= x <= x2 and y1 <= y <= y2), None)

    def visible_rectangles(self):
        result = [(key, (node.x,node.y,node.x+270,node.y+_height(node)), node.kind)
                  for key, node in self.graph.nodes.items() if key not in self.collapsed_members]
        result += [(key, self.group_boxes[key], "Group") for key, group in self.graph.groups.items() if group["collapsed"]]
        return result
