"""pydrop 0.1 — a small visual Python editor, using only the standard library."""
from __future__ import annotations

# Standard-library helpers handle persistence, child processes, and worker queues.
import argparse
import copy
import io
import json
import math
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
from pathlib import Path

# These modules supply node definitions, source translators, and platform helpers.
from example_graphs import EXAMPLES
from code_to_nodes import CodeDrop, looks_like_code, translate_code, connect_drop
from drop_language import LANGUAGE_NAME, KEYWORDS as DROP_KEYWORDS, SAMPLE as DROP_SAMPLE, translate_drop
from custom_nodes import load_templates, save_template, validate_template
from engineering_units import ENGINEERING, UNIT_NAMES
from serial_runtime import SERIAL_KINDS, _serial_ports
from app_metadata import APP_NAME, APP_VERSION
from app_runtime import (custom_library_path as default_custom_library_path, project_directory,
                         resource_path, script_command, configure_windows_identity)
from packaging_ui import PackagingUI

# Graph validation and compilation live outside the Tk interface.
from graph_core import (COLORS, DESCRIPTIONS, KINDS, Graph, GraphError, Node,
                        branch_demo, compile_graph, demo_graph, ports, validate_props, input_default)

# Shared theme and graph-space dimensions; drawing scales these with the zoom.
BG = "#0c111b"
PANEL = "#131b29"
CARD = "#1b2738"
LINE = "#2c3c53"
TEXT = "#edf3fc"
MUTED = "#94a7c2"
GREEN = "#66e0bb"
GOLD = "#f5c578"
ERROR = "#ff8f9d"
# Node geometry uses graph coordinates; the canvas applies zoom when drawing.
NODE_W = 270
HEADER_H = 54
ROW_H = 29
# Keep the earlier project extension available when opening and saving graphs.
PROJECT_FILETYPES = [("pydrop graph", "*.drop.json *.nodeforge.json"),
                     ("JSON", "*.json"), ("All files", "*.*")]


def node_height(node):
    # Input and output rows share vertical space, so use the longer side.
    inputs, outputs = ports(node)
    return HEADER_H + max(len(inputs), len(outputs)) * ROW_H + 48


def serial_run_allowance(graph, library):
    """Budget bounded I/O, including repeated calls to reusable functions."""
    function_allowances = {}
    def allowance(current):
        total = 0
        for node in current.nodes.values():
            # Add time in seconds for each serial operation and its overhead.
            if node.kind == "Serial Connect":
                total += 4
            elif node.kind == "Serial Send":
                total += 2
            elif node.kind == "Serial Read Line":
                total += float(node.props["timeout"]) + 1
            elif node.kind == "Function Call":
                name = node.props["function"]
                # Calculate a body's budget once, but charge it for every call.
                if name not in function_allowances:
                    function_allowances[name] = allowance(Graph.from_dict(library[name]["graph"], _body=True))
                total += function_allowances[name]
        return total
    return allowance(graph)


class Editor(PackagingUI):
    # Each window owns its view and edit history. Function windows keep a draft
    # graph and refer back to the project owner for shared function definitions.
    def __init__(self, root, graph=None, path=None, custom_library_path=None, function_owner=None, function_name=None):
        import tkinter as tk
        from tkinter import ttk
        import tkinter.font as tkfont
        self.tkfont = tkfont
        self.title_fonts = {}
        # Prefer native-looking fonts, falling back to Tk's installed defaults.
        families = {f.lower(): f for f in tkfont.families(root)}
        self.ui_font = next((families[f.lower()] for f in ("Segoe UI", "Helvetica Neue", "DejaVu Sans", "Helvetica") if f.lower() in families), tkfont.nametofont("TkDefaultFont").actual("family"))
        self.code_font = next((families[f.lower()] for f in ("Consolas", "Menlo", "DejaVu Sans Mono", "Courier") if f.lower() in families), tkfont.nametofont("TkFixedFont").actual("family"))
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.function_owner = function_owner
        self.function_name = function_name
        self.function_editor = None
        self.graph = graph if graph is not None else demo_graph()
        self.path = Path(path) if path else None
        # Selection, wiring, and dragging are transient canvas interaction state.
        self.selected = None
        self.selected_edge = None
        self.pending = None
        self.drag_node = None
        self.drag_origin = None
        self.drag_snapshot = None
        self.pan_origin = None
        self.zoom = 1.0
        self.offset = [40.0, 45.0]
        self.port_hits = []
        self.node_hits = []
        self.edge_items = {}
        # Serialized graph snapshots support both undo and unsaved-change checks.
        self.undo_stack, self.redo_stack = [], []
        self.saved_state = self.snapshot()
        self.form_vars = {}
        self.form_dirty = False
        self.inspector_node = None
        self.busy = False
        self.stop_requested = False
        self.active_node = None
        self.error_node = None
        # A serial identifies each run so delayed callbacks can reject old work.
        self.run_serial = 0
        self.process = None
        # Worker threads enqueue results; Tk widgets are updated by poll_run.
        self.run_queue = queue.Queue()
        self.input_requests = queue.Queue()
        self.pending_input = None
        self.execution_timeout = 5
        self.closed = False
        # Runtime results are separate from the graph saved in project files.
        self.run_values = {}
        self.data_previews = {}
        self.data_windows = []
        # CSV headers and serial discovery arrive asynchronously through queues.
        self.column_cache = {}
        self.column_queue = queue.Queue()
        self.column_widgets = []
        self.serial_events = queue.Queue()
        self.serial_port_queue = queue.Queue()
        self.serial_ports = ["SIMULATED"]
        self.serial_scanning = False
        self.serial_connected = False
        self.serial_port_widget = None
        # Retain auxiliary windows so reopening a tool can reuse its current form.
        self.script_window = None
        self.creator_window = None
        self.custom_library_path = Path(custom_library_path) if custom_library_path is not None else default_custom_library_path()
        # Keep the editor usable if the custom library cannot be read. Retain
        # the error so creating a node cannot overwrite that unreadable library.
        library_error = None
        try:
            self.custom_templates = load_templates(self.custom_library_path)
        except (GraphError,OSError) as exc:
            self.custom_templates = {}
            library_error = str(exc)
        self.custom_library_error = library_error
        self.tooltip_timer = None
        self.tooltip_window = None
        self.node_picker = None
        self.search_anchor = None
        self.status_var = tk.StringVar(value="Ready. Drag a node from the palette or run the example.")
        self.serial_status_var = tk.StringVar(value="Serial: disconnected")
        self.graph_status = tk.StringVar()
        self.zoom_var = tk.StringVar(value="100%")
        self.project_var = tk.StringVar(value="Force calculation")
        self.hint_var = tk.StringVar(value="Click an output pin, then an input pin to connect.")
        self.node_value_var = tk.StringVar(value="Run the graph to inspect this node's value.")
        self.root.title(f"{APP_NAME} {APP_VERSION} | Visual Python")
        try:
            # Windows uses an ICO; other Tk platforms accept a PNG PhotoImage.
            if sys.platform == "win32":
                self.root.iconbitmap(str(resource_path("assets/pydrop.ico")))
            else:
                self.app_icon = tk.PhotoImage(file=str(resource_path("assets/pydrop.png")))
                self.root.iconphoto(True, self.app_icon)
        except (tk.TclError, OSError):
            # Missing optional artwork must not prevent the editor from opening.
            pass
        self.root.geometry("1480x900")
        self.root.minsize(1050, 700)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", (self.ui_font, 10))
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LINE)
        # The clam theme lets ttk controls use the editor's custom dark colors.
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=CARD, background=LINE, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)])
        style.configure("TScrollbar", background=LINE, troughcolor=PANEL, bordercolor=PANEL,
                        arrowcolor=MUTED, lightcolor=PANEL, darkcolor=PANEL)
        self.build_ui()
        self.bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # Fit after Tk has measured the canvas, then poll workers on the UI loop.
        self.root.after(100, self.fit_view)
        self.root.after(50, self.poll_run)
        self.refresh()
        if library_error:
            self.status_var.set("Custom node library needs attention: "+library_error)

    def label(self, parent, text="", **kw):
        # Inherit the parent's background while allowing per-label overrides.
        defaults = dict(bg=parent.cget("bg"), fg=TEXT, anchor="w")
        defaults.update(kw)
        return self.tk.Label(parent, text=text, **defaults)

    def button(self, parent, text, command, accent=False, **kw):
        # Centralize button styling, hover feedback, and common shortcut hints.
        defaults = dict(bg=GREEN if accent else CARD, fg=BG if accent else TEXT,
                        activebackground="#78ddba" if accent else LINE,
                        activeforeground=BG if accent else TEXT, relief="flat", bd=0,
                        padx=13, pady=7, cursor="hand2", font=(self.ui_font, 10, "bold"),
                        highlightthickness=1, highlightbackground="#3c796e" if accent else LINE,
                        highlightcolor=GREEN)
        defaults.update(kw)
        button = self.tk.Button(parent, text=text, command=command, **defaults)
        def enter(event):
            button.hover_base = button.cget("bg")
            if str(button.cget("state")) != "disabled":
                button.configure(bg="#8aecd0" if accent else "#293b53")
        button.bind("<Enter>", enter)
        button.bind("<Leave>", lambda e: button.configure(bg=getattr(button, "hover_base", defaults["bg"])))
        tips = {"New": "Create a graph · Ctrl+N", "Open": "Open a saved graph · Ctrl+O",
                "Save": "Save your editable graph · Ctrl+S", "Save As": "Save under a new name · Ctrl+Shift+S",
                "Export .py": "Export standalone Python · Ctrl+E",
                "Undo": "Undo the last graph change · Ctrl+Z", "Redo": "Restore an undone change · Ctrl+Y",
                "Fit graph": "Fit every node into view · F", "−": "Zoom out", "+": "Zoom in",
                "100%": "Reset to actual size", "▶  Run graph": "Run generated Python · F5"}
        if text in tips:
            self.attach_tooltip(button, tips[text])
        return button

    def hide_tooltip(self, event=None):
        # Cancel delayed creation as well as removing an already visible tip.
        if self.tooltip_timer is not None:
            self.root.after_cancel(self.tooltip_timer)
            self.tooltip_timer = None
        if self.tooltip_window is not None:
            self.tooltip_window.destroy()
            self.tooltip_window = None

    def attach_tooltip(self, widget, text):
        # Delay tooltips until the pointer rests; additive bindings preserve
        # the widget's existing hover and click behavior.
        def show():
            self.tooltip_timer = None
            if self.closed or not widget.winfo_exists():
                return
            tip = self.tk.Toplevel(self.root)
            self.tooltip_window = tip
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            self.tk.Label(tip, text=text, bg="#263951", fg=TEXT, padx=10, pady=7,
                          wraplength=260, justify="left", font=(self.ui_font, 9)).pack()
            tip.update_idletasks()
            # Measure before placing the tip, then keep it within the screen.
            x = min(widget.winfo_rootx(), self.root.winfo_screenwidth()-tip.winfo_reqwidth()-8)
            y = widget.winfo_rooty()+widget.winfo_height()+7
            if y+tip.winfo_reqheight() > self.root.winfo_screenheight():
                y = widget.winfo_rooty()-tip.winfo_reqheight()-7
            tip.geometry(f"+{max(0,x)}+{max(0,y)}")

        def schedule(event):
            self.hide_tooltip()
            self.tooltip_timer = self.root.after(550, show)

        widget.bind("<Enter>", schedule, add="+")
        widget.bind("<Leave>", self.hide_tooltip, add="+")
        widget.bind("<ButtonPress>", self.hide_tooltip, add="+")
        widget.bind("<Destroy>", self.hide_tooltip, add="+")

    def build_ui(self):
        # Assemble the toolbar, palette, inspector, canvas, and code/output pane.
        tk, ttk = self.tk, self.ttk
        toolbar = tk.Frame(self.root, bg=PANEL, height=68)
        toolbar.pack(fill="x")
        self.label(toolbar, "p", bg=GREEN, fg=BG, font=(self.ui_font, 20, "bold"), padx=13, pady=3).pack(side="left", padx=(18, 10), pady=13)
        brand = tk.Frame(toolbar, bg=PANEL)
        brand.pack(side="left", padx=(0, 26))
        self.label(brand, APP_NAME, font=(self.ui_font, 16, "bold")).pack(anchor="w")
        self.label(brand, "BUILD WITH FLOW  /  " + APP_VERSION, fg=MUTED, font=(self.ui_font, 8)).pack(anchor="w")
        # Function windows apply a draft to their owner instead of opening files.
        for label, command in (("New", self.new_project), ("Open", self.open_project), ("Save", self.save_project), ("Save As", self.save_project_as), ("Export .py", self.export_python)):
            if self.function_owner and label in ("New", "Open", "Save As"):
                continue
            if self.function_owner and label == "Save":
                label = "Apply function"
            self.button(toolbar, label, command).pack(side="left", padx=3)
        self.run_button = self.button(toolbar, "▶  Run graph", self.run_graph, accent=True)
        self.run_button.pack(side="right", padx=(8, 18))
        self.stop_button = self.button(toolbar, "Stop", self.stop_run, state="disabled", padx=10)
        self.stop_button.pack(side="right", padx=3)
        if not self.function_owner:
            self.button(toolbar, "Examples ▾", self.examples_menu).pack(side="right", padx=4)
        self.button(toolbar, "Help", self.show_help).pack(side="right", padx=4)
        self.create_node_button = self.button(toolbar,"Create node",self.open_node_creator,padx=10)
        self.create_node_button.pack(side="left",padx=(10,3))

        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")
        status = tk.Frame(self.root, bg=PANEL)
        status.pack(side="bottom", fill="x")
        self.label(status, textvariable=self.status_var, fg=MUTED, padx=16, pady=7, font=(self.ui_font, 9)).pack(side="left", fill="x", expand=True)
        self.label(status, "F5  Run   ·   Ctrl+S  Save", fg=MUTED, padx=14, font=(self.ui_font, 9)).pack(side="right")
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        self.palette = tk.Frame(body, bg=PANEL, width=180)
        self.palette.pack(side="left", fill="y")
        self.palette.pack_propagate(False)
        # Embed a frame in a canvas to make the palette vertically scrollable.
        palette_canvas = tk.Canvas(self.palette, bg=PANEL, bd=0, highlightthickness=0)
        palette_scroll = ttk.Scrollbar(self.palette, orient="vertical", command=palette_canvas.yview)
        palette_scroll.pack(side="right", fill="y")
        palette_canvas.pack(fill="both", expand=True)
        palette_canvas.configure(yscrollcommand=palette_scroll.set)
        palette_body = tk.Frame(palette_canvas, bg=PANEL)
        palette_window = palette_canvas.create_window(0,0,window=palette_body,anchor="nw")
        palette_body.bind("<Configure>", lambda e: palette_canvas.configure(scrollregion=palette_canvas.bbox("all")))
        palette_canvas.bind("<Configure>", lambda e: palette_canvas.itemconfigure(palette_window,width=e.width))
        self.label(palette_body, "NODE PALETTE", fg=MUTED, font=(self.ui_font, 9, "bold")).pack(anchor="w", padx=12, pady=(20, 4))
        self.label(palette_body, "Drag onto the canvas", fg=MUTED, font=(self.ui_font, 9)).pack(anchor="w", padx=12, pady=(0, 16))
        function_kinds = ("Function Call", "Function Inputs", "Return") if self.function_owner else ("Function Call",)
        for group, kinds in (("EXECUTION", ("Main", "Input", "Random", "Function", "Assign", "Print")), ("ARDUINO / SERIAL", SERIAL_KINDS), ("DATA", ("CSV Input", "Data Table", "Line Plot", "Scatter Plot", "CSV Output")), ("FUNCTION GRAPHS", function_kinds), ("VALUES", ("Variable", "Math", "Clamp", "Convert", "Text Join")), ("ENGINEERING", ("Quantity", "Unit Convert", *ENGINEERING)), ("DECISIONS", ("Compare", "Boolean Logic", "Branch"))):
            self.label(palette_body, group, fg=MUTED, font=(self.ui_font, 8, "bold")).pack(anchor="w", padx=12, pady=(13, 6))
            for kind in kinds:
                item = tk.Frame(palette_body, bg=CARD, cursor="hand2", height=36)
                item.pack(fill="x", padx=12, pady=3)
                item.pack_propagate(False)
                stripe = tk.Frame(item, bg=COLORS[kind], width=4)
                stripe.pack(side="left", fill="y")
                title = self.label(item, kind, font=(self.ui_font, 10, "bold"), padx=11)
                title.pack(side="left", fill="y")
                plus = self.label(item, "+", fg=COLORS[kind], padx=12, font=(self.ui_font, 14))
                plus.pack(side="right", fill="y")
                for widget in (item, stripe, title, plus):
                    # Bind every child so wheel and drag work across the whole card.
                    widget.bind("<MouseWheel>", lambda e: palette_canvas.yview_scroll(-1 if e.delta>0 else 1,"units"))
                    widget.bind("<Button-4>", lambda e: palette_canvas.yview_scroll(-1,"units"))
                    widget.bind("<Button-5>", lambda e: palette_canvas.yview_scroll(1,"units"))
                    self.attach_tooltip(widget, DESCRIPTIONS[kind])
                    # Capture this iteration's kind rather than the loop's last one.
                    widget.bind("<ButtonPress-1>", lambda e, k=kind: self.palette_start(e, k))
                    widget.bind("<B1-Motion>", self.palette_motion)
                    widget.bind("<ButtonRelease-1>", self.palette_release)
        self.custom_palette = tk.Frame(palette_body,bg=PANEL)
        self.custom_palette.pack(fill="x")
        self.refresh_custom_palette()
        guide = tk.Frame(palette_body, bg=PANEL)
        guide.pack(side="bottom", fill="x", padx=17, pady=20)
        self.label(guide, "WIRES", fg=MUTED, font=(self.ui_font, 8, "bold")).pack(anchor="w", pady=(0, 8))
        self.label(guide, "◇  Execution order", fg=TEXT, font=(self.ui_font, 9)).pack(anchor="w", pady=2)
        self.label(guide, "●  Values / data", fg=GREEN, font=(self.ui_font, 9)).pack(anchor="w", pady=2)
        self.label(guide, "Right-click for actions.\nDrag empty space to pan.\nScroll to zoom.", fg=MUTED, justify="left", font=(self.ui_font, 9)).pack(anchor="w", pady=(14, 0))

        # The inspector has its own scroll area for nodes with many properties.
        self.inspector = tk.Frame(body, bg=PANEL, width=300)
        self.inspector.pack(side="right", fill="y")
        self.inspector.pack_propagate(False)
        self.label(self.inspector, "INSPECTOR", fg=MUTED, font=(self.ui_font, 9, "bold")).pack(anchor="w", padx=18, pady=(20, 10))
        inspector_scroll = ttk.Scrollbar(self.inspector, orient="vertical")
        inspector_scroll.pack(side="right", fill="y")
        self.inspector_canvas = tk.Canvas(self.inspector, bg=PANEL, bd=0, highlightthickness=0,
                                         yscrollcommand=inspector_scroll.set)
        self.inspector_canvas.pack(fill="both", expand=True)
        inspector_scroll.configure(command=self.inspector_canvas.yview)
        self.inspector_content = tk.Frame(self.inspector_canvas, bg=PANEL)
        self.inspector_window = self.inspector_canvas.create_window(18, 0, anchor="nw", window=self.inspector_content)
        self.inspector_content.bind("<Configure>", lambda e: self.inspector_canvas.configure(scrollregion=self.inspector_canvas.bbox("all")))
        self.inspector_canvas.bind("<Configure>", lambda e: self.inspector_canvas.itemconfigure(self.inspector_window, width=max(100, e.width-36)))
        self.inspector_canvas.bind("<MouseWheel>", lambda e: self.inspector_canvas.yview_scroll(-1 if e.delta>0 else 1,"units"))
        self.inspector_canvas.bind("<Button-4>", lambda e: self.inspector_canvas.yview_scroll(-1,"units"))
        self.inspector_canvas.bind("<Button-5>", lambda e: self.inspector_canvas.yview_scroll(1,"units"))

        center = tk.Frame(body, bg=BG)
        center.pack(fill="both", expand=True)
        graphbar = tk.Frame(center, bg=BG, height=54)
        graphbar.pack(fill="x")
        self.label(graphbar, textvariable=self.project_var, font=(self.ui_font, 11, "bold"), padx=18, pady=14).pack(side="left")
        self.button(graphbar, "Fit graph", self.fit_view, padx=10, pady=4).pack(side="right", padx=14)
        self.button(graphbar, "Undo", self.undo, padx=10, pady=3).pack(side="right", padx=(4, 15))
        self.button(graphbar, "Redo", self.redo, padx=10, pady=3).pack(side="right", padx=4)
        # Let users resize the graph area relative to generated code and output.
        self.split = tk.PanedWindow(center, orient="vertical", sashwidth=6, bg=LINE, bd=0,
                                   sashrelief="flat", opaqueresize=True)
        self.split.pack(fill="both", expand=True)
        graph_area = tk.Frame(self.split, bg=BG)
        code_bar = tk.Frame(graph_area, bg=PANEL, padx=10, pady=7)
        code_bar.pack(fill="x")
        self.label(code_bar,"Type Python or a node name",fg=MUTED,font=(self.ui_font,9)).pack(side="left",padx=(0,10))
        self.code_entry_var = tk.StringVar()
        self.code_entry = tk.Entry(code_bar,textvariable=self.code_entry_var,bg=CARD,fg=TEXT,
                                   insertbackground=GREEN,relief="flat",font=(self.code_font,11),
                                   highlightthickness=1,highlightbackground=LINE,highlightcolor=GREEN)
        self.code_entry.pack(side="left",fill="x",expand=True,ipady=6)
        self.code_entry.bind("<Return>",self.submit_code_bar)
        self.button(code_bar,"Add ↵",self.submit_code_bar,accent=True,padx=12,pady=5).pack(side="left",padx=(8,0))
        self.attach_tooltip(self.code_entry,'Type print("Hello"), speed = 10, or Math, then Enter. Tab or B from the workspace focuses this field; Ctrl+Space also works.')
        # This mode controls execution wiring for nodes added from text.
        code_options = tk.Frame(graph_area,bg=PANEL,padx=10,pady=5)
        code_options.pack(fill="x")
        self.label(code_options,"Connect actions",fg=MUTED,font=(self.ui_font,9)).pack(side="left",padx=(0,8))
        self.code_connect_var = tk.StringVar(value="Disconnected")
        self.ttk.Combobox(code_options,textvariable=self.code_connect_var,
                          values=["Disconnected","After Main","After selected"],state="readonly",width=17).pack(side="left")
        self.button(code_options,"Script…",self.open_script_editor,padx=12,pady=3).pack(side="right")
        self.button(code_options,"Functions ▾",self.functions_menu,padx=10,pady=3).pack(side="right",padx=4)
        if self.function_owner:
            self.button(code_options,"Back to project",self.close,padx=10,pady=3).pack(side="right",padx=4)
        self.canvas = tk.Canvas(graph_area, bg=BG, highlightthickness=0, bd=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        # Overlay navigation controls without moving the graph's canvas origin.
        self.view_controls = tk.Frame(self.canvas, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.view_controls.place(relx=0, rely=1, x=14, y=-14, anchor="sw")
        self.button(self.view_controls, "−", lambda: self.zoom_center(1/1.15), padx=9, pady=5).pack(side="left")
        zoom_button = self.button(self.view_controls, "100%", self.reset_zoom, padx=9, pady=5)
        zoom_button.configure(textvariable=self.zoom_var)
        zoom_button.pack(side="left", padx=1)
        self.button(self.view_controls, "+", lambda: self.zoom_center(1.15), padx=9, pady=5).pack(side="left")
        self.minimap = tk.Canvas(self.canvas, width=164, height=110, bg=PANEL,
                                 highlightthickness=1, highlightbackground=LINE, cursor="crosshair")
        self.minimap.place(relx=1, rely=1, x=-14, y=-14, anchor="se")
        self.minimap.bind("<Button-1>", self.minimap_navigate)
        self.minimap.bind("<B1-Motion>", self.minimap_navigate)
        self.attach_tooltip(self.minimap, "Graph overview · Click or drag to move the viewport")
        helpbar = tk.Frame(graph_area, bg=BG)
        helpbar.pack(fill="x")
        self.label(helpbar, textvariable=self.hint_var, fg=MUTED, font=(self.ui_font, 9), padx=14, pady=7).pack(side="left")
        self.split.add(graph_area, minsize=240, height=500, stretch="always")

        lower = tk.Frame(self.split, bg=PANEL)
        self.serial_status_label = self.label(lower, textvariable=self.serial_status_var, fg=MUTED,
                                               padx=12, pady=5, font=(self.ui_font, 9), wraplength=800)
        self.serial_status_label.pack(fill="x")
        tabs = tk.Frame(lower, bg=PANEL)
        tabs.pack(fill="x")
        self.python_tab = self.button(tabs, "GENERATED PYTHON", lambda: self.set_tab("code"), pady=8, font=(self.ui_font, 9, "bold"))
        self.python_tab.pack(side="left")
        self.output_tab = self.button(tabs, "OUTPUT", lambda: self.set_tab("output"), pady=8, font=(self.ui_font, 9, "bold"))
        self.output_tab.pack(side="left", padx=2)
        self.button(tabs, "Build EXE", self.open_executable_builder, pady=6).pack(side="left", padx=6)
        self.label(tabs, textvariable=self.graph_status, fg=MUTED, font=(self.ui_font, 8), padx=10).pack(side="right")
        self.code_frame = tk.Frame(lower, bg=PANEL)
        self.output_frame = tk.Frame(lower, bg=PANEL)
        # The input panel is shown only while the child process awaits an answer.
        self.input_panel = tk.Frame(self.output_frame, bg=PANEL, padx=12, pady=8)
        self.input_prompt = tk.StringVar()
        self.label(self.input_panel, textvariable=self.input_prompt, wraplength=450).pack(fill="x", pady=(0,6))
        input_row = tk.Frame(self.input_panel, bg=PANEL)
        input_row.pack(fill="x")
        self.input_entry = tk.Entry(input_row, bg=CARD, fg=TEXT, insertbackground=GREEN, relief="flat", font=(self.ui_font,11))
        self.input_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.input_entry.bind("<Return>", lambda e: self.submit_input())
        self.button(input_row,"Send",self.submit_input,accent=True).pack(side="left",padx=6)
        self.button(input_row,"Cancel",lambda:self.submit_input(cancel=True)).pack(side="left")
        self.code_text = self.text_area(self.code_frame)
        self.output_text = self.text_area(self.output_frame)
        # Token tags color generated Python; runtime failures use a separate tag.
        self.code_text.tag_configure("keyword", foreground="#c6a0f6")
        self.code_text.tag_configure("string", foreground="#86d6b4")
        self.code_text.tag_configure("number", foreground=GOLD)
        self.code_text.tag_configure("comment", foreground="#70829c")
        self.output_text.tag_configure("error", foreground=ERROR)
        self.set_text(self.output_text, "Run the graph to see its output here.\n\nThe example calculates force from mass, acceleration, and an offset.")
        self.set_tab("code")
        self.split.add(lower, minsize=145, height=220, stretch="never")
        # Route canvas gestures to handlers that share selection and drag state.
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.canvas_press)
        self.canvas.bind("<Double-Button-1>", self.open_call_event)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)
        self.canvas.bind("<Motion>", self.canvas_motion)
        self.canvas.bind("<KeyPress>", self.canvas_type_node)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("hover"))
        self.canvas.bind("<ButtonPress-2>", self.pan_start)
        self.canvas.bind("<B2-Motion>", self.pan_move)
        self.canvas.bind("<ButtonRelease-2>", lambda e: setattr(self, "pan_origin", None))
        self.canvas.bind("<Button-3>", self.context_menu)
        # Tk reports wheel movement differently on Windows/macOS and X11.
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.1))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_at(e.x, e.y, 1 / 1.1))

    def text_area(self, parent):
        # Use the same read-only, scrollable text presentation in both tabs.
        area = self.tk.Text(parent, bg="#121923", fg="#c4d0e0", insertbackground=TEXT,
                            selectbackground=LINE, wrap="none", font=(self.code_font, 10), bd=0,
                            padx=16, pady=13, undo=False, height=8)
        vertical = self.ttk.Scrollbar(parent, orient="vertical", command=area.yview)
        horizontal = self.ttk.Scrollbar(parent, orient="horizontal", command=area.xview)
        area.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        area.pack(fill="both", expand=True)
        area.configure(state="disabled")
        return area

    def set_text(self, widget, text, tag=None):
        # Temporarily unlock display-only text widgets while replacing content.
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text, tag or ())
        widget.configure(state="disabled")

    def set_tab(self, name):
        # Swap the lower pane's visible frame and update its active-tab styling.
        self.code_frame.pack_forget()
        self.output_frame.pack_forget()
        (self.code_frame if name == "code" else self.output_frame).pack(fill="both", expand=True)
        self.python_tab.configure(bg=LINE if name == "code" else PANEL, fg=TEXT if name == "code" else MUTED)
        self.output_tab.configure(bg=LINE if name == "output" else PANEL, fg=TEXT if name == "output" else MUTED)
        self.python_tab.hover_base = LINE if name == "code" else PANEL
        self.output_tab.hover_base = LINE if name == "output" else PANEL

    def bind_keys(self):
        # Graph-edit shortcuts go through graph_key to respect text-entry focus.
        for sequence, fn in (("<Control-s>", self.save_project), ("<Control-o>", self.open_project),
                             ("<Control-Shift-S>", self.save_project_as),
                             ("<Control-n>", self.new_project), ("<F5>", self.run_graph),
                             ("<Control-e>", self.export_python)):
            self.root.bind(sequence, lambda e, callback=fn: (callback(), "break")[1])
        for sequence, fn in (("<Control-z>", self.undo), ("<Control-y>", self.redo),
                             ("<Delete>", self.delete_selected), ("<Control-d>", self.duplicate_selected),
                             ("<Key-f>", self.fit_view)):
            self.root.bind(sequence, lambda e, callback=fn: self.graph_key(callback))
        self.root.bind("<Escape>", lambda e: self.cancel_wire())
        self.root.bind("<Control-space>",lambda e:(self.code_entry.focus_set(),"break")[1])
        for sequence in ("<Tab>","<Key-b>","<Key-B>"):
            self.canvas.bind(sequence,lambda e:self.focus_node_entry())
            self.root.bind(sequence,lambda e:self.graph_key(self.focus_node_entry))
        self.root.bind("<ButtonPress-1>", self.dismiss_node_picker, add="+")

    def focus_node_entry(self):
        # End popup interactions before handing typing back to the fixed code bar.
        self.close_node_picker()
        self.hide_tooltip()
        self.code_entry.focus_set()
        self.code_entry.icursor("end")
        return "break"

    def dismiss_node_picker(self, event):
        # Walking the parent chain distinguishes clicks inside the popup.
        widget = event.widget
        while widget is not None:
            if widget == self.node_picker:
                return
            widget = getattr(widget, "master", None)
        self.close_node_picker()

    def close_node_picker(self):
        # Reset the reference as well as the widget so future searches can reopen.
        if self.node_picker is not None:
            self.node_picker.destroy()
            self.node_picker = None

    def canvas_type_node(self, event):
        # Typing creates nodes only after an empty-canvas click sets an anchor.
        if self.busy or self.pending or self.drag_node or self.search_anchor is None:
            return
        # Let shortcuts such as Ctrl+S reach the existing root bindings.
        if event.state & (0x4 | 0x8 | 0x20000):
            return
        if event.char and event.char.isprintable() and not event.char.isspace():
            self.open_node_picker(event.char)
            return "break"

    def submit_code_bar(self, event=None):
        # Reuse the picker to resolve either a node name or a Python statement.
        if self.busy:
            self.status_var.set("Wait for the current run to finish before adding nodes.")
            return "break"
        text = self.code_entry_var.get().strip()
        if not text:
            self.code_entry.focus_set()
            return "break"
        # With no canvas insertion point, place the new group at the view center.
        if self.search_anchor is None:
            self.search_anchor = self.inverse(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2)
        self.open_node_picker(text)
        if self.picker_matches:
            self.accept_node_picker()
            if self.node_picker is None:
                self.code_entry_var.set("")
        return "break"

    def open_node_picker(self, query=""):
        # Keep the popup inside the canvas, even when its anchor is near an edge.
        if self.busy or self.search_anchor is None:
            return
        self.close_node_picker()
        self.hide_tooltip()
        panel = self.tk.Frame(self.canvas, bg=PANEL, highlightthickness=1, highlightbackground=GREEN)
        self.node_picker = panel
        x, y = self.transform(*self.search_anchor)
        width = min(330, max(180, self.canvas.winfo_width()-20))
        height = min(282, max(150, self.canvas.winfo_height()-20))
        panel.place(x=max(10,min(x,self.canvas.winfo_width()-width-10)),
                    y=max(10,min(y,self.canvas.winfo_height()-height-10)), width=width, height=height)
        self.label(panel, "ADD NODE / PYTHON", fg=GREEN, font=(self.ui_font,9,"bold"), padx=12, pady=9).pack(fill="x")
        self.picker_query = self.tk.StringVar(value=query)
        self.picker_entry = self.tk.Entry(panel, textvariable=self.picker_query, bg=CARD, fg=TEXT,
                                         insertbackground=GREEN, relief="flat", font=(self.ui_font,11))
        self.picker_entry.pack(fill="x", padx=12, ipady=6)
        self.picker_message = self.tk.StringVar(value="↑ ↓ Choose    Enter Add    Esc Cancel")
        self.label(panel, textvariable=self.picker_message, fg=MUTED, wraplength=width-24,
                   font=(self.ui_font,8), padx=12, pady=7).pack(side="bottom", fill="x")
        self.picker_list = self.tk.Listbox(panel, bg=PANEL, fg=TEXT, selectbackground=LINE,
                                         selectforeground=GREEN, activestyle="none", exportselection=False,
                                         relief="flat", highlightthickness=0, font=(self.ui_font,11))
        self.picker_list.pack(fill="both", expand=True, padx=12, pady=(8,0))
        # Refresh results on every edit; entry and list share navigation keys.
        self.picker_query.trace_add("write", lambda *args: self.filter_node_picker())
        for widget in (self.picker_entry, self.picker_list):
            widget.bind("<Return>", self.accept_node_picker)
            widget.bind("<Escape>", lambda e: self.cancel_node_picker())
            widget.bind("<Down>", lambda e: self.move_node_picker(1))
            widget.bind("<Up>", lambda e: self.move_node_picker(-1))
        self.picker_list.bind("<ButtonRelease-1>", self.accept_node_picker)
        self.filter_node_picker()
        self.picker_entry.focus_set()
        self.picker_entry.icursor("end")

    def filter_node_picker(self):
        # Recognized names take precedence over Python syntax, including the
        # apostrophe in Ohm's Law and punctuation in custom template names.
        self.picker_code = False
        text = self.picker_query.get().strip()
        node_name_match = any(name.casefold().startswith(text.casefold()) for name in (*KINDS, *self.custom_templates))
        if looks_like_code(text) and not node_name_match:
            self.picker_list.delete(0,"end")
            try:
                # Translation here supplies a preview; accepting rebuilds the plan.
                plan = translate_code(text,self.graph,*self.search_anchor)
            except GraphError as exc:
                self.picker_matches = []
                self.picker_list.insert("end","Finish or revise your Python")
                self.picker_message.set(str(exc))
            else:
                kind = plan.graph.nodes[plan.primary].kind
                self.picker_code = True
                self.picker_matches = [kind]
                self.picker_list.insert("end",f"{kind} · from Python")
                self.picker_list.selection_set(0)
                self.picker_list.activate(0)
                self.picker_message.set(f"Enter adds {len(plan.added)} node(s). Esc cancels. Existing values are wired automatically.")
            return
        self.picker_message.set('Node name or Python: print("Hello"), speed = 10\n↑ ↓ Choose    Enter Add    Esc Cancel')
        query = self.picker_query.get().strip().lower()
        aliases = {"Main":"start entry", "Variable":"constant number text boolean value",
                   "Function":"formula calculate", "Math":"arithmetic add subtract multiply divide",
                   "Compare":"comparison equals greater less", "Branch":"if else decision", "Print":"output console display",
                   "Convert":"conversion cast number text boolean", "Text Join":"string concatenate combine", "Input":"ask question prompt read",
                   "Boolean Logic":"and or not logical condition", "Clamp":"limit bounds minimum maximum range",
                   "Random":"dice rng integer decimal seed generate", "Assign":"assignment named capture value",
                   "Quantity":"units length mass time force voltage current resistance engineering",
                   "Unit Convert":"units conversion meters feet kilograms pounds engineering",
                   "Force":"physics mass acceleration newtons", "Torque":"force distance lever moment",
                   "Ohm's Law":"voltage current resistance electrical", "Electrical Power":"voltage current watts electrical"}
        choices = [kind for kind in KINDS if kind != "Custom" and
                   (self.function_owner or kind not in ("Function Inputs", "Return"))] + list(self.custom_templates)
        # Match every query word against names and aliases; offer Main only once.
        self.picker_matches = [kind for kind in choices
                               if not (kind == "Main" and any(n.kind == "Main" for n in self.graph.nodes.values()))
                               and all(word in f"{kind} {aliases.get(kind,self.custom_templates.get(kind,{}).get('description',''))}".lower() for word in query.split())]
        # Prefer names beginning with the query over matches found through aliases.
        self.picker_matches.sort(key=lambda kind: not kind.lower().startswith(query))
        self.picker_list.delete(0, "end")
        for kind in self.picker_matches:
            self.picker_list.insert("end", kind)
        if self.picker_matches:
            self.picker_list.selection_set(0)
            self.picker_list.activate(0)
        else:
            self.picker_list.insert("end", "No matching nodes")

    def move_node_picker(self, step):
        # Wrap keyboard selection at either end of the filtered result list.
        if self.picker_matches:
            selection = self.picker_list.curselection()
            index = ((selection[0] if selection else 0)+step) % len(self.picker_matches)
            self.picker_list.selection_clear(0, "end")
            self.picker_list.selection_set(index)
            self.picker_list.activate(index)
            self.picker_list.see(index)
        return "break"

    def accept_node_picker(self, event=None):
        # Translation and automatic wiring are prepared before committing a
        # graph replacement, leaving the live graph intact on validation errors.
        selection = self.picker_list.curselection()
        if self.picker_matches and selection and self.apply_properties():
            if self.picker_code:
                try:
                    plan = translate_code(self.picker_query.get(),self.graph,*self.search_anchor)
                    connect_drop(plan,self.code_connect_var.get(),self.selected)
                except GraphError as exc:
                    self.picker_message.set(str(exc))
                    return "break"
                self.commit_code_drop(plan)
                self.canvas.focus_set()
                return "break"
            kind = self.picker_matches[selection[0]]
            x, y = self.search_anchor
            if self.code_connect_var.get() != "Disconnected":
                try:
                    # Node-name additions use the same staged wiring as source imports.
                    graph = copy.deepcopy(self.graph)
                    node = self.make_palette_node(graph,kind,x,y)
                    executable = "run" in ports(node)[0]
                    plan = CodeDrop(graph,node.id,[node.id],node.id if executable else None,node.id if executable else None)
                    connect_drop(plan,self.code_connect_var.get(),self.selected)
                except GraphError as exc:
                    self.picker_message.set(str(exc))
                    return "break"
                self.commit_code_drop(plan)
                self.canvas.focus_set()
                return "break"
            self.close_node_picker()
            self.add_node(kind, x, y)
            self.search_anchor = None
            self.canvas.focus_set()
        return "break"

    def cancel_node_picker(self):
        # Consume Escape so closing search does not trigger another key handler.
        self.close_node_picker()
        self.canvas.focus_set()
        return "break"

    def commit_code_drop(self, plan):
        # Treat the entire translated node group as a single undoable change.
        self.remember()
        self.graph = plan.graph
        self.selected,self.selected_edge = plan.primary,None
        self.form_dirty = False
        self.refresh()
        self.reveal_nodes(plan.added)
        self.status_var.set(f"Added {len(plan.added)} editable nodes. The whole import is one undo step.")

    def reveal_nodes(self, node_ids):
        # Reframe only when the new group extends beyond the current viewport.
        nodes = [self.graph.nodes[node_id] for node_id in node_ids if node_id in self.graph.nodes]
        if not nodes:
            return
        x1,y1 = min(n.x for n in nodes)-35,min(n.y for n in nodes)-35
        x2,y2 = max(n.x+NODE_W for n in nodes)+35,max(n.y+node_height(n) for n in nodes)+35
        width,height = max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())
        left,top = self.transform(x1,y1)
        right,bottom = self.transform(x2,y2)
        if left < 0 or top < 0 or right > width or bottom > height:
            self.zoom = min(1.15,max(.01,min(width/(x2-x1),height/(y2-y1))))
            self.offset = [(width-(x2-x1)*self.zoom)/2-x1*self.zoom,
                           (height-(y2-y1)*self.zoom)/2-y1*self.zoom]
            self.draw()

    def open_script_editor(self):
        # Keep one script window and invalidate its preview when inputs change.
        if self.busy or not self.apply_properties():
            return
        if self.script_window is not None and self.script_window.winfo_exists():
            self.script_window.lift()
            self.script_text.focus_set()
            return
        self.close_node_picker()
        self.script_selected = self.selected
        # Keep the insertion point fixed while the script window is being edited.
        self.script_position = self.search_anchor or self.inverse(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2)
        window = self.tk.Toplevel(self.root)
        self.script_window = window
        window.title("Script to nodes — pydrop")
        window.geometry("720x650")
        window.minsize(560,500)
        window.configure(bg=PANEL)
        def close_script():
            window.destroy()
            self.script_window = None
        window.protocol("WM_DELETE_WINDOW",close_script)
        self.label(window,"Script → editable nodes",font=(self.ui_font,16,"bold"),padx=16,pady=12).pack(fill="x")
        self.label(window,"Enter a short script. Enter starts a new line; Ctrl+Enter previews it.",fg=MUTED,padx=16).pack(fill="x")
        languages = self.tk.Frame(window,bg=PANEL,padx=16,pady=8)
        languages.pack(fill="x")
        self.label(languages,"Language",fg=MUTED).pack(side="left",padx=(0,8))
        self.script_language_var = self.tk.StringVar(value="Python")
        language_choice = self.ttk.Combobox(languages,textvariable=self.script_language_var,
                                           values=["Python",LANGUAGE_NAME],state="readonly",width=14)
        language_choice.pack(side="left")
        self.button(languages,"Load example",self.load_language_example,padx=10,pady=3).pack(side="right")
        self.label(languages,f"{LANGUAGE_NAME}: {DROP_KEYWORDS['define']} … {DROP_KEYWORDS['bind']} …   {DROP_KEYWORDS['input']} … {DROP_KEYWORDS['destination']} …   {DROP_KEYWORDS['output']} …",fg=MUTED,font=(self.ui_font,8)).pack(side="left",padx=10)
        actions = self.tk.Frame(window,bg=PANEL,padx=16,pady=10)
        actions.pack(side="bottom",fill="x")
        self.script_note = self.tk.StringVar(value="Preview your script before adding its node group.")
        self.label(window,textvariable=self.script_note,fg=MUTED,wraplength=660,padx=16,pady=8).pack(side="bottom",fill="x")
        self.script_preview = self.tk.Canvas(window,bg=BG,height=150,bd=0,highlightthickness=0)
        self.script_preview.pack(side="bottom",fill="x",padx=16,pady=8)
        # Only offer the selected node as the default if it has a next-action port.
        self.script_connect_var = self.tk.StringVar(value="After selected" if self.script_selected and "next" in ports(self.graph.nodes[self.script_selected])[1] else "After Main")
        self.label(actions,"Connect",fg=MUTED).pack(side="left",padx=(0,8))
        mode = self.ttk.Combobox(actions,textvariable=self.script_connect_var,
                                 values=["Disconnected","After Main","After selected"],state="readonly",width=17)
        mode.pack(side="left")
        self.script_import_button = self.button(actions,"Add group",self.import_script,accent=True)
        self.script_import_button.pack(side="right")
        self.script_import_button.configure(state="disabled")
        self.button(actions,"Preview",self.preview_script).pack(side="right",padx=8)
        text_frame = self.tk.Frame(window,bg=PANEL)
        text_frame.pack(fill="both",expand=True,padx=16,pady=12)
        self.script_text = self.tk.Text(text_frame,bg=BG,fg=TEXT,insertbackground=GREEN,
                                        selectbackground=LINE,font=(self.code_font,11),wrap="none",undo=True,padx=10,pady=10)
        sy = self.ttk.Scrollbar(text_frame,orient="vertical",command=self.script_text.yview)
        sx = self.ttk.Scrollbar(text_frame,orient="horizontal",command=self.script_text.xview)
        self.script_text.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        sy.pack(side="right",fill="y")
        sx.pack(side="bottom",fill="x")
        self.script_text.pack(fill="both",expand=True)
        initial = self.code_entry_var.get().strip()
        if not looks_like_code(initial):
            initial = 'price = 12.5\nquantity = 3\ntotal = price * quantity\nname = input("Your name? ")\nprint("Hello " + name)\nprint(total)'
        self.script_text.insert("1.0",initial)
        self.script_text.edit_modified(False)
        def invalidate(event=None):
            self.script_import_button.configure(state="disabled")
            self.script_note.set("Source or connection changed. Preview again before adding.")
        def changed(event):
            if self.script_text.edit_modified():
                # Clear Tk's modified flag so the next edit produces another event.
                self.script_text.edit_modified(False)
                invalidate()
        self.script_text.bind("<<Modified>>",changed)
        mode.bind("<<ComboboxSelected>>",invalidate)
        self.script_language_var.trace_add("write",lambda *args:invalidate())
        self.script_text.bind("<Control-Return>",lambda e:(self.preview_script(),"break")[1])
        window.bind("<Escape>",lambda e:close_script())
        self.script_text.focus_set()

    def prepare_script(self):
        # Translate and wire a proposed graph without committing it. Connected
        # actions must also compile before the preview can be accepted.
        translator = translate_drop if self.script_language_var.get() == LANGUAGE_NAME else translate_code
        plan = translator(self.script_text.get("1.0","end-1c"),self.graph,*self.script_position)
        connect_drop(plan,self.script_connect_var.get(),self.selected)
        if plan.first_action and self.script_connect_var.get() != "Disconnected":
            self.compile_project(graph=plan.graph)
        return plan

    def load_language_example(self):
        # Switching example source requires a fresh preview before importing.
        source = DROP_SAMPLE if self.script_language_var.get() == LANGUAGE_NAME else 'price = 12.5\nquantity = 3\ntotal = price * quantity\nname = input("Your name? ")\nprint("Hello " + name)\nprint(total)'
        self.script_text.delete("1.0","end")
        self.script_text.insert("1.0",source)
        self.script_import_button.configure(state="disabled")
        self.script_note.set("Example loaded. Preview it before adding the group.")

    def preview_script(self):
        # Show only the imported group, scaled to fit the small preview canvas.
        try:
            plan = self.prepare_script()
        except GraphError as exc:
            self.script_note.set(str(exc))
            self.script_import_button.configure(state="disabled")
            self.script_preview.delete("all")
            return
        self.script_import_button.configure(state="normal")
        self.script_note.set(f"{len(plan.added)} nodes · {self.script_connect_var.get()} · Existing downstream actions are preserved · One undo step")
        canvas = self.script_preview
        canvas.delete("all")
        nodes = [plan.graph.nodes[node_id] for node_id in plan.added]
        x1,y1 = min(n.x for n in nodes),min(n.y for n in nodes)
        x2,y2 = max(n.x+NODE_W for n in nodes),max(n.y+node_height(n) for n in nodes)
        width,height = max(500,canvas.winfo_width()),150
        scale = min((width-24)/max(1,x2-x1),(height-24)/max(1,y2-y1))
        ox,oy = (width-(x2-x1)*scale)/2,12
        # Exclude existing graph wires; this miniature depicts only the added group.
        imported = set(plan.added)
        for edge in plan.graph.edges:
            if edge.source in imported and edge.target in imported:
                a,b = plan.graph.nodes[edge.source],plan.graph.nodes[edge.target]
                canvas.create_line(ox+(a.x+NODE_W-x1)*scale,oy+(a.y+node_height(a)/2-y1)*scale,
                                   ox+(b.x-x1)*scale,oy+(b.y+node_height(b)/2-y1)*scale,fill=LINE)
        for n in nodes:
            px,py = ox+(n.x-x1)*scale,oy+(n.y-y1)*scale
            canvas.create_rectangle(px,py,px+NODE_W*scale,py+node_height(n)*scale,fill=CARD,outline=COLORS[n.kind])
            canvas.create_text(px+4,py+4,text=n.title[:22],anchor="nw",fill=TEXT,font=(self.ui_font,7))

    def import_script(self):
        # Rebuild the plan from current inputs rather than trusting an old preview.
        if self.busy or not self.apply_properties():
            return
        try:
            plan = self.prepare_script()
        except GraphError as exc:
            self.script_note.set(str(exc))
            self.script_import_button.configure(state="disabled")
            return
        self.commit_code_drop(plan)
        self.code_entry_var.set("")
        self.script_window.destroy()
        self.script_window = None
        self.canvas.focus_set()

    def graph_key(self, callback):
        # Leave typing, deletion, and text undo to the focused input widget.
        widget = self.root.focus_get()
        if widget and widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text"):
            return
        callback()
        return "break"

    def function_library(self):
        # Nested function editors share the top-level project's definitions.
        owner = self.function_owner or self
        return owner.graph.functions

    def execution_project(self, graph=None):
        # Run function drafts in a copy of the full project so callers and other
        # definitions remain available without applying the draft to the owner.
        graph = self.graph if graph is None else graph
        if not self.function_owner:
            return graph
        project = copy.deepcopy(self.function_owner.graph)
        project.functions[self.function_name]["graph"] = graph.to_dict()
        return project

    def compile_project(self, trace=False, graph=None):
        # Traced runs resolve data files relative to the owning project.
        return compile_graph(self.execution_project(graph), trace=trace,
                             data_base=str(self.data_directory()) if trace else None)

    def data_directory(self):
        # Function windows use the project file's directory, not their draft.
        owner = self.function_owner or self
        return owner.path.resolve().parent if owner.path else project_directory()

    def csv_path(self, text):
        # Expand home-directory shortcuts while anchoring relative CSV paths.
        if "\0" in text:
            raise ValueError("CSV paths cannot contain a NUL character.")
        target = Path(text).expanduser()
        return target if target.is_absolute() else self.data_directory() / target

    def visible_runtime_id(self, node_id):
        # Strip this function's trace prefix; keep other project IDs distinct
        # from the local node IDs used by its canvas and inspector.
        if self.function_owner:
            prefix = self.function_name + "/"
            return node_id[len(prefix):] if node_id.startswith(prefix) else "project/" + node_id
        return node_id

    def functions_menu(self):
        # Capture each function name in its callback to avoid late-bound loops.
        menu = self.tk.Menu(self.root, tearoff=False, bg=CARD, fg=TEXT, activebackground=LINE)
        if not self.function_owner:
            menu.add_command(label="New function…", command=self.new_function_dialog)
        for name in self.function_library():
            menu.add_command(label="Add call: " + name, command=lambda name=name: self.add_function_call(name))
            menu.add_command(label="Open graph: " + name, command=lambda name=name: self.open_function(name))
        if self.function_owner:
            menu.add_separator()
            menu.add_command(label="Add Return", command=lambda: self.add_node("Return", *self.inverse(self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def new_function_dialog(self):
        # Collect the function signature before opening its editable graph.
        if self.busy or not self.apply_properties():
            return
        window = self.tk.Toplevel(self.root)
        window.title("New reusable function")
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        body = self.tk.Frame(window, bg=PANEL, padx=22, pady=20)
        body.pack(fill="both", expand=True)
        self.label(body, "Create a function graph", font=(self.ui_font, 16, "bold")).pack(anchor="w", pady=(0, 12))
        variables = {}
        for key, title, default in (("name", "Python function name", "calculate_total"),
                                    ("inputs", "Named inputs (comma separated, up to 8)", "price, quantity"),
                                    ("returns", "Named return values (comma separated, 1–8)", "total")):
            self.label(body, title, fg=MUTED).pack(anchor="w", pady=(9, 4))
            variables[key] = self.tk.StringVar(value=default)
            self.tk.Entry(body, textvariable=variables[key], width=50, bg=CARD, fg=TEXT,
                          insertbackground=TEXT, font=(self.code_font, 11)).pack(fill="x", ipady=6)
        note = self.tk.StringVar(value="Wire Function Inputs into calculations, then wire results into Return.")
        self.label(body, textvariable=note, wraplength=470, fg=MUTED, justify="left").pack(fill="x", pady=15)
        def create():
            # Ignore empty comma-separated entries before validating the signature.
            split = lambda key: [part.strip() for part in variables[key].get().split(",") if part.strip()]
            try:
                name = variables["name"].get().strip()
                self.create_reusable_function(name, split("inputs"), split("returns"), open_editor=False)
            except GraphError as exc:
                note.set(str(exc))
                return
            window.destroy()
            self.open_function(name)
        self.button(body, "Create & open graph", create, accent=True).pack(side="right")
        self.button(body, "Cancel", window.destroy).pack(side="left")

    def create_reusable_function(self, name, inputs, returns, open_editor=True):
        # Record history only after the graph accepts the new definition.
        if self.busy or self.function_owner or not self.apply_properties():
            return
        before = self.snapshot()
        self.graph.add_function(name, inputs, returns)
        self.remember(before)
        self.refresh()
        if open_editor:
            self.open_function(name)

    def add_function_call(self, name):
        # Prepare the call and its execution connections on a graph copy.
        if self.busy or not self.apply_properties():
            return
        from tkinter import messagebox
        try:
            graph = copy.deepcopy(self.graph)
            x, y = self.search_anchor or self.inverse(self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
            node = graph.add_call(name, x, y, self.function_library())
            plan = CodeDrop(graph, node.id, [node.id], node.id, node.id)
            connect_drop(plan, self.code_connect_var.get(), self.selected)
        except GraphError as exc:
            messagebox.showerror("Cannot add call", str(exc), parent=self.root)
            return
        self.commit_code_drop(plan)

    def open_call_event(self, event):
        # Double-clicking a call opens the definition it references.
        node_id = self.hit_node(event.x, event.y)
        node = self.graph.nodes.get(node_id)
        if node and node.kind == "Function Call":
            self.open_function(node.props["function"])
            return "break"

    def open_function(self, name):
        # Edit a separate draft in a modal child window; Apply copies it back.
        if self.busy or not self.apply_properties():
            return
        if self.function_editor and not self.function_editor.closed:
            # Keep one active child draft per window rather than opening duplicates.
            self.function_editor.root.lift()
            return
        definition = self.function_library().get(name)
        if definition is None:
            self.status_var.set("That reusable function is missing.")
            return
        window = self.tk.Toplevel(self.root)
        owner = self.function_owner or self
        editor = Editor(window, Graph.from_dict(definition["graph"]), custom_library_path=self.custom_library_path,
                        function_owner=owner, function_name=name)
        # The project owner supplies definitions; the immediate parent restores
        # window state when a nested editor closes.
        editor.function_parent = self
        self.function_editor = editor
        window.transient(self.root)
        window.grab_set()
        editor.status_var.set("Editing " + name + ". Apply function updates this project; save the project afterward. F5 runs the project with this draft.")

    def snapshot(self):
        # Stable key ordering makes equivalent graph states compare equally.
        return json.dumps(self.graph.to_dict(), sort_keys=True)

    def remember(self, before=None):
        # Keep bounded, nonduplicate history. A new edit invalidates redo states.
        state = before if before is not None else self.snapshot()
        if not self.undo_stack or self.undo_stack[-1] != state:
            self.undo_stack.append(state)
            self.undo_stack = self.undo_stack[-80:]
        self.redo_stack.clear()

    def refresh(self):
        # Graph changes invalidate pending gestures, run animations, and previews.
        self.close_node_picker()
        self.search_anchor = None
        self.pending = None
        self.hint_var.set("Click an output pin, then an input pin to connect.")
        self.active_node = None
        self.run_serial += 1
        self.refresh_code()
        self.update_title()
        self.show_inspector()
        self.draw()

    def update_title(self):
        # Include unapplied inspector edits in the unsaved-change indicator.
        dirty = self.snapshot() != self.saved_state or self.form_dirty
        name = self.path.name if self.path else "Untitled graph"
        if self.function_owner:
            name = "Function: " + self.function_name
        self.root.title(f"{'* ' if dirty else ''}{name} — {APP_NAME} {APP_VERSION}")
        self.project_var.set(name)

    def refresh_code(self):
        # Recompile after edits and discard values from the previous graph run.
        self.run_values.clear()
        self.data_previews.clear()
        for window in self.data_windows:
            if window.winfo_exists():
                window.destroy()
        self.data_windows.clear()
        self.update_value_inspector()
        self.error_node = None
        try:
            compiled = self.compile_project()
            self.current_code = compiled.code
            self.set_text(self.code_text, compiled.code)
            self.graph_status.set(f"{len(self.graph.nodes)} nodes · {len(self.graph.edges)} wires" + (f" · {len(compiled.warnings)} notice(s)" if compiled.warnings else " · Ready"))
            try:
                # Token locations map directly to Tk text indices for highlighting.
                for token in tokenize.generate_tokens(io.StringIO(compiled.code).readline):
                    tag = None
                    if token.type == tokenize.NAME and token.string in ("def", "return", "if", "else", "from", "import", "raise", "not", "or", "and", "pass"):
                        tag = "keyword"
                    elif token.type == tokenize.STRING:
                        tag = "string"
                    elif token.type == tokenize.NUMBER:
                        tag = "number"
                    elif token.type == tokenize.COMMENT:
                        tag = "comment"
                    if tag:
                        self.code_text.tag_add(tag, f"{token.start[0]}.{token.start[1]}", f"{token.end[0]}.{token.end[1]}")
            except tokenize.TokenError:
                pass
            # Keep the generated program in view despite the embedded unit runtime.
            main_line = next(i for i, line in enumerate(compiled.code.splitlines(), 1) if line == "def main():")
            self.code_text.see(f"{main_line}.0")
        except GraphError as exc:
            # An invalid graph still remains editable; replace stale generated
            # code with the validation message until the graph is corrected.
            self.error_node = exc.node_id
            self.current_code = ""
            self.set_text(self.code_text, "# Graph needs attention\n# " + str(exc).replace("\n", "\n# "))
            self.graph_status.set("Needs attention")

    def transform(self, x, y):
        # Graph coordinates -> canvas pixels: scale first, then pan.
        return x * self.zoom + self.offset[0], y * self.zoom + self.offset[1]

    def inverse(self, x, y):
        # Canvas pixels -> graph coordinates, used by placement and navigation.
        return (x - self.offset[0]) / self.zoom, (y - self.offset[1]) / self.zoom

    def pin_position(self, node, direction, name):
        # Port order defines row placement on each side of the node card.
        mapping = ports(node)[0 if direction == "in" else 1]
        index = list(mapping).index(name)
        return self.transform(node.x + (0 if direction == "in" else NODE_W), node.y + HEADER_H + 17 + index * ROW_H)

    def roundrect(self, x1, y1, x2, y2, radius, **kw):
        # A smoothed polygon provides rounded cards without image assets.
        r = radius
        points = [x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.canvas.create_polygon(points, smooth=True, splinesteps=18, **kw)

    def draw(self):
        # Rebuild canvas items and hit-test caches together so they stay aligned.
        if self.closed:
            return
        c, z = self.canvas, self.zoom
        c.delete("all")
        self.port_hits, self.node_hits, self.edge_items = [], [], {}
        width, height = c.winfo_width(), c.winfo_height()
        spacing = 32 * z
        # Keep the grid quiet and sparse when zooming out.
        while spacing < 22:
            spacing *= 2
        startx, starty = self.offset[0] % spacing, self.offset[1] % spacing
        for i in range(int(width / spacing) + 1):
            for j in range(int(height / spacing) + 1):
                x, y = startx + i * spacing, starty + j * spacing
                c.create_oval(x, y, x+1.5, y+1.5, fill="#24344a", outline="")
        # Draw wires behind cards; retain item IDs for wire selection.
        for edge in self.graph.edges:
            a, b = self.graph.nodes[edge.source], self.graph.nodes[edge.target]
            x1, y1 = self.pin_position(a, "out", edge.output)
            x2, y2 = self.pin_position(b, "in", edge.input)
            # Horizontal control points let the curve leave and enter pins cleanly.
            bend = max(65*z, abs(x2-x1)*0.48)
            color = "#a5b8d4" if ports(a)[1][edge.output] == "exec" else GREEN
            emphasized = self.selected in (edge.source, edge.target) or self.active_node in (edge.source, edge.target)
            if self.active_node in (edge.source, edge.target):
                color = GOLD
            if edge == self.selected_edge:
                color = GOLD
                emphasized = True
            if emphasized:
                # A wider line beneath the wire creates a selection glow.
                c.create_line(x1,y1,x1+bend,y1,x2-bend,y2,x2,y2, smooth="bezier", splinesteps=30,
                              fill="#33485f", width=max(5, 7*z))
            item = c.create_line(x1,y1,x1+bend,y1,x2-bend,y2,x2,y2, smooth="bezier", splinesteps=30,
                                 fill=color, width=max(1.7, (3 if emphasized else 2.4)*z), arrow="last", arrowshape=(7*z, 8*z, 3*z))
            self.edge_items[item] = edge
        ordered = list(self.graph.nodes.values())
        # Selected cards are drawn last and take precedence in hit testing.
        ordered.sort(key=lambda n: n.id == self.selected)
        for node in ordered:
            x, y = self.transform(node.x, node.y)
            w, h = NODE_W*z, node_height(node)*z
            # Skip offscreen cards while keeping all graph connections available.
            if x+w < -20 or y+h < -20 or x > width+20 or y > height+20:
                continue
            color = COLORS[node.kind]
            outline = ERROR if node.id == self.error_node else (GOLD if node.id == self.active_node else (color if node.id == self.selected else LINE))
            self.roundrect(x+2*z,y+7*z,x+w+2*z,y+h+7*z,12*z,fill="#070b12",outline="")
            if node.id in (self.selected, self.error_node, self.active_node):
                self.roundrect(x-4*z,y-4*z,x+w+4*z,y+h+4*z,14*z,fill="",outline=outline,width=1)
            self.roundrect(x,y,x+w,y+h,12*z,fill=CARD,outline=outline,width=3 if node.id == self.error_node else (2 if node.id in (self.selected,self.active_node) else 1))
            self.roundrect(x+1*z,y+1*z,x+w-1*z,y+HEADER_H*z,11*z,fill="#223249",outline="")
            c.create_line(x+12*z,y+HEADER_H*z,x+w-12*z,y+HEADER_H*z,fill=LINE)
            self.roundrect(x+10*z,y+15*z,x+34*z,y+39*z,6*z,fill=color,outline="")
            symbol = {"Main": ">", "Variable": "x", "Function": "f", "Math": "+", "Compare": "=", "Branch": "?", "Print": ">_", "Convert":"~", "Text Join":"&", "Input":"?", "Boolean Logic":"&", "Clamp":"[]", "Random":"#", "Assign":"=", "Custom":"f", "Function Call":"f", "Function Inputs":"in", "Return":"out",
                      "Quantity":"u", "Unit Convert":"~", "Force":"F", "Torque":"T", "Ohm's Law":"V", "Electrical Power":"P",
                      "CSV Input":"in", "Data Table":"[]", "Line Plot":"/", "Scatter Plot":"..", "CSV Output":"out",
                      "Serial Connect":"+", "Serial Send":">", "Serial Read Line":"<", "Parse Value":"#", "Serial Disconnect":"x"}[node.kind]
            c.create_text(x+22*z,y+27*z,text=symbol,fill=BG,font=(self.code_font,max(7,round(11*z)),"bold"))
            title_size = max(7,round(11*z))
            # Cache measurement fonts by zoom-derived size to avoid recreating
            # a Tk font for every card on every redraw.
            if title_size not in self.title_fonts:
                self.title_fonts[title_size] = self.tkfont.Font(family=self.ui_font, size=title_size, weight="bold")
            title_font = self.title_fonts[title_size]
            title = node.title
            # Measure actual glyph widths so long titles fit the card header.
            while len(title) > 1 and title_font.measure(title) > w-58*z:
                title = title[:-2] + "…"
            c.create_text(x+43*z,y+21*z,text=title,anchor="w",fill=TEXT,
                          font=(self.ui_font,max(7,round(11*z)),"bold"))
            c.create_text(x+44*z,y+39*z,text=node.kind.upper(),anchor="w",fill=color,
                          font=(self.ui_font,max(6,round(7*z)),"bold"))
            for direction, mapping in (("in", ports(node)[0]), ("out", ports(node)[1])):
                for name, kind in mapping.items():
                    px, py = self.pin_position(node, direction, name)
                    r = max(4, 5.5*z)
                    fill = TEXT if kind == "exec" else GREEN
                    connected = self.graph.input_edge(node.id, name) if direction == "in" else any(e.source == node.id and e.output == name for e in self.graph.edges)
                    c.create_oval(px-r-2*z,py-r-2*z,px+r+2*z,py+r+2*z,fill=BG,outline="")
                    if self.pending == (node.id, direction, name):
                        c.create_oval(px-r-4,py-r-4,px+r+4,py+r+4,outline=GOLD,width=2)
                    elif self.pending and self.compatible_pin((node.id, direction, name)):
                        c.create_oval(px-r-4,py-r-4,px+r+4,py+r+4,outline=GREEN,width=2)
                    # Diamonds represent execution flow; circles carry data.
                    if kind == "exec":
                        c.create_polygon(px,py-r,px+r,py,px,py+r,px-r,py,fill=fill,outline=BG,width=1)
                    else:
                        c.create_oval(px-r,py-r,px+r,py+r,fill=fill if connected else CARD,outline=fill,width=max(1.5,1.7*z))
                    label = name
                    if direction == "in" and kind == "data" and not self.graph.input_edge(node.id, name):
                        # Unwired inputs display the fallback value used at runtime.
                        label += "  ·  " + str(input_default(node,name))[:12]
                        if node.kind in ENGINEERING:
                            label += " " + ENGINEERING[node.kind][0][name]
                    c.create_text(px+(13*z if direction=="in" else -13*z),py,text=label,anchor="w" if direction=="in" else "e",
                                  fill=MUTED if kind=="exec" else "#c0d9d0",font=(self.ui_font,max(6,round(9*z))))
                    self.port_hits.append((px,py,node.id,direction,name))
            p = node.props
            detail = {
                "Main": "PROGRAM ENTRY",
                "Variable": f"{p.get('type', '')}  =  {p.get('value', '')}",
                "Function": p.get("expression", ""),
                "Math": f"a {p.get('operator', '')} b",
                "Compare": f"a {p.get('operator', '')} b",
                "Branch": "TRUE / FALSE PATHS",
                "Print": p.get("label") or "Console output",
                "Convert": "Convert to " + p.get("type", "Number"),
                "Text Join": "Join with " + repr(p.get("separator", "")),
                "Input": p.get("prompt", "Enter a value"),
                "Boolean Logic": "NOT a (b ignored)" if p.get("operator") == "NOT" else "a " + p.get("operator", "AND") + " b",
                "Clamp": f"Range {p.get('min', '')} … {p.get('max', '')}",
                "Random": p.get("mode", "Integer") + (" · seed " + p["seed"] if p.get("seed", "").strip() else " · fresh each run"),
                "Assign": p.get("name", "Assigned value"),
                "Custom": p.get("expression",""),
                "Function Call": "Double-click to open function",
                "Function Inputs": ", ".join(p.get("names", [])),
                "Return": "Return to caller",
                "Quantity": p.get("value", "1") + " " + p.get("unit", "m"),
                "Unit Convert": "Convert to " + p.get("unit", "cm"),
                "CSV Input": p.get("path") or "Choose a CSV file",
                "CSV Output": p.get("path") or "Choose an output CSV",
                "Data Table": p.get("columns") or "Inspect all columns",
                "Line Plot": p.get("title") or "Line plot of selected columns",
                "Scatter Plot": p.get("title") or "Scatter plot of selected columns",
                "Serial Connect": p.get("mode", "Simulation") + " | " + p.get("port", "") + " @ " + p.get("baud", "9600"),
                "Serial Send": p.get("command", "READ"),
                "Serial Read Line": "Read timeout: " + p.get("timeout", "1") + " s",
                "Parse Value": "Parse " + (p.get("key") or "plain number"),
                "Serial Disconnect": "Close serial connection",
                **{name: "Result in " + spec[1] for name, spec in ENGINEERING.items()},
            }[node.kind]
            # A completed run replaces the configuration summary with its value.
            if node.id in self.run_values:
                detail = "→ " + self.run_values[node.id]["value"]
            self.roundrect(x+10*z,y+h-34*z,x+w-10*z,y+h-8*z,6*z,fill="#111b2a",outline="")
            c.create_text(x+18*z,y+h-21*z,text=str(detail).replace("\n"," ")[:31],anchor="w",fill=color,
                          font=(self.code_font,max(6,round(9*z))))
            self.node_hits.append((x,y,x+w,y+h,node.id))
        if not self.graph.nodes:
            c.create_text(width/2,height/2-15,text="Build your first graph",fill=TEXT,font=(self.ui_font,20,"bold"))
            c.create_text(width/2,height/2+23,text="Drag Main from the palette to begin.",fill=MUTED,font=(self.ui_font,11))
        self.zoom_var.set(f"{round(z*100)}%")
        self.draw_minimap()

    def draw_minimap(self):
        # Include both graph bounds and the viewport so panning into empty space
        # still produces a meaningful overview and viewport rectangle.
        mini = self.minimap
        mini.delete("all")
        nodes = list(self.graph.nodes.values())
        vx1, vy1 = self.inverse(0, 0)
        vx2, vy2 = self.inverse(self.canvas.winfo_width(), self.canvas.winfo_height())
        x1 = min([vx1] + [n.x for n in nodes])-25
        y1 = min([vy1] + [n.y for n in nodes])-25
        x2 = max([vx2] + [n.x+NODE_W for n in nodes])+25
        y2 = max([vy2] + [n.y+node_height(n) for n in nodes])+25
        scale = min(144/max(1, x2-x1), 78/max(1, y2-y1))
        ox, oy = (164-(x2-x1)*scale)/2, 24+(78-(y2-y1)*scale)/2
        # Save the same mapping used to draw so minimap clicks can invert it.
        self.minimap_transform = (x1, y1, scale, ox, oy)
        mini.create_text(10, 11, text="OVERVIEW", anchor="w", fill=MUTED, font=(self.ui_font, 7, "bold"))
        for edge in self.graph.edges:
            a, b = self.graph.nodes[edge.source], self.graph.nodes[edge.target]
            mini.create_line(ox+(a.x+NODE_W-x1)*scale, oy+(a.y+node_height(a)/2-y1)*scale,
                             ox+(b.x-x1)*scale, oy+(b.y+node_height(b)/2-y1)*scale, fill=LINE)
        for n in nodes:
            mini.create_rectangle(ox+(n.x-x1)*scale, oy+(n.y-y1)*scale,
                                  ox+(n.x+NODE_W-x1)*scale, oy+(n.y+node_height(n)-y1)*scale,
                                  fill=COLORS[n.kind], outline=ERROR if n.id == self.error_node else "")
        mini.create_rectangle(ox+(vx1-x1)*scale, oy+(vy1-y1)*scale,
                              ox+(vx2-x1)*scale, oy+(vy2-y1)*scale, outline=TEXT, width=1)

    def minimap_navigate(self, event):
        # Invert the minimap transform and center the main view at that point.
        if event.y < 23:
            # The heading above the overview is not a navigation target.
            return
        x1, y1, scale, ox, oy = self.minimap_transform
        wx, wy = x1+(event.x-ox)/scale, y1+(event.y-oy)/scale
        self.offset = [self.canvas.winfo_width()/2-wx*self.zoom,
                       self.canvas.winfo_height()/2-wy*self.zoom]
        self.draw()

    def compatible_pin(self, pin):
        # Connections need opposite directions, distinct nodes, and equal kinds
        # (execution or data); Graph.connect performs the final validation.
        if not self.pending or pin[0] == self.pending[0] or pin[1] == self.pending[1]:
            return False
        def kind(item):
            return ports(self.graph.nodes[item[0]])[0 if item[1] == "in" else 1][item[2]]
        return kind(pin) == kind(self.pending)

    def hit_port(self, x, y):
        # Reverse drawing order favors topmost pins; keep a usable pixel radius.
        for px,py,node_id,direction,name in reversed(self.port_hits):
            if (px-x)**2 + (py-y)**2 <= max(10, 10*self.zoom)**2:
                return node_id, direction, name
        return None

    def hit_node(self, x, y):
        # Check frontmost card rectangles before cards drawn underneath them.
        return next((n for x1,y1,x2,y2,n in reversed(self.node_hits) if x1 <= x <= x2 and y1 <= y <= y2), None)

    def hit_edge(self, x, y):
        # A small pixel tolerance makes thin curved wires easier to select.
        return next((self.edge_items[item] for item in reversed(self.canvas.find_overlapping(x-5,y-5,x+5,y+5)) if item in self.edge_items), None)

    def select(self, node_id=None, edge=None):
        # Invalid inspector edits must be resolved before changing selection.
        if not self.apply_properties():
            return False
        self.selected, self.selected_edge = node_id, edge
        self.show_inspector()
        self.draw()
        return True

    def canvas_press(self, event):
        # Prioritize pins, then cards, then wires; empty space starts a pan or
        # establishes an anchor for typing a new node.
        self.close_node_picker()
        self.search_anchor = None
        self.canvas.focus_set()
        if self.busy:
            return
        pin = self.hit_port(event.x,event.y)
        if pin:
            if self.pending and pin != self.pending:
                if not self.apply_properties():
                    return
                first, second = self.pending, pin
                if first[1] == second[1]:
                    self.status_var.set("Choose an output pin and an input pin.")
                    return
                # Either endpoint may be clicked first; connect always expects
                # source/output followed by target/input.
                output, input_pin = (first, second) if first[1] == "out" else (second, first)
                before = self.snapshot()
                try:
                    self.graph.connect(output[0],output[2],input_pin[0],input_pin[2])
                    self.remember(before)
                    self.status_var.set("Connected. Right-click a pin or wire to disconnect.")
                    self.refresh()
                except GraphError as exc:
                    self.status_var.set(str(exc))
                    self.cancel_wire()
            else:
                self.pending = pin
                self.hint_var.set("Choose a compatible pin. Esc cancels.")
                self.draw()
            return
        if self.pending:
            self.cancel_wire()
            return
        node_id = self.hit_node(event.x,event.y)
        if node_id:
            if not self.select(node_id):
                return
            self.drag_node = node_id
            node = self.graph.nodes[node_id]
            # Retain both coordinate systems and one pre-drag undo snapshot.
            self.drag_origin = (event.x,event.y,node.x,node.y)
            self.drag_snapshot = self.snapshot()
            return
        edge = self.hit_edge(event.x,event.y)
        if not self.select(edge=edge):
            return
        if edge is None:
            self.search_anchor = self.inverse(event.x,event.y)
            self.hint_var.set("Type a node name or Python, then Enter. Drag to pan.")
            self.pan_start(event)

    def canvas_drag(self, event):
        # Mouse deltas are pixels; divide by zoom when moving graph-space nodes.
        if self.busy:
            return
        if self.drag_node:
            x,y,nx,ny = self.drag_origin
            node = self.graph.nodes[self.drag_node]
            node.x, node.y = nx+(event.x-x)/self.zoom, ny+(event.y-y)/self.zoom
            self.draw()
        elif self.pending:
            self.canvas_motion(event)
        elif self.pan_origin:
            self.pan_move(event)

    def canvas_release(self, event):
        # A full drag contributes one history entry, regardless of motion events.
        if self.drag_node:
            if self.snapshot() != self.drag_snapshot:
                self.remember(self.drag_snapshot)
                self.update_title()
            self.drag_node = self.drag_origin = self.drag_snapshot = None
        elif self.pending:
            target = self.hit_port(event.x,event.y)
            if target and target != self.pending:
                # Reuse click-to-connect validation for a drag-to-connect gesture.
                self.canvas_press(event)
        self.pan_origin = None

    def canvas_motion(self, event):
        # Update hover feedback and a temporary wire without changing the graph.
        self.canvas.delete("pending")
        self.canvas.delete("hover")
        target = self.hit_port(event.x,event.y)
        self.canvas.configure(cursor="crosshair" if self.pending else ("hand2" if target or self.hit_node(event.x,event.y) else "arrow"))
        if not self.pending:
            if target:
                node_id, direction, port = target
                self.hint_var.set(f"{self.graph.nodes[node_id].title} · {port} · {'Input' if direction == 'in' else 'Output'} — click or drag to connect")
                px, py = self.pin_position(self.graph.nodes[node_id], direction, port)
                self.canvas.create_oval(px-10,py-10,px+10,py+10,outline=TEXT,width=2,tags="hover")
            else:
                self.hint_var.set("Type a node name or Python, then Enter. Drag to pan." if self.search_anchor is not None
                                  else "Click an output pin, then an input pin to connect.")
            return
        node_id,direction,port = self.pending
        if node_id not in self.graph.nodes:
            return
        x,y = self.pin_position(self.graph.nodes[node_id],direction,port)
        # Reverse the preview curve when dragging outward from an input pin.
        sign = 1 if direction == "out" else -1
        compatible = target and self.compatible_pin(target)
        color = GREEN if compatible else (ERROR if target and target != self.pending else GOLD)
        self.hint_var.set("Release to connect matching pins." if compatible else "Choose a matching input/output. Esc cancels.")
        if target:
            px, py = self.pin_position(self.graph.nodes[target[0]],target[1],target[2])
            self.canvas.create_oval(px-11,py-11,px+11,py+11,outline=color,width=2,tags="hover")
        self.canvas.create_line(x,y,x+80*sign,y,event.x-80*sign,event.y,event.x,event.y,
                                smooth="bezier",fill=color,width=3,dash=(5,4),tags="pending")

    def cancel_wire(self):
        # Escape clears transient wiring and picker state together.
        self.close_node_picker()
        self.pending = None
        self.hint_var.set("Click an output pin, then an input pin to connect.")
        self.draw()

    def pan_start(self, event):
        # Store the original offset so each motion uses an absolute drag delta.
        self.close_node_picker()
        self.pan_origin = (event.x,event.y,self.offset[0],self.offset[1])

    def pan_move(self, event):
        # Ignore tiny motions to preserve the empty-space typing anchor on clicks.
        if self.pan_origin:
            if abs(event.x-self.pan_origin[0])+abs(event.y-self.pan_origin[1]) < 4:
                return
            self.search_anchor = None
            x,y,ox,oy = self.pan_origin
            self.offset = [ox+event.x-x,oy+event.y-y]
            self.draw()

    def wheel(self, event):
        # Use the wheel direction for consistent incremental zoom steps.
        if event.delta:
            self.zoom_at(event.x,event.y,1.1 if event.delta>0 else 1/1.1)

    def zoom_at(self, x, y, factor):
        # Adjust the offset so the graph point under the cursor stays fixed.
        wx,wy = self.inverse(x,y)
        self.zoom = max(0.01,min(1.8,self.zoom*factor))
        self.offset = [x-wx*self.zoom,y-wy*self.zoom]
        self.draw()

    def zoom_center(self, factor):
        # Toolbar zoom keeps the middle of the current viewport stationary.
        self.zoom_at(self.canvas.winfo_width()/2, self.canvas.winfo_height()/2, factor)

    def reset_zoom(self):
        # Applying the reciprocal restores 100% through the same zoom path.
        self.zoom_center(1/self.zoom)

    def fit_view(self):
        # Fit padded graph bounds while limiting magnification of small graphs.
        if not self.graph.nodes:
            self.zoom,self.offset = 1.0,[40,40]
        else:
            nodes = list(self.graph.nodes.values())
            x1,y1 = min(n.x for n in nodes)-35,min(n.y for n in nodes)-35
            x2,y2 = max(n.x+NODE_W for n in nodes)+35,max(n.y+node_height(n) for n in nodes)+35
            width,height = max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())
            self.zoom = min(1.15,max(0.01,min(width/(x2-x1),height/(y2-y1))))
            self.offset = [(width-(x2-x1)*self.zoom)/2-x1*self.zoom,(height-(y2-y1)*self.zoom)/2-y1*self.zoom]
        self.draw()

    def palette_start(self, event, kind):
        # Screen coordinates distinguish a simple palette click from a drag.
        self.palette_drag = (kind,event.x_root,event.y_root)
        self.status_var.set(f"Drag {kind} onto the canvas, or click to add it.")

    def palette_motion(self, event):
        # A drag cursor signals that releasing can place a palette node.
        self.root.configure(cursor="crosshair")

    def palette_release(self, event):
        # Drop inside the canvas at the pointer; a simple click adds at its center.
        self.root.configure(cursor="")
        if not getattr(self,"palette_drag",None):
            return
        kind,sx,sy = self.palette_drag
        self.palette_drag = None
        # Palette events use screen coordinates; the canvas hit area is local.
        x,y = event.x_root-self.canvas.winfo_rootx(),event.y_root-self.canvas.winfo_rooty()
        inside = 0<=x<self.canvas.winfo_width() and 0<=y<self.canvas.winfo_height()
        if inside:
            self.add_node(kind,*self.inverse(x,y))
        elif abs(event.x_root-sx)+abs(event.y_root-sy)<10:
            x,y=self.inverse(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2)
            self.add_node(kind,x-NODE_W/2,y-60)

    def add_node(self, kind, x, y):
        # Preserve a pre-edit snapshot and select the successfully created node.
        if self.busy or not self.apply_properties():
            return
        before = self.snapshot()
        try:
            node = self.make_palette_node(self.graph,kind,x,y)
            self.remember(before)
            self.selected,self.selected_edge = node.id,None
            self.status_var.set(f"Added {kind}. Edit its properties in the inspector.")
            self.refresh()
        except GraphError as exc:
            self.status_var.set(str(exc))

    def make_palette_node(self, graph, kind, x, y):
        # Resolve function signatures and custom templates before falling back
        # to the built-in node factory. Template properties must not be shared.
        if kind == "Function Call":
            from tkinter import simpledialog
            names = list(self.function_library())
            if not names:
                raise GraphError("Create a reusable function using Functions > New function first.")
            name = names[0] if len(names) == 1 else simpledialog.askstring("Function Call", "Function name: " + ", ".join(names), parent=self.root)
            if not name:
                raise GraphError("Function call canceled.")
            return graph.add_call(name, x, y, self.function_library())
        if kind in ("Return", "Function Inputs"):
            if not self.function_owner:
                raise GraphError("These nodes belong inside a reusable function.")
            definition = self.function_library()[self.function_name]
            if kind == "Function Inputs" and any(n.kind == kind for n in graph.nodes.values()):
                raise GraphError("This function already has its Function Inputs node.")
            node = graph.add_node(kind, x, y)
            # Derive the boundary-node ports from the reusable function signature.
            node.props = ({"names": list(definition["inputs"])} if kind == "Function Inputs" else
                          {"values": [{"name": name, "default": "0"} for name in definition["returns"]]})
            return node
        if kind in self.custom_templates:
            node = graph.add_node("Custom",x,y)
            node.props = copy.deepcopy(self.custom_templates[kind])
            return node
        return graph.add_node(kind,x,y)

    def refresh_custom_palette(self):
        # Rebuild saved-template entries after the custom library changes.
        for child in self.custom_palette.winfo_children():
            child.destroy()
        self.label(self.custom_palette,"MY NODES",fg=COLORS["Custom"],font=(self.ui_font,8,"bold")).pack(anchor="w",padx=12,pady=(16,6))
        if not self.custom_templates:
            self.label(self.custom_palette,"Use Create node above to save your own calculation.",fg=MUTED,
                       font=(self.ui_font,8),wraplength=135,justify="left").pack(fill="x",padx=12,pady=(0,8))
        for name,props in sorted(self.custom_templates.items()):
            item = self.tk.Frame(self.custom_palette,bg=CARD,cursor="hand2")
            item.pack(fill="x",padx=10,pady=3)
            label = self.label(item,name,fg=COLORS["Custom"],font=(self.ui_font,9,"bold"),wraplength=120,padx=8,pady=9)
            label.pack(fill="x")
            for widget in (item,label):
                self.attach_tooltip(widget,props.get("description") or props["expression"])
                # Freeze each template name in its callback and make both the
                # card background and its label act as the same draggable item.
                widget.bind("<ButtonPress-1>",lambda e,k=name:self.palette_start(e,k))
                widget.bind("<B1-Motion>",self.palette_motion)
                widget.bind("<ButtonRelease-1>",self.palette_release)

    def open_node_creator(self):
        # Keep one creator window with named inputs, defaults, and a formula test.
        if self.busy or not self.apply_properties():
            return
        if self.creator_window is not None and self.creator_window.winfo_exists():
            self.creator_window.lift()
            return
        self.close_node_picker()
        window = self.tk.Toplevel(self.root)
        self.creator_window = window
        window.title("Create your own node — pydrop")
        window.geometry("660x720")
        window.minsize(600,680)
        window.configure(bg=PANEL)
        def close_creator():
            window.destroy()
            self.creator_window = None
        window.protocol("WM_DELETE_WINDOW",close_creator)
        window.bind("<Escape>",lambda e:close_creator())
        body = self.tk.Frame(window,bg=PANEL,padx=20,pady=16)
        body.pack(fill="both",expand=True)
        self.label(body,"Create a calculation node",font=(self.ui_font,18,"bold")).pack(fill="x",pady=(0,6))
        self.label(body,"Give it a name, choose up to 3 number inputs, and write a formula.\nYour node is saved in My Nodes and can be reused in any graph.",
                   fg=MUTED,justify="left").pack(fill="x",pady=(0,14))
        self.creator_vars = {"template_name":self.tk.StringVar(value="Add tax"),
                             "description":self.tk.StringVar(value="Add a percentage of tax to a price."),
                             "expression":self.tk.StringVar(value="round(price * (1 + rate / 100), 2)")}
        def entry(parent,var):
            widget = self.tk.Entry(parent,textvariable=var,bg=CARD,fg=TEXT,insertbackground=GREEN,relief="flat",
                                    highlightthickness=1,highlightbackground=LINE,highlightcolor=GREEN,font=(self.code_font,11))
            return widget
        self.label(body,"1. Name your node",font=(self.ui_font,11,"bold")).pack(anchor="w",pady=(0,5))
        name_entry = entry(body,self.creator_vars["template_name"])
        name_entry.pack(fill="x",ipady=6)
        self.label(body,"Short description (optional)",fg=MUTED,font=(self.ui_font,9)).pack(anchor="w",pady=(8,4))
        entry(body,self.creator_vars["description"]).pack(fill="x",ipady=5)
        self.label(body,"2. Give your inputs names and example numbers",font=(self.ui_font,11,"bold")).pack(anchor="w",pady=(14,5))
        self.label(body,"Use names like price or quantity. Leave unused names blank.",fg=MUTED,font=(self.ui_font,9)).pack(anchor="w",pady=(0,5))
        self.creator_inputs = []
        # Keep the form at three rows; blank names are discarded when it is read.
        for index,(name,default) in enumerate((("price","100"),("rate","10"),("","0")),1):
            row = self.tk.Frame(body,bg=PANEL)
            row.pack(fill="x",pady=3)
            self.label(row,f"Input {index}",fg=MUTED,width=7).pack(side="left")
            name_var,default_var = self.tk.StringVar(value=name),self.tk.StringVar(value=default)
            self.creator_inputs.append((name_var,default_var))
            entry(row,name_var).pack(side="left",fill="x",expand=True,ipady=5)
            self.label(row,"Default",fg=MUTED,padx=8).pack(side="left")
            default_entry = entry(row,default_var)
            default_entry.configure(width=10)
            default_entry.pack(side="left",ipady=5)
        self.label(body,"3. Write your formula",font=(self.ui_font,11,"bold")).pack(anchor="w",pady=(14,5))
        entry(body,self.creator_vars["expression"]).pack(fill="x",ipady=6)
        self.label(body,"Use your input names with +, -, *, / or helpers such as round and sqrt.\nExample: price * quantity. The result is one number.",fg=MUTED,font=(self.ui_font,9),justify="left").pack(anchor="w",pady=(6,8))
        self.creator_result = self.tk.StringVar(value="Try the example: price 100 and rate 10 should give 110.")
        self.label(body,textvariable=self.creator_result,fg=GREEN,wraplength=600,justify="left").pack(fill="x",pady=(6,10))
        actions = self.tk.Frame(body,bg=PANEL)
        actions.pack(fill="x",side="bottom")
        self.creator_test_button = self.button(actions,"Test formula",self.test_custom_formula)
        self.creator_test_button.pack(side="left")
        self.creator_create_button = self.button(actions,"Create & add",self.create_custom_node,accent=True)
        self.creator_create_button.pack(side="right")
        self.button(actions,"Cancel",close_creator).pack(side="right",padx=8)
        name_entry.focus_set()

    def creator_definition(self):
        # Omit unused input rows, then validate the full template consistently.
        props = {key:var.get().strip() for key,var in self.creator_vars.items()}
        props["inputs"] = [{"name":name.get().strip(),"default":default.get().strip()}
                           for name,default in self.creator_inputs if name.get().strip()]
        return validate_template(props)

    def test_custom_formula(self):
        # Compile a temporary Main -> Custom -> Print graph to exercise defaults
        # in a child interpreter with a short execution timeout.
        try:
            props = self.creator_definition()
            graph = Graph()
            main = graph.add_node("Main",0,0)
            node = graph.add_node("Custom",330,0)
            node.props = props
            out = graph.add_node("Print",660,0)
            out.props["label"] = ""
            graph.connect(main.id,"next",node.id,"run")
            graph.connect(node.id,"next",out.id,"run")
            graph.connect(node.id,"result",out.id,"value")
            # Use the normal compiler/runtime so a formula test matches a real run.
            with tempfile.TemporaryDirectory(prefix="pydrop-formula-") as directory:
                script = Path(directory) / "formula.py"
                script.write_text(compile_graph(graph).code, encoding="utf-8")
                result = subprocess.run(script_command(script),
                                        capture_output=True,text=True,encoding="utf-8",timeout=2,
                                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if result.returncode:
                self.creator_result.set("Check your example numbers: "+(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "The graph runtime exited without an error message. Check the installation."))
            else:
                self.creator_result.set("Result with these defaults: "+result.stdout.strip())
        except (GraphError,OSError,subprocess.TimeoutExpired) as exc:
            self.creator_result.set(str(exc))

    def create_custom_node(self):
        # Validate and connect a graph copy before saving the reusable template;
        # commit the live graph only after the library write succeeds.
        if self.busy or not self.apply_properties():
            return
        try:
            props = self.creator_definition()
            if self.custom_library_error:
                raise GraphError("The existing node library could not be read. Fix custom_nodes.json before saving new nodes.")
            graph = copy.deepcopy(self.graph)
            # Reuse the insertion point and connection mode from typed code entry.
            x,y = self.search_anchor or self.inverse(self.canvas.winfo_width()/2,self.canvas.winfo_height()/2)
            node = graph.add_node("Custom",x,y)
            node.props = copy.deepcopy(props)
            plan = CodeDrop(graph,node.id,[node.id],node.id,node.id)
            connect_drop(plan,self.code_connect_var.get(),self.selected)
            templates = save_template(self.custom_library_path,self.custom_templates,props)
        except (GraphError,OSError) as exc:
            self.creator_result.set(str(exc))
            return
        self.custom_templates = templates
        self.refresh_custom_palette()
        self.commit_code_drop(plan)
        self.creator_window.destroy()
        self.creator_window = None
        self.status_var.set(f"Created {props['template_name']}. Find it in My Nodes or type its name to add another.")

    def show_inspector(self):
        # Rebuild the form for the selected wire or node. Dynamic input fields
        # use an input: prefix to address defaults within nested property lists.
        self.inspector_canvas.yview_moveto(0)
        # Drop references to controls from the previous selection before rebuilding.
        for child in self.inspector_content.winfo_children():
            child.destroy()
        self.form_vars = {}
        self.column_widgets = []
        self.serial_port_widget = None
        self.form_dirty = False
        self.inspector_node = self.selected
        parent = self.inspector_content
        if self.selected_edge:
            edge = self.selected_edge
            self.label(parent,"Connection",font=(self.ui_font,17,"bold")).pack(anchor="w",pady=(2,12))
            self.label(parent,f"{self.graph.nodes[edge.source].title}.{edge.output}\n↓\n{self.graph.nodes[edge.target].title}.{edge.input}",justify="left",wraplength=258).pack(anchor="w",pady=8)
            self.button(parent,"Remove wire",self.delete_selected).pack(anchor="w",pady=15)
            return
        node = self.graph.nodes.get(self.selected)
        if node is None:
            self.label(parent,"Your code,\nconnected.",font=(self.ui_font,21,"bold"),justify="left").pack(anchor="w",pady=(12,18))
            self.label(parent,"Select a node to edit its values and behavior.",fg=MUTED,wraplength=248,justify="left").pack(anchor="w",pady=(0,20))
            for title,text in (("01  Start with Main","White diamond pins define the order of execution."),
                               ("02  Supply values","Green circles carry variables and calculated results."),
                               ("03  Run your graph","See real Python and its output in the lower panel.")):
                self.label(parent,title,font=(self.ui_font,10,"bold")).pack(anchor="w",pady=(15,5))
                self.label(parent,text,fg=MUTED,wraplength=248,justify="left",font=(self.ui_font,10)).pack(anchor="w")
            return
        self.label(parent,node.kind,fg=COLORS[node.kind],font=(self.ui_font,18,"bold")).pack(anchor="w",pady=(2,10))
        self.label(parent,node.title,fg=TEXT,font=(self.ui_font,11,"bold"),wraplength=240).pack(anchor="w",pady=(0,8))
        self.label(parent,node.props.get("description") or DESCRIPTIONS[node.kind],fg=MUTED,wraplength=240,justify="left",font=(self.ui_font,10)).pack(anchor="w",pady=(0,16))
        self.update_value_inspector()
        self.label(parent,"LAST RUN VALUE",fg=MUTED,font=(self.ui_font,8,"bold")).pack(anchor="w",pady=(0,5))
        self.label(parent,textvariable=self.node_value_var,fg=GREEN,wraplength=240,justify="left",font=(self.code_font,10)).pack(fill="x",pady=(0,12))
        # Each descriptor supplies a property key, label, and optional choices.
        # Custom/function signatures derive their default fields from the node.
        fields = {
            "Main": [],
            "Variable": [("name","Name",None),("type","Value type",["Number","Text","Boolean"]),("value","Value",None)],
            "Function": [("name","Function name",None),("expression","Formula (a, b, c)",None),("a","Default a",None),("b","Default b",None),("c","Default c",None)],
            "Math": [("operator","Operation",["+","-","*","/","//","%","**"]),("a","Default a",None),("b","Default b",None)],
            "Compare": [("operator","Comparison",[">",">=","<","<=","==","!="]),("a","Default a",None),("b","Default b",None)],
            "Branch": [("condition","Default condition",["True","False"])],
            "Print": [("label","Output prefix",None),("value","Default text",None)],
            "Convert": [("type","Convert to",["Number","Text","Boolean"]),("value","Default value",None)],
            "Text Join": [("a","Default first text",None),("b","Default second text",None),("separator","Separator",None)],
            "Input": [("prompt","Question",None)],
            "Boolean Logic": [("operator","Logic",["AND","OR","NOT"]),("a","Default a",["True","False"]),("b","Default b (AND / OR only)",["True","False"])],
            "Clamp": [("value","Default value",None),("min","Default minimum",None),("max","Default maximum",None)],
            "Random": [("mode","Generate",["Integer","Decimal"]),("min","Default minimum",None),("max","Default maximum",None),("seed","Seed (blank = random)",None)],
            "Assign": [("name","Assigned name",None),("value","Default value (text)",None)],
            "Custom": [("expression","Formula (use your input names)",None)] + [("input:"+item["name"],"Default "+item["name"],None) for item in node.props.get("inputs",[])],
            "Function Call": [("input:"+item["name"], "Default "+item["name"], None) for item in node.props.get("inputs", [])],
            "Function Inputs": [],
            "Return": [("input:"+item["name"], "Default "+item["name"], None) for item in node.props.get("values", [])],
            "Quantity": [("value", "Default magnitude (unitless number)", None), ("unit", "Unit (1 = unitless)", UNIT_NAMES)],
            "Unit Convert": [("value", "Default magnitude", None), ("from_unit", "Default source unit", UNIT_NAMES), ("unit", "Target unit", UNIT_NAMES)],
            "CSV Input": [("path", "CSV file", None), ("delimiter", "Delimiter (one character)", None),
                          ("x_column", "x output column (blank = first)", None), ("y_column", "y output column (blank = second)", None)],
            "Data Table": [("columns", "Columns (comma separated; blank = all)", None),
                           ("column_name", "Added column name (values wire)", None)],
            "Line Plot": [("x_column", "X column (blank = first)", None), ("y_column", "Y column (blank = second)", None),
                          ("title", "Plot title", None), ("x_label", "X axis label", None), ("y_label", "Y axis label", None)],
            "Scatter Plot": [("x_column", "X column (blank = first)", None), ("y_column", "Y column (blank = second)", None),
                             ("title", "Plot title", None), ("x_label", "X axis label", None), ("y_label", "Y axis label", None)],
            "CSV Output": [("path", "Destination CSV", None), ("columns", "Columns (comma separated; blank = all)", None),
                           ("delimiter", "Delimiter (one character)", None), ("mode", "Existing file", ["Error if exists", "Overwrite"])],
            "Serial Connect": [("mode", "Connection mode", ["Simulation", "Hardware"]),
                               ("port", "Serial port (Refresh ports to discover)", None),
                               ("baud", "Baud rate", ["9600", "19200", "38400", "57600", "115200", "230400"]),
                               ("timeout", "Default read timeout (0-30 seconds)", None)],
            "Serial Send": [("command", "Command (text)", None), ("newline", "Command ending", ["LF", "CRLF", "None"])],
            "Serial Read Line": [("timeout", "Read timeout (0-30 seconds)", None)],
            "Parse Value": [("line", "Default line", None), ("key", "Key (blank = plain number)", None),
                            ("separator", "Field separator", None)],
            "Serial Disconnect": [],
            **{name: [(pin, "Default " + pin + " (" + unit + ")", None) for pin, unit in spec[0].items()]
               for name, spec in ENGINEERING.items()},
        }[node.kind]
        for key,title,choices in fields:
            # Match form keys to pin names to show when a wire overrides a default.
            port_key = key.split(":",1)[1] if key.startswith("input:") else key
            wired = self.graph.input_edge(node.id,port_key)
            label = title + ("  (wired)" if wired else "")
            self.label(parent,label,fg=MUTED,font=(self.ui_font,9)).pack(anchor="w",pady=(9,5))
            value = input_default(node,port_key) if key.startswith("input:") else node.props[key]
            var=self.tk.StringVar(value=str(value))
            self.form_vars[key]=var
            if node.kind == "Serial Connect" and key == "port":
                entry = self.ttk.Combobox(parent, textvariable=var, values=self.serial_ports,
                                          state="normal", font=(self.ui_font,10))
                self.serial_port_widget = entry
            elif key in ("x_column", "y_column"):
                entry = self.ttk.Combobox(parent, textvariable=var, values=["", *self.available_columns(node)],
                                          state="normal", font=(self.ui_font,10))
                self.column_widgets.append(entry)
            elif choices:
                # Units and baud rates allow typed values beyond the suggestions.
                entry=self.ttk.Combobox(parent,textvariable=var,values=choices,
                                       state="normal" if key in ("unit", "from_unit", "baud") else "readonly",font=(self.ui_font,10))
            else:
                entry=self.tk.Entry(parent,textvariable=var,bg=CARD,fg=TEXT,insertbackground=TEXT,
                                    relief="flat",highlightthickness=1,highlightbackground=LINE,
                                    highlightcolor=COLORS[node.kind],font=(self.code_font,11))
            entry.pack(fill="x",ipady=5)
            if wired:
                self.attach_tooltip(entry, f"Connected to {self.graph.nodes[wired.source].title}.{wired.output}. Disconnect the wire to use this default.")
            elif key in ("unit", "from_unit"):
                self.attach_tooltip(entry, "Choose a symbol or type a compound unit such as kg*m/s^2. Symbols are case-sensitive. Use ohm for resistance and 1 for unitless.")
            elif key == "expression":
                self.attach_tooltip(entry, "Use a, b, c and math helpers. Example: round(a * b + c, 2)")
            elif node.kind == "Random" and key == "seed":
                self.attach_tooltip(entry, "Optional integer seed. The same seed, mode, and bounds repeat the result on each run. Blank varies the result.")
            entry.bind("<Return>",lambda e:self.apply_properties())
            # Install traces after initialization so populating the form stays clean.
            var.trace_add("write",lambda *args:self.form_changed())
            if key == "path" and node.kind in ("CSV Input", "CSV Output"):
                self.button(parent, "Browse…", lambda n=node: self.browse_csv(n), pady=4).pack(anchor="w", pady=(4,0))
        if node.kind == "CSV Input":
            self.button(parent, "Read column names", lambda: self.read_csv_columns(node), pady=5).pack(fill="x", pady=(10,0))
            self.label(parent, "Read column names to populate selectors. You can also type a column name.",
                       fg=MUTED, wraplength=240, justify="left", font=(self.ui_font,8)).pack(fill="x", pady=6)
        if node.kind in SERIAL_KINDS:
            self.label(parent, textvariable=self.serial_status_var, fg=GREEN, wraplength=240,
                       justify="left", font=(self.ui_font,9)).pack(fill="x", pady=(10,0))
            self.label(parent, "Run Serial Connect before Send or Read Line. Connections close at the end of each run. Simulation needs no hardware.",
                       fg=MUTED, wraplength=240, justify="left", font=(self.ui_font,8)).pack(fill="x", pady=6)
        if node.kind == "Serial Connect":
            self.button(parent, "Refresh ports", self.refresh_serial_ports, pady=5).pack(fill="x", pady=(6,0))
            self.label(parent, "Hardware uses optional pyserial. You can type a port and baud rate. Arduino startup takes about 2 seconds.",
                       fg=MUTED, wraplength=240, justify="left", font=(self.ui_font,8)).pack(fill="x", pady=6)
        if node.kind in ("CSV Input", "Data Table", "Line Plot", "Scatter Plot"):
            self.data_inspect_button = self.button(parent, "Inspect last data", lambda: self.inspect_data(node.id),
                                                   state="normal" if node.id in self.data_previews else "disabled")
            self.data_inspect_button.pack(fill="x", pady=(10,0))
        if node.kind in ("Data Table", "CSV Output"):
            names = self.available_columns(node)
            if names:
                self.label(parent, "Available: " + ", ".join(names), fg=MUTED, wraplength=240,
                           justify="left", font=(self.ui_font,8)).pack(fill="x", pady=6)
        if fields:
            self.button(parent,"Apply changes",self.apply_properties,accent=True).pack(fill="x",pady=(18,7))
            self.label(parent,"Enter to apply. Connected inputs use\nthe wire instead of the default value.",fg=MUTED,font=(self.ui_font,8),justify="left").pack(anchor="w",pady=(0,8))
        if node.kind=="Function":
            self.label(parent,"Math helpers\nabs · min · max · round · sqrt\nsin · cos · tan · log · exp · pi · e",fg=MUTED,font=(self.ui_font,8),justify="left").pack(anchor="w",pady=8)
        if node.kind == "Function Call":
            self.button(parent, "Open function graph", lambda: self.open_function(node.props["function"])).pack(fill="x", pady=8)
        actions=self.tk.Frame(parent,bg=PANEL)
        actions.pack(fill="x",pady=(16,10))
        if node.kind!="Main":
            self.button(actions,"Duplicate",self.duplicate_selected,padx=10).pack(side="left")
        self.button(actions,"Delete",self.delete_selected,padx=10,fg=ERROR).pack(side="right")

    def refresh_serial_ports(self):
        # Device discovery runs off the Tk thread; polling applies its result.
        if self.serial_scanning:
            return
        self.serial_scanning = True
        self.status_var.set("Looking for serial ports...")
        def worker():
            try:
                self.serial_port_queue.put((_serial_ports(), None))
            except Exception as exc:
                self.serial_port_queue.put(([], str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_serial(self):
        # Apply discovery results to whichever port selector is currently visible.
        try:
            found, error = self.serial_port_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self.serial_scanning = False
            self.serial_ports = ["SIMULATED", *sorted(set(found) - {"SIMULATED"})]
            widget = self.serial_port_widget
            if widget is not None and widget.winfo_exists():
                widget.configure(values=self.serial_ports)
            self.status_var.set("Cannot list ports: " + error if error else
                                f"Found {len(self.serial_ports)-1} hardware port(s). Select Hardware and a port, or use Simulation. Hardware requires pyserial.")
        while True:
            try:
                run_id, payload = self.serial_events.get_nowait()
            except queue.Empty:
                break
            # Late events from a previous run must not change the current status.
            if run_id != self.run_serial:
                continue
            connected, status, mode, port = payload
            self.serial_connected = connected
            self.serial_status_var.set(f"Serial | {mode} | {port}: {status}")
            self.serial_status_label.configure(fg=GREEN if connected else MUTED)

    def stop_run(self):
        # Killing the child unblocks waiting I/O; run polling handles completion.
        if not self.busy:
            return
        self.stop_requested = True
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        self.status_var.set("Stopping graph and closing serial connection...")

    def available_columns(self, node, seen=None):
        # Infer table headers upstream without reading files on the UI thread.
        seen = set() if seen is None else seen
        # The graph may be incomplete while editing, so guard recursive traversal.
        if node.id in seen:
            return []
        seen.add(node.id)
        # Prefer actual run data; otherwise fall back to cached headers and wiring.
        preview = self.data_previews.get(node.id)
        if preview and preview.get("columns"):
            return preview["columns"]
        if node.kind == "CSV Input":
            try:
                path = str(self.csv_path(node.props["path"]))
            except (ValueError, RuntimeError):
                return []
            return self.column_cache.get((path, node.props["delimiter"]), [])
        edge = self.graph.input_edge(node.id, "data")
        names = list(self.available_columns(self.graph.nodes[edge.source], seen)) if edge else []
        if node.kind == "Data Table" and node.props["columns"].strip():
            import csv
            try:
                # CSV parsing preserves quoted column names containing commas.
                selected = next(csv.reader([node.props["columns"]], skipinitialspace=True, strict=True))
                names = [name if name in names else name.strip() for name in selected]
            except csv.Error:
                names = []
        if node.kind == "Data Table" and self.graph.input_edge(node.id, "values"):
            name = node.props["column_name"]
            if name not in names:
                names.append(name)
        return names

    def browse_csv(self, node):
        # Input nodes choose an existing file; output nodes choose a destination.
        from tkinter import filedialog
        options = dict(parent=self.root, initialdir=str(self.data_directory()),
                       filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if node.kind == "CSV Output":
            path = filedialog.asksaveasfilename(title="CSV output destination", defaultextension=".csv", **options)
        else:
            path = filedialog.askopenfilename(title="Read CSV data", **options)
        if path:
            self.form_vars["path"].set(path)
            if node.kind == "CSV Input":
                self.read_csv_columns(node)

    def read_csv_columns(self, node):
        # Header discovery reads one CSV record in a worker. No file I/O touches Tk.
        path_text = self.form_vars["path"].get()
        delimiter = self.form_vars["delimiter"].get()
        # Accept the visible two-character spelling of a tab in the form.
        if delimiter == r"\t":
            delimiter = "\t"
        if not path_text.strip():
            self.status_var.set("Choose a CSV file before reading column names.")
            return
        if len(delimiter) != 1 or delimiter in "\r\n\0":
            self.status_var.set("The CSV delimiter must be one character, such as a comma or semicolon.")
            return
        try:
            path = self.csv_path(path_text)
        except (ValueError, RuntimeError) as exc:
            self.status_var.set(str(exc))
            return
        cache_key = (str(path), self.form_vars["delimiter"].get())
        self.status_var.set("Reading CSV column names…")
        def worker():
            import csv
            try:
                # utf-8-sig also accepts a BOM without making it part of the header.
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    names = next(csv.reader(stream, delimiter=delimiter, strict=True), [])
                if not names or any(not name.strip() for name in names) or len(set(names)) != len(names):
                    raise ValueError("CSV needs a header with nonempty, unique column names.")
                self.column_queue.put((node.id, cache_key, names, None))
            except FileNotFoundError:
                self.column_queue.put((node.id, cache_key, [], f"CSV file not found: {path}. Choose an existing file."))
            except (OSError, UnicodeError, csv.Error, ValueError) as exc:
                self.column_queue.put((node.id, cache_key, [], f"Cannot read CSV column names: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def poll_csv_columns(self):
        # Drain completed header reads without waiting for workers still running.
        while True:
            try:
                node_id, cache_key, names, error = self.column_queue.get_nowait()
            except queue.Empty:
                return
            if node_id not in self.graph.nodes:
                continue
            self.column_cache[cache_key] = names
            # A previous request may finish after the user changes its path.
            if self.inspector_node == node_id and "path" in self.form_vars:
                try:
                    current = (str(self.csv_path(self.form_vars["path"].get())), self.form_vars["delimiter"].get())
                except (ValueError, RuntimeError):
                    continue
                if current != cache_key:
                    continue
                for widget in self.column_widgets:
                    if widget.winfo_exists():
                        widget.configure(values=["", *names])
                self.status_var.set(error or f"Read {len(names)} columns. Select x and y, then Apply changes.")

    def inspect_data(self, node_id):
        # Header-only discovery has no table payload to display.
        payload = self.data_previews.get(node_id)
        if payload and payload.get("kind") != "columns":
            from data_ui import _data_display
            self.data_windows = [window for window in self.data_windows if window.winfo_exists()]
            self.data_windows.append(_data_display(payload, parent=self.root))

    def form_changed(self):
        # Defer validation until Apply or a navigation action needs the values.
        self.form_dirty = True
        self.update_title()

    def apply_properties(self):
        # Validate a copied node first, keeping invalid form text out of the graph.
        if not self.form_dirty:
            return True
        if self.busy:
            return False
        node=self.graph.nodes.get(self.inspector_node)
        if node is None:
            self.form_dirty=False
            return True
        candidate=copy.deepcopy(node)
        if candidate.kind in ("Custom", "Function Call", "Return"):
            # Signature defaults live in named lists rather than top-level props.
            if candidate.kind == "Custom":
                candidate.props["expression"] = self.form_vars["expression"].get()
            for item in candidate.props["values" if candidate.kind == "Return" else "inputs"]:
                item["default"] = self.form_vars["input:"+item["name"]].get()
        else:
            candidate.props.update({key:var.get() for key,var in self.form_vars.items()})
        try:
            validate_props(candidate)
            # Check downstream dimensional constraints before applying an edit.
            proposed = copy.deepcopy(self.graph)
            proposed.nodes[candidate.id] = candidate
            proposed.check_dimensions(partial=True)
            if candidate.kind in ("Variable","Function","Assign") and any(n.id!=candidate.id and n.kind in ("Variable","Function","Assign") and n.props["name"]==candidate.props["name"] for n in self.graph.nodes.values()):
                raise GraphError("That name is already used. Choose a unique name.")
        except GraphError as exc:
            from tkinter import messagebox
            self.error_node = node.id
            self.status_var.set(f"{node.title}: {exc}")
            self.draw()
            messagebox.showerror("Check node properties",str(exc),parent=self.root)
            return False
        if candidate.props != node.props:
            # A valid edit becomes one undo step; unchanged values add no history.
            self.remember()
            node.props=candidate.props
        self.form_dirty=False
        self.refresh_code()
        self.draw()
        self.update_title()
        self.status_var.set(f"Updated {node.title}.")
        return True

    def delete_selected(self):
        # Node removal also removes incident wires through the graph model.
        if self.busy:
            return
        if self.selected is None and self.selected_edge is None:
            return
        self.remember()
        if self.selected:
            self.graph.remove_node(self.selected)
        elif self.selected_edge in self.graph.edges:
            self.graph.edges.remove(self.selected_edge)
        self.selected=self.selected_edge=None
        self.form_dirty=False
        self.refresh()
        self.status_var.set("Deleted. Ctrl+Z to undo.")

    def duplicate_selected(self):
        # Copy properties independently but keep the factory's unique name.
        if self.busy or not self.apply_properties():
            return
        old=self.graph.nodes.get(self.selected)
        if not old or old.kind in ("Main", "Function Inputs"):
            return
        before=self.snapshot()
        try:
            node=self.graph.add_node(old.kind,old.x+40,old.y+45)
        except GraphError as exc:
            self.status_var.set(str(exc))
            return
        generated_name=node.props.get("name")
        node.props=copy.deepcopy(old.props)
        if generated_name:
            node.props["name"]=generated_name
        self.remember(before)
        self.selected=node.id
        self.refresh()

    def context_menu(self,event):
        # Offer actions for the most specific object under the pointer.
        if self.busy:
            return
        menu=self.tk.Menu(self.root,tearoff=False,bg=CARD,fg=TEXT,activebackground=LINE)
        pin=self.hit_port(event.x,event.y)
        node_id=self.hit_node(event.x,event.y)
        edge=self.hit_edge(event.x,event.y)
        if pin:
            menu.add_command(label="Disconnect pin",command=lambda:self.disconnect_pin(pin))
        elif node_id:
            if not self.select(node_id):
                return
            menu.add_command(label="Duplicate node",command=self.duplicate_selected,state="disabled" if self.graph.nodes[node_id].kind=="Main" else "normal")
            menu.add_command(label="Delete node",command=self.delete_selected)
        elif edge:
            if not self.select(edge=edge):
                return
            menu.add_command(label="Delete wire",command=self.delete_selected)
        else:
            x,y=self.inverse(event.x,event.y)
            menu.add_command(label="Type Python or search…",command=lambda:self.open_code_at(x,y))
            menu.add_separator()
            for kind in KINDS:
                if not self.function_owner and kind in ("Function Inputs", "Return"):
                    continue
                menu.add_command(label="Add "+kind,command=lambda k=kind:self.add_node(k,x,y))
            menu.add_separator()
            menu.add_command(label="Fit graph",command=self.fit_view)
        menu.tk_popup(event.x_root,event.y_root)

    def open_code_at(self, x, y):
        # Store graph coordinates so later insertion ignores viewport changes.
        self.search_anchor=(x,y)
        self.open_node_picker()

    def disconnect_pin(self,pin):
        # Remove every edge attached to this pin, respecting its direction.
        if self.busy or not self.apply_properties():
            return
        node_id,direction,name=pin
        self.remember()
        self.graph.edges=[e for e in self.graph.edges if not ((direction=="in" and e.target==node_id and e.input==name) or (direction=="out" and e.source==node_id and e.output==name))]
        self.refresh()

    def undo(self):
        # Save the current state for redo before restoring the prior snapshot.
        if self.busy or not self.apply_properties() or not self.undo_stack:
            return
        self.redo_stack.append(self.snapshot())
        self.graph=Graph.from_dict(json.loads(self.undo_stack.pop()))
        self.selected=self.selected_edge=None
        self.refresh()

    def redo(self):
        # Mirror undo, moving the current state back onto the undo stack.
        if self.busy or not self.apply_properties() or not self.redo_stack:
            return
        self.undo_stack.append(self.snapshot())
        self.graph=Graph.from_dict(json.loads(self.redo_stack.pop()))
        self.selected=self.selected_edge=None
        self.refresh()

    def can_leave(self):
        # Shared guard for closing, replacing, or loading a project. Cancellation
        # and failed saves both stop navigation to protect unsaved graph edits.
        from tkinter import messagebox
        if self.busy:
            return False
        if not self.apply_properties():
            return False
        if self.snapshot()==self.saved_state:
            return True
        answer=messagebox.askyesnocancel("Save graph?","Save your changes before continuing?",parent=self.root)
        if answer is None:
            return False
        return self.save_project() if answer else True

    def replace_graph(self,graph,path=None):
        # A newly loaded graph starts fresh history and invalidates old run data.
        self.graph,self.path=graph,Path(path) if path else None
        self.selected=self.selected_edge=None
        self.pending=None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.form_dirty=False
        self.saved_state=self.snapshot()
        self.run_serial+=1
        self.set_text(self.output_text,"Run this graph to see its output.")
        self.refresh()
        self.fit_view()

    def new_project(self):
        # Start with an execution entry point after resolving unsaved changes.
        if self.function_owner:
            return
        if self.can_leave():
            graph=Graph()
            graph.add_node("Main",50,60)
            self.replace_graph(graph)

    def examples_menu(self):
        # Capture factories individually so each menu entry loads its own graph.
        menu=self.tk.Menu(self.root,tearoff=False,bg=CARD,fg=TEXT,activebackground=LINE)
        for slug, title, factory in EXAMPLES:
            menu.add_command(label=title, command=lambda factory=factory: self.load_example(factory))
        menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery())

    def load_example(self,factory):
        # Build a fresh example only after the current project's save guard passes.
        if self.can_leave():
            self.replace_graph(factory())

    def open_project(self):
        # Load successfully before replacing the current graph and saved baseline.
        from tkinter import filedialog,messagebox
        if self.function_owner:
            return
        if not self.can_leave():
            return
        path=filedialog.askopenfilename(parent=self.root,title="Open pydrop graph",filetypes=PROJECT_FILETYPES)
        if path:
            try:
                graph=Graph.load(path)
                self.replace_graph(graph,path)
                self.status_var.set(f"Opened {Path(path).name}.")
            except (GraphError,OSError) as exc:
                messagebox.showerror("Cannot open graph",str(exc),parent=self.root)

    def save_project_as(self):
        # Share validation and persistence with Save, but always prompt for a path.
        return self.save_project(save_as=True)

    def save_project(self, save_as=False):
        # Function windows apply drafts to the owner; the owner's project save
        # is responsible for persisting those definitions to disk.
        from tkinter import filedialog,messagebox
        if self.busy or not self.apply_properties():
            return False
        if self.function_owner:
            owner = self.function_owner
            owner.remember()
            owner.graph.functions[self.function_name]["graph"] = self.graph.to_dict()
            self.saved_state = self.snapshot()
            owner.refresh()
            self.update_title()
            self.status_var.set("Function applied to project. Save the project to keep it on disk.")
            return True
        path=self.path
        if path is None or save_as:
            options = dict(parent=self.root, title="Save graph as" if save_as else "Save graph",
                           defaultextension=".drop.json",
                           initialfile=path.name if path else "my_graph.drop.json",
                           filetypes=PROJECT_FILETYPES)
            if path is not None:
                options["initialdir"] = str(path.resolve().parent)
            name=filedialog.asksaveasfilename(**options)
            if not name:
                return False
            path=Path(name)
        try:
            self.graph.save(path)
            # Advance the clean baseline only after the write succeeds.
            self.path=path
            self.saved_state=self.snapshot()
            self.update_title()
            self.status_var.set(f"Saved {path.name}.")
            return True
        except (OSError,ValueError) as exc:
            messagebox.showerror("Cannot save graph",str(exc),parent=self.root)
            return False

    def export_python(self):
        # Export compiled standalone source after applying inspector changes.
        from tkinter import filedialog,messagebox
        if not self.apply_properties():
            return
        try:
            code=self.compile_project().code
        except GraphError as exc:
            self.show_node_error(exc.node_id, str(exc))
            messagebox.showerror("Cannot export yet",str(exc),parent=self.root)
            return
        path=filedialog.asksaveasfilename(parent=self.root,title="Export standalone Python",defaultextension=".py",initialfile="my_program.py",filetypes=[("Python","*.py")])
        if path:
            try:
                Path(path).write_text(code,encoding="utf-8")
                self.status_var.set(f"Exported {Path(path).name}. Run it with Python.")
            except OSError as exc:
                messagebox.showerror("Cannot export",str(exc),parent=self.root)

    def run_graph(self):
        # Compile with trace instrumentation, then run outside Tk's event loop.
        if self.busy or not self.apply_properties():
            return
        self.set_tab("output")
        try:
            compiled=self.compile_project(trace=True)
        except GraphError as exc:
            self.set_text(self.output_text,"Cannot run: "+str(exc),"error")
            self.show_node_error(exc.node_id, str(exc))
            return
        # Associate every queued response and animation with this run, and clear
        # previews so the inspector cannot show results from the previous graph.
        self.run_serial+=1
        serial=self.run_serial
        self.active_node=None
        self.error_node=None
        self.run_values.clear()
        self.data_previews.clear()
        for window in self.data_windows:
            if window.winfo_exists():
                window.destroy()
        self.data_windows.clear()
        self.update_value_inspector()
        self.busy=True
        self.stop_requested=False
        self.serial_connected=False
        self.serial_status_var.set("Serial: disconnected (waiting for Serial Connect)")
        self.serial_status_label.configure(fg=MUTED)
        self.pending_input=None
        self.input_panel.pack_forget()
        self.run_button.configure(state="disabled",text="Running…")
        self.stop_button.configure(state="normal")
        self.set_text(self.output_text,"Running graph…")
        self.status_var.set("Running generated Python…")
        # Nodes inside reusable functions can also perform slow reads; include
        # those bodies when choosing the CSV allowance for this execution.
        project = self.execution_project()
        scopes = [project] + [Graph.from_dict(definition["graph"], _body=True) for definition in project.functions.values()]
        run_timeout = max(60, self.execution_timeout) if any(n.kind == "CSV Input" for scope in scopes for n in scope.nodes.values()) else self.execution_timeout
        run_timeout += serial_run_allowance(project, project.functions)
        # Background work communicates through queues rather than Tk widgets.
        def worker():
            process=None
            script_path=None
            try:
                flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
                # Embedded data/viewer runtimes can exceed Windows' command-line
                # length limit. Run a temporary source file instead of -c text.
                with tempfile.NamedTemporaryFile("w", suffix=".py", prefix="pydrop-run-", encoding="utf-8", delete=False) as stream:
                    stream.write(compiled.code)
                    script_path = Path(stream.name)
                process=subprocess.Popen(script_command(script_path),
                                         stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                         text=True,encoding="utf-8",errors="replace",creationflags=flags,
                                         env=self.execution_env())
                self.process=process
                # Stop may have been pressed while the child was starting.
                if self.closed or self.stop_requested:
                    process.kill()
                output, errors = [], []
                # This event marks time spent waiting for the user's answer.
                waiting = threading.Event()
                def read_output():
                    for line in process.stdout:
                        output.append(line)
                def read_errors():
                    for line in process.stderr:
                        # Stderr carries structured UI requests alongside errors;
                        # malformed requests remain visible as ordinary diagnostics.
                        if line.startswith("PYDROP_INPUT:"):
                            try:
                                node_id,prompt=json.loads(line.split(":",1)[1])
                            except (ValueError,TypeError):
                                errors.append(line)
                                continue
                            waiting.set()
                            self.input_requests.put((serial,node_id,prompt,process,waiting))
                        elif line.startswith("PYDROP_SERIAL:"):
                            try:
                                payload = json.loads(line.split(":",1)[1])
                                if (not isinstance(payload, list) or len(payload) != 4 or
                                        type(payload[0]) is not bool or not all(isinstance(item, str) for item in payload[1:])):
                                    raise ValueError("Invalid serial status")
                                self.serial_events.put((serial, payload))
                            except (ValueError, TypeError):
                                errors.append(line)
                        else:
                            errors.append(line)
                # Drain both pipes concurrently to avoid blocking the child when
                # either output stream fills. Stderr also carries input requests.
                readers=[threading.Thread(target=read_output,daemon=True), threading.Thread(target=read_errors,daemon=True)]
                for reader in readers:
                    reader.start()
                elapsed=0
                # A monotonic clock keeps system clock changes out of timeout math.
                previous=time.monotonic()
                timed_out=False
                while process.poll() is None:
                    now=time.monotonic()
                    # Time spent waiting for a user's answer does not consume
                    # the graph's execution time allowance.
                    if not waiting.is_set():
                        elapsed+=now-previous
                    previous=now
                    if self.closed or elapsed > run_timeout:
                        timed_out=not self.closed
                        process.kill()
                        break
                    try:
                        process.wait(timeout=.025)
                    except subprocess.TimeoutExpired:
                        pass
                process.wait()
                # Read through EOF before publishing a complete output snapshot.
                for reader in readers:
                    reader.join()
                out,err="".join(output),"".join(errors)
                if timed_out:
                    err+=f"\nExecution stopped after {run_timeout:g} seconds.\n"
                elif self.stop_requested:
                    err+="\nExecution stopped by user. Serial connection closed.\n"
                self.run_queue.put((serial,process.returncode,out,err,compiled.warnings))
            except (OSError,ValueError) as exc:
                self.run_queue.put((serial,1,"",str(exc),compiled.warnings))
            finally:
                # Startup failures, cancellation, and normal exit share cleanup.
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    for stream in (process.stdin,process.stdout,process.stderr):
                        if stream is not None:
                            stream.close()
                if self.process is process:
                    self.process=None
                if script_path is not None:
                    # Windows can remove the temporary source after the child exits.
                    script_path.unlink(missing_ok=True)
        threading.Thread(target=worker,daemon=True).start()

    @staticmethod
    def execution_env():
        import os
        # Copy the environment so per-run settings do not affect the editor.
        env=os.environ.copy()
        # -I ignores PYTHON* settings; -X utf8 guarantees Windows output bytes.
        # Kept for non-CPython environments as well.
        env["PYTHONIOENCODING"]="utf-8"
        return env

    def poll_run(self):
        # Process worker messages on Tk's thread, rescheduling without blocking.
        if self.closed:
            return
        self.poll_csv_columns()
        self.poll_serial()
        try:
            serial,node_id,prompt,process,waiting=self.input_requests.get_nowait()
        except queue.Empty:
            pass
        else:
            # An input request can outlive its process or belong to an older run.
            if serial==self.run_serial and self.busy and process.poll() is None:
                self.pending_input=(process,waiting)
                self.input_prompt.set(prompt)
                self.input_entry.delete(0,"end")
                self.set_tab("output")
                self.input_panel.pack(side="bottom",fill="x",before=self.output_text)
                self.input_entry.focus_set()
                self.active_node=self.visible_runtime_id(node_id)
                self.status_var.set("Waiting for your answer. Enter text and press Send, or Cancel to stop.")
                self.draw()
        try:
            serial,returncode,out,err,warnings=self.run_queue.get_nowait()
        except queue.Empty:
            self.root.after(70,self.poll_run)
            return
        if serial != self.run_serial:
            # Discard stale completion messages while keeping the polling loop alive.
            self.root.after(70,self.poll_run)
            return
        # The readers finish before completion is queued. Drain their final
        # status events now so a fast run cannot replay Connected after exit.
        self.poll_serial()
        self.busy=False
        stopped = self.stop_requested
        self.stop_button.configure(state="disabled")
        if stopped or self.serial_connected:
            self.serial_connected=False
            self.serial_status_var.set("Serial: disconnected (stopped; port released)" if stopped else
                                       "Serial: disconnected (run ended; port released)")
            self.serial_status_label.configure(fg=MUTED)
        self.pending_input=None
        self.input_panel.pack_forget()
        self.run_button.configure(state="normal",text="▶  Run graph")
        if serial==self.run_serial:
            trace=[]
            errors=[]
            failed_node=None
            # Separate the generated runtime's protocol markers from actual
            # errors before displaying output and animating the executed path.
            for line in err.splitlines():
                if line.startswith("NODEFORGE_TRACE:"):
                    # Runtime IDs may name nodes inside a function; map them to
                    # the node this editor can actually highlight.
                    node_id = self.visible_runtime_id(line.split(":",1)[1])
                    if node_id in self.graph.nodes:
                        trace.append(node_id)
                elif line.startswith("PYDROP_ERROR:"):
                    node_id = self.visible_runtime_id(line.split(":",1)[1])
                    if node_id in self.graph.nodes:
                        failed_node=node_id
                elif line.startswith("PYDROP_DATA:"):
                    try:
                        runtime_id, payload = json.loads(line.split(":",1)[1])
                        node_id = self.visible_runtime_id(runtime_id)
                        # A function's viewer still belongs to this running
                        # project even when its internal nodes are off-canvas.
                        if node_id not in self.graph.nodes and "/" in runtime_id and payload.get("kind") != "columns":
                            scope, scoped_id = runtime_id.split("/", 1)
                            definition = self.function_library().get(scope)
                            source_node = next((item for item in definition["graph"]["nodes"] if item["id"] == scoped_id), None) if definition else None
                            if source_node and source_node["kind"] in ("Data Table", "Line Plot", "Scatter Plot"):
                                from data_ui import _data_display
                                self.data_windows.append(_data_display(payload, parent=self.root))
                        if node_id in self.graph.nodes:
                            self.data_previews[node_id] = payload
                            if payload.get("kind") == "columns":
                                # Reuse headers discovered during execution in the
                                # inspector without starting another file read.
                                node = self.graph.nodes[node_id]
                                path = str(self.csv_path(node.props["path"]))
                                self.column_cache[(path, node.props["delimiter"])] = payload["columns"]
                            elif self.graph.nodes[node_id].kind != "CSV Input":
                                self.inspect_data(node_id)
                    except (ValueError, TypeError, KeyError) as exc:
                        errors.append("Cannot display data preview: " + str(exc))
                elif line.startswith("PYDROP_VALUE:"):
                    try:
                        node_id,value_type,value=json.loads(line.split(":",1)[1])
                        node_id = self.visible_runtime_id(node_id)
                        if node_id in self.graph.nodes:
                            self.run_values[node_id]={"type":value_type,"value":value}
                    except (ValueError,TypeError):
                        errors.append(line)
                else:
                    errors.append(line)
            for node_id in trace:
                # Execution-only nodes still get an inspector result even when
                # the runtime emitted no computed value for them.
                self.run_values.setdefault(node_id,{"type":"Execution","value":"Executed"})
            self.update_value_inspector()
            if self.selected in self.data_previews and not self.form_dirty:
                # Rebuild the selected node's column choices and inspection action.
                self.show_inspector()
            if returncode and not stopped:
                # Prefer an explicit error marker; the last traced node is a
                # fallback when the child failed before it could emit one.
                failed_node=failed_node or (trace[-1] if trace else None)
                advice=self.runtime_advice(err)
                node=self.graph.nodes.get(failed_node)
                errors.insert(0, f"{node.title} ({node.id}): {advice}" if node else advice)
                self.show_node_error(failed_node, advice)
            text=out or ("(No printed output.)\n" if returncode==0 else "")
            if errors:
                text+="\n"+"\n".join(errors)+"\n"
            text+="\n"+("Execution stopped." if stopped else "Finished successfully." if returncode==0 else "Execution failed. Check the error above.")
            if warnings:
                text+="\n\nGraph notices:\n"+"\n".join("• "+w for w in warnings)
            self.set_text(self.output_text,text,"error" if returncode and not stopped else None)
            self.last_run_status = "Run stopped. Serial connection closed." if stopped else "Run complete." if returncode==0 else self.status_var.get()
            self.status_var.set("Run complete. Highlighting the executed path." if returncode==0 and not stopped else self.last_run_status)
            self.animate_trace(trace,serial)
        self.root.after(70,self.poll_run)

    def submit_input(self, cancel=False):
        # JSON preserves the answer as one protocol line; EOF signals cancel.
        if self.pending_input is None:
            return
        process,waiting=self.pending_input
        self.pending_input=None
        self.input_panel.pack_forget()
        # Resume timeout accounting before waking the child with an answer or EOF.
        waiting.clear()
        try:
            if cancel:
                process.stdin.close()
            else:
                process.stdin.write(json.dumps(self.input_entry.get())+"\n")
                # Flush immediately: the child is blocked waiting for this line.
                process.stdin.flush()
        except (OSError,ValueError):
            pass
        self.canvas.focus_set()
        self.status_var.set("Input cancelled." if cancel else "Running generated Python…")

    def update_value_inspector(self):
        # Values describe the last run only and are cleared when code changes.
        value = self.run_values.get(self.selected)
        self.node_value_var.set(f"{value['type']}: {value['value']}" if value else "Not evaluated. Run the graph to inspect values.")

    def show_node_error(self, node_id, message):
        # Select the failing node and reveal its properties alongside the error.
        self.error_node=node_id if node_id in self.graph.nodes else None
        if self.error_node:
            self.selected=self.error_node
            self.selected_edge=None
            self.show_inspector()
            node=self.graph.nodes[self.error_node]
            # Center the problem node even when it was outside the viewport.
            self.offset=[self.canvas.winfo_width()/2-(node.x+NODE_W/2)*self.zoom,
                         self.canvas.winfo_height()/2-(node.y+node_height(node)/2)*self.zoom]
        self.status_var.set(message)
        self.draw()

    @staticmethod
    def runtime_advice(error):
        # Translate recognizable runtime failures into actionable editor hints.
        # Domain runtimes supply their own messages after these exception names.
        if "DataError:" in error:
            return error.rsplit("DataError:", 1)[1].strip()
        if "SerialError:" in error:
            return error.rsplit("SerialError:", 1)[1].strip()
        if "UnitError:" in error:
            return error.rsplit("UnitError:", 1)[1].strip()
        if "Boolean Logic inputs" in error:
            return "Boolean Logic needs True/False values. Connect Compare results, Boolean variables, or Convert set to Boolean."
        if "minimum cannot exceed maximum" in error:
            return "Minimum is greater than maximum. Swap the bounds or correct their connected values."
        if "Random Integer bounds" in error:
            return "Random Integer needs whole-number bounds. Use Decimal mode for fractional bounds."
        if "Input cancelled" in error:
            return "Input cancelled. Run again when you are ready to answer."
        if "Convert to Number:" in error:
            return "Cannot convert this value to a number. Enter numeric text such as 12.5 and check the connected input."
        if "Convert to Boolean:" in error:
            return "Cannot convert this text to a boolean. Enter True or False."
        if "ZeroDivisionError" in error:
            return "Division by zero. Check the divisor input or change the formula to handle zero."
        if "TypeError" in error:
            return "Incompatible input types. Check connected values; numeric nodes need numbers."
        if "math domain error" in error:
            return "Value outside the formula's domain. Use nonnegative inputs for sqrt and positive inputs for log."
        if "OverflowError" in error or "non-finite" in error:
            return "Numeric result is too large. Reduce the inputs or adjust the formula."
        if "Execution stopped" in error:
            return next((line.strip() for line in reversed(error.splitlines()) if "Execution stopped" in line), "Execution timed out.")
        return "Check this node's formula, input values, and connected nodes. See the error below for details."

    def animate_trace(self,trace,serial):
        # Serial checks stop an old animation after a new run or graph refresh.
        if serial!=self.run_serial or self.closed:
            return
        self.active_node=trace[0] if trace else None
        self.draw()
        if trace:
            # Tk schedules each highlight so normal editing events remain responsive.
            self.root.after(280,lambda:self.animate_trace(trace[1:],serial))
        else:
            self.status_var.set(self.last_run_status)

    def show_help(self):
        # Keep the quick guide local to this window, including function editors.
        from tkinter import messagebox
        messagebox.showinfo("pydrop quick guide",
            "1. Drag a node from the palette onto the canvas. Click a palette item to add it at the center.\n\n"
            "2. Click or drag from an output pin to an input pin. White diamonds control execution. Green circles carry values.\n\n"
            "3. Select a node, edit the inspector, and press Enter or Apply changes. Connected inputs override defaults.\n\n"
            "4. Run with F5. Export .py creates an independent Python program. Save stores an editable graph.\n\n"
            "Navigate: drag empty space or middle-drag to pan; wheel to zoom; F to fit.\n"
            "Type to add: click empty canvas and type a node name or a supported Python statement, such as print(\"Hello\") or speed = 10, then Enter. Esc cancels.\n"
            "Script opens multi-line entry: Preview, choose Disconnected/After Main/After selected, then Add group. Expression assignments create named Assign nodes. After running, select a node to see its Last run value.\n"
            "Functions > New function creates a reusable graph with named inputs and returns. Add a Function Call and double-click it to edit the body. Apply function updates the project; save the project to keep it. Every execution path must end at Return.\n"
            "Edit: Delete removes; Ctrl+D duplicates; Ctrl+Z / Ctrl+Y undo and redo. Right-click pins or wires to disconnect.\n\n"
            "Input nodes ask questions in the Output panel. Convert changes text to numbers or booleans; Text Join combines values into messages. Try Examples > Interactive calculator.\n\n"
            "Engineering: Quantity attaches units to a number; Unit Convert changes compatible units. Force, Torque, Ohm's Law, and Electrical Power require matching input dimensions. See the Engineering examples. Math and formulas retain units; ordinary unitless numbers still work.\n\n"
            "Boolean Logic combines True/False values with AND, OR, or NOT (NOT ignores b). Clamp keeps numbers within bounds. Random needs white execution wires; leave Seed blank for fresh draws or use an integer seed to repeat results.\n\n"
             "Data: CSV Input reads a headered UTF-8 file. Browse, then Read column names to choose x/y. Math works on columns element by element. Data Table can add its values wire as a named column. Line Plot and Scatter Plot let you select columns and label axes. CSV Output writes the full table; choose Overwrite explicitly to replace an existing file. Tables and plots open inside the app after Run. Try Experimental measurements.\n\n"
             "Arduino / Serial: run Connect before Send or Read Line. Choose Simulation to work without hardware, or Hardware with a port and matching baud rate. Refresh ports discovers devices. Read Line reports received=False on timeout; branch before Parse Value. The connection indicator updates during Run. Stop ends a waiting run; connections close when the run ends. Try Sensor dashboard. See SERIAL_GUIDE.md for the matching Arduino sketch.\n\n"
            "0.1 scope: formulas, constants, text, input, calculations, branches, and output. No loops, arbitrary Python imports, or conversion of existing Python files yet.",parent=self.root)

    def close(self):
        # Stop active execution, resolve unsaved edits, and restore the parent
        # function window's modal grab when unwinding nested editors.
        self.close_node_picker()
        self.hide_tooltip()
        if self.busy:
            # Closing stops the bounded child process; ask about graph edits
            # after clearing busy so the ordinary save guard can run.
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.busy=False
        if not self.can_leave():
            return
        self.closed=True
        self.run_serial+=1
        if self.function_owner:
            parent = self.function_parent
            parent.function_editor = None
            # Cancel callbacks registered by this child window before its Tcl
            # commands disappear; callbacks owned by other windows remain active.
            commands = set(self.root._tclCommands or ())
            for timer in self.root.tk.call("after", "info"):
                script = str(self.root.tk.call("after", "info", timer)[0])
                if script.split()[0] in commands:
                    self.root.after_cancel(timer)
            self.root.grab_release()
            if parent.function_owner and not parent.closed:
                parent.root.grab_set()
        self.root.destroy()


def main():
    # The same graph loader supports GUI editing and headless export/execution.
    parser=argparse.ArgumentParser(description="pydrop — visual Python programming")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("project",nargs="?",help="A .drop.json / .nodeforge.json graph or .drop source file to open")
    parser.add_argument("--export",metavar="FILE.py",help="Export the graph to Python without opening the GUI")
    parser.add_argument("--run",action="store_true",help="Run the graph without opening the GUI")
    args=parser.parse_args()
    try:
        drop_source = bool(args.project and Path(args.project).suffix.lower() == ".drop")
        # Drop text is translated into an editable graph before either mode runs.
        if drop_source:
            source_path = Path(args.project)
            # Reject oversized files before reading and translating their contents.
            if source_path.stat().st_size > 50000:
                raise GraphError("Drop source must contain at most 12,000 characters.")
            plan = translate_drop(source_path.read_text(encoding="utf-8-sig"),Graph(),40,40)
            graph = connect_drop(plan,"After Main").graph
            if not any(n.kind == "Main" for n in graph.nodes.values()):
                graph.add_node("Main",-290,40)
        else:
            graph=Graph.load(args.project) if args.project else demo_graph()
        if args.export or args.run:
            code=compile_graph(graph).code
            if args.export:
                Path(args.export).write_text(code,encoding="utf-8")
                print(f"Exported {args.export}")
            if args.run:
                # Resolve relative data files against the opened project's folder.
                code=compile_graph(graph, data_base=str(Path(args.project).resolve().parent) if args.project else str(project_directory())).code
                # Console input belongs to the terminal; allow time to answer.
                interactive=any(n.kind=="Input" for n in graph.nodes.values()) or any(
                    n["kind"] == "Input" for definition in graph.functions.values() for n in definition["graph"]["nodes"])
                kinds = {n.kind for n in graph.nodes.values()} | {n["kind"] for definition in graph.functions.values() for n in definition["graph"]["nodes"]}
                # Viewer windows also need an open-ended wait until the user closes them.
                interactive = interactive or bool(kinds & {"Data Table", "Line Plot", "Scatter Plot"})
                with tempfile.TemporaryDirectory(prefix="pydrop-run-") as directory:
                    script = Path(directory) / "program.py"
                    script.write_text(code, encoding="utf-8")
                    run_timeout = (60 if "CSV Input" in kinds else 5) + serial_run_allowance(graph, graph.functions)
                    result=subprocess.run(script_command(script, unbuffered=False),timeout=None if interactive else run_timeout)
                return result.returncode
            return 0
        # Import Tk only for GUI mode so headless commands need no display.
        import tkinter as tk
        configure_windows_identity()
        root=tk.Tk()
        # Imported source has no JSON save path until the user saves a graph.
        Editor(root,graph,None if drop_source else args.project)
        root.mainloop()
        return 0
    except (GraphError,OSError,UnicodeError,subprocess.TimeoutExpired) as exc:
        # Windowed Windows launches may have no console stream for startup errors.
        if sys.stderr is None:
            from tkinter import messagebox
            messagebox.showerror(APP_NAME + " could not start", str(exc))
        else:
            print(f"{APP_NAME}: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    # Propagate CLI failures and generated-program exit codes to the invoking shell.
    raise SystemExit(main())
