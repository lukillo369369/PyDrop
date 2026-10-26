"""Library review, explicit installation, and Python-node authoring UI."""
import json
from pathlib import Path

from node_libraries import (LIBRARY_FORMAT, LibraryCatalog, LibraryError, content_digest,
                            dependency_issues, install_library, load_library, save_library, validate_library)


def open_library_manager(editor):
    existing = editor.library_manager
    if existing is not None and existing.window.winfo_exists():
        existing.window.lift()
        return existing
    editor.library_manager = LibraryManager(editor)
    return editor.library_manager


class LibraryManager:
    def __init__(self, editor):
        self.editor = editor
        tk, ttk = editor.tk, editor.ttk
        panel, card, text, accent = "#131b29", "#1b2738", "#edf3fc", "#f5c578"
        self.window = tk.Toplevel(editor.root)
        self.window.title("Node libraries — Executable Python")
        self.window.geometry("980x820")
        self.window.minsize(800, 700)
        self.window.configure(bg=panel)
        self.preview, self.preview_path = None, None
        editor.label(self.window, "Node libraries · EXECUTABLE PYTHON", fg=accent,
                     font=(editor.ui_font,16,"bold"), padx=18, pady=12).pack(fill="x")
        editor.label(self.window, "Installed implementations run with your permissions when a graph reaches the node.\nBrowsing, saving, and opening projects do not execute library code. Review the code before installing.",
                     fg=text, padx=18, pady=5, justify="left").pack(fill="x")
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=14, pady=12)
        self.notebook = notebook
        browse_tab, author_tab, installed_tab = [tk.Frame(notebook, bg=panel, padx=12, pady=12) for _ in range(3)]
        for tab, label in ((browse_tab, "Browse and install"), (author_tab, "Author a library"), (installed_tab, "Installed and project dependencies")):
            notebook.add(tab, text=label)

        row = tk.Frame(browse_tab, bg=panel)
        row.pack(fill="x")
        editor.button(row, "Browse library file…", self.browse).pack(side="left")
        self.install_button = editor.button(row, "Install executable Python", self.install_preview, accent=True)
        self.install_button.pack(side="right")
        self.install_button.configure(state="disabled")
        self.preview_status = tk.StringVar(value="Select a .pydrop-lib.json file to review. Nothing is installed by browsing.")
        editor.label(browse_tab, textvariable=self.preview_status, fg=accent, wraplength=870, justify="left", pady=10).pack(fill="x")
        self.preview_text = self._text(browse_tab, height=22)
        self.preview_text.configure(state="disabled")

        # Keep every authoring control reachable on smaller desktop displays.
        author_scroll = ttk.Scrollbar(author_tab, orient="vertical")
        author_scroll.pack(side="right", fill="y")
        self.author_canvas = tk.Canvas(author_tab, bg=panel, highlightthickness=0,
                                       yscrollcommand=author_scroll.set)
        self.author_canvas.pack(fill="both", expand=True)
        author_scroll.configure(command=self.author_canvas.yview)
        author_body = tk.Frame(self.author_canvas, bg=panel)
        author_window = self.author_canvas.create_window(0, 0, anchor="nw", window=author_body)
        author_body.bind("<Configure>", lambda event: self.author_canvas.configure(scrollregion=self.author_canvas.bbox("all")))
        self.author_canvas.bind("<Configure>", lambda event: self.author_canvas.itemconfigure(author_window, width=event.width))
        author_tab = author_body
        self.fields = {}
        metadata = tk.Frame(author_tab, bg=panel)
        metadata.pack(fill="x")
        entries = (("library_id", "Library ID", "my-calculations"), ("library_name", "Library name", "My calculations"),
                   ("library_version", "Library version", "1.0.0"), ("definition_id", "Definition ID", "scale-message"),
                   ("definition_version", "Definition version", "1.0.0"), ("name", "Node name", "Scale and describe"),
                   ("category", "Palette category", "Calculations"), ("description", "Description", "Scale a number and describe the result."))
        for index, (key, label, default) in enumerate(entries):
            row_index, column = divmod(index, 2)
            group = tk.Frame(metadata, bg=panel)
            group.grid(row=row_index, column=column, sticky="ew", padx=(0,10), pady=3)
            editor.label(group, label, font=(editor.ui_font,9)).pack(anchor="w")
            var = tk.StringVar(value=default)
            self.fields[key] = var
            tk.Entry(group, textvariable=var, bg=card, fg=text, insertbackground=text,
                     relief="flat", font=(editor.code_font,10)).pack(fill="x", ipady=4)
        metadata.columnconfigure(0, weight=1)
        metadata.columnconfigure(1, weight=1)
        ports_row = tk.Frame(author_tab, bg=panel)
        ports_row.pack(fill="x", pady=(8,0))
        self.port_editors = {}
        for side in ("inputs", "outputs"):
            group = tk.Frame(ports_row, bg=panel)
            group.pack(side="left", fill="both", expand=True, padx=(0,8))
            editor.label(group, side.title() + " (JSON; name/type/default/unit)", font=(editor.ui_font,9)).pack(anchor="w")
            widget = self._text(group, height=5, expand=False)
            initial = ([{"name":"value", "type":"Number", "default":"3"}, {"name":"factor", "type":"Number", "default":"2"}]
                       if side == "inputs" else [{"name":"scaled", "type":"Number"}, {"name":"message", "type":"Text"}])
            widget.insert("1.0", json.dumps(initial, indent=2))
            self.port_editors[side] = widget
        editor.label(author_tab, "Types: Number, Text, Boolean, Quantity (requires unit), Any. Defaults are strings; output ports have no default.",
                     font=(editor.ui_font,9), wraplength=870, pady=5).pack(fill="x")
        editor.label(author_tab, 'Dependencies (JSON): [{"module":"numpy", "package":"numpy", "version":"2.0.0"}] or []',
                     font=(editor.ui_font,9)).pack(anchor="w")
        self.requirements = self._text(author_tab, height=2, expand=False)
        self.requirements.insert("1.0", "[]")
        editor.label(author_tab, "Python function body · inputs are arguments; return a dict with exactly the named outputs.",
                     font=(editor.ui_font,9), pady=6).pack(anchor="w")
        self.implementation = self._text(author_tab, height=7)
        self.implementation.insert("1.0", 'scaled = value * factor\nreturn {"scaled": scaled, "message": f"Scaled: {scaled:g}"}\n')
        self.author_status = tk.StringVar(value="Saving creates a reusable file only. Review and install it separately before running.")
        editor.label(author_tab, textvariable=self.author_status, fg=accent, wraplength=870, justify="left", pady=6).pack(fill="x")
        self.save_button = editor.button(author_tab, "Save library file…", self.save_authored, accent=True)
        self.save_button.pack(anchor="e")

        editor.button(installed_tab, "Refresh installed libraries", self.refresh_installed).pack(anchor="w")
        editor.label(installed_tab, "Store: " + str(editor.library_catalog.store), wraplength=870, font=(editor.ui_font,9), pady=8).pack(fill="x")
        self.installed_text = self._text(installed_tab, height=20)
        self.refresh_installed()

    def _text(self, parent, height, expand=True):
        editor = self.editor
        frame = editor.tk.Frame(parent, bg="#131b29")
        frame.pack(fill="both" if expand else "x", expand=expand)
        widget = editor.tk.Text(frame, height=height, width=30, bg="#0c111b", fg="#edf3fc",
                                insertbackground="#66e0bb", wrap="none", undo=True,
                                font=(editor.code_font,10), padx=7, pady=6)
        vertical = editor.ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        horizontal = editor.ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        widget.pack(fill="both", expand=True)
        return widget

    def browse(self, path=None):
        from tkinter import filedialog
        if path is None:
            path = filedialog.askopenfilename(parent=self.window, title="Review executable Python node library",
                                              filetypes=[("PyDrop executable Python libraries", "*.pydrop-lib.json"), ("JSON", "*.json")])
        if not path:
            return
        self.preview, self.preview_path = None, None
        self.install_button.configure(state="disabled")
        try:
            library = load_library(path)
            issues = dependency_issues(library)
            self.preview, self.preview_path = library, Path(path)
            lines = ["EXECUTABLE PYTHON — install only code you trust.",
                     f"{library['name']} ({library['id']}@{library['version']})", "Fingerprint: " + content_digest(library),
                     "Dependencies: " + (" | ".join(issues) if issues else "Declared and static-import dependencies are available."), ""]
            for node in library["nodes"]:
                lines += [f"{node['name']} / {node['category']} / {node['id']}@{node['version']}", node["description"],
                          "Inputs: " + json.dumps(node["inputs"]), "Outputs: " + json.dumps(node["outputs"]),
                          "Requirements: " + json.dumps(library["requirements"] + node["requirements"]),
                          "Python body:", node["implementation"], ""]
            self.editor.set_text(self.preview_text, "\n".join(lines))
            self.preview_status.set("Review all definitions. Install permits these implementations to run when reached in a graph; installation itself does not run them.")
            self.install_button.configure(state="normal")
        except (LibraryError, OSError) as exc:
            self.preview_status.set(str(exc))
            self.editor.set_text(self.preview_text, "This file was not installed.\n" + str(exc))

    def install_preview(self):
        if self.preview is None or self.editor.busy:
            return
        try:
            install_library(self.preview_path, self.editor.library_catalog.store, allow_executable=True,
                            expected_digest=content_digest(self.preview))
            self.refresh_installed()
            self.preview_status.set("Installed executable Python library. Its categorized nodes are now searchable in the palette. No library code was executed during installation.")
        except (LibraryError, OSError) as exc:
            self.preview_status.set(str(exc))

    def author_data(self):
        values = {key: variable.get().strip() for key, variable in self.fields.items()}
        try:
            ports = {side: json.loads(widget.get("1.0", "end-1c")) for side, widget in self.port_editors.items()}
            requirements = json.loads(self.requirements.get("1.0", "end-1c"))
        except (ValueError, RecursionError) as exc:
            raise LibraryError("Invalid port or dependency JSON: " + str(exc)) from None
        return validate_library({"format": LIBRARY_FORMAT, "schema_version": 1,
                                 "id": values["library_id"], "name": values["library_name"], "version": values["library_version"],
                                 "requirements": requirements, "nodes": [{"id": values["definition_id"], "version": values["definition_version"],
                                 "name": values["name"], "category": values["category"], "description": values["description"],
                                 **ports, "implementation": self.implementation.get("1.0", "end-1c")}]})

    def save_authored(self, path=None):
        from tkinter import filedialog
        try:
            data = self.author_data()
            if path is None:
                path = filedialog.asksaveasfilename(parent=self.window, title="Save executable Python library (does not install)",
                                                    defaultextension=".pydrop-lib.json", initialfile=data["id"] + ".pydrop-lib.json",
                                                    filetypes=[("PyDrop executable Python libraries", "*.pydrop-lib.json")])
            if not path:
                return
            save_library(path, data)
            self.author_status.set(f"Saved {Path(path).name}. Browse and review it, then click Install executable Python to enable its nodes.")
        except (LibraryError, OSError) as exc:
            self.author_status.set(str(exc))

    def refresh_installed(self):
        self.editor.reload_libraries()
        catalog = LibraryCatalog(self.editor.library_catalog.store)
        lines = ["Only explicitly registered files are installed; this view does not execute code.", ""]
        for library in catalog.libraries.values():
            lines.append(f"EXECUTABLE PYTHON: {library['name']} ({library['id']}@{library['version']})")
            lines += [f"  {node['category']} / {node['name']} — {node['id']}@{node['version']}" for node in library["nodes"]]
            lines += ["  " + issue for issue in dependency_issues(library)]
        issues = catalog.issues + self.editor.graph.dependency_issues(catalog)
        lines += ["", "Project / registry issues:", *(issues or ["None."])]
        self.editor.set_text(self.installed_text, "\n".join(lines))
