"""A background executable builder for the current editable project."""
from pathlib import Path
import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import uuid

from app_runtime import (external_python_environment, frozen, project_directory,
                         resolve_build_python, resource_path)


class PackagingUI:
    def open_executable_builder(self):
        from tkinter import filedialog
        if self.busy or not self.apply_properties():
            return
        existing = getattr(self, "packaging_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        tk = self.tk
        window = tk.Toplevel(self.root)
        self.packaging_window = window
        window.title("Build a graph executable")
        window.geometry("680x530")
        window.minsize(570, 460)
        window.configure(bg="#131b29")
        window.transient(self.root)
        panel = tk.Frame(window, bg="#131b29", padx=18, pady=14)
        panel.pack(fill="both", expand=True)
        self.label(panel, "Package this graph", font=(self.ui_font, 15, "bold")).pack(anchor="w")
        self.label(panel, "Build a console program with its own Python runtime. Editable project and Python source copies are included.\nChoose a build environment with PyInstaller and this graph's dependencies installed.",
                   wraplength=620, justify="left", fg="#94a7c2").pack(fill="x", pady=(6,12))
        python_var = tk.StringVar(value="" if frozen() else str(Path(sys.executable).with_name("python.exe") if sys.platform == "win32" else sys.executable))
        name_var = tk.StringVar(value="MyGraph")
        output_var = tk.StringVar(value=str(project_directory() / "executables"))
        self.packaging_vars = {"python": python_var, "name": name_var, "output": output_var}
        for title, variable, browse in (
            ("Python build interpreter", python_var, lambda: filedialog.askopenfilename(parent=window, title="Select Python build interpreter", filetypes=[("Python executable", "python*.exe"), ("All files", "*.*")])),
            ("Executable name", name_var, None),
            ("Output folder", output_var, lambda: filedialog.askdirectory(parent=window, title="Choose build output folder")),
        ):
            self.label(panel, title, fg="#94a7c2").pack(anchor="w", pady=(6,3))
            row = tk.Frame(panel, bg="#131b29")
            row.pack(fill="x")
            entry = tk.Entry(row, textvariable=variable, bg="#1b2738", fg="#edf3fc", insertbackground="#66e0bb", relief="flat")
            entry.pack(side="left", fill="x", expand=True, ipady=5)
            if browse:
                def choose(variable=variable, browse=browse):
                    selected = browse()
                    if selected:
                        variable.set(selected)
                self.button(row, "Browse", choose, pady=4).pack(side="right", padx=(6,0))
        self.label(panel, "Use the command-line builder for extra modules, bundled CSV files, or one-folder output. See BUILDING.md.",
                   wraplength=620, justify="left", fg="#94a7c2", font=(self.ui_font,9)).pack(fill="x", pady=(10,6))
        result = tk.Text(panel, height=6, bg="#0c111b", fg="#edf3fc", wrap="word", relief="flat")
        result.pack(fill="both", expand=True, pady=6)
        result.insert("end", "Dependency report checks imports without running your graph. Building can take a minute.\n")
        result.configure(state="disabled")
        self.packaging_result = result
        buttons = tk.Frame(panel, bg="#131b29")
        buttons.pack(fill="x", pady=(5,0))
        self.packaging_buttons = [
            self.button(buttons, "Dependency report", lambda: self.start_executable_build(True)),
            self.button(buttons, "Build executable", lambda: self.start_executable_build(False), accent=True),
        ]
        for button in self.packaging_buttons:
            button.pack(side="left", padx=(0,6))
        self.label(buttons, "Your original project stays editable.", fg="#94a7c2", font=(self.ui_font,8)).pack(side="right")

    def start_executable_build(self, report_only=False):
        window = self.packaging_window
        result = self.packaging_result
        def show(text):
            result.configure(state="normal")
            result.delete("1.0", "end")
            result.insert("end", text)
            result.configure(state="disabled")
        try:
            python = resolve_build_python(self.packaging_vars["python"].get())
            name = self.packaging_vars["name"].get().strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", name):
                raise ValueError("Choose an executable name with 1-64 letters, digits, spaces, underscores, or hyphens.")
            output = self.packaging_vars["output"].get().strip()
            if not output:
                raise ValueError("Choose an output folder.")
            output = Path(output).expanduser().resolve()
            code = self.compile_project().code
            project = self.execution_project().to_dict()
            from node_libraries import DEFAULT_LIBRARY_STORE
            library_store = getattr(getattr(self, "library_catalog", None), "store", DEFAULT_LIBRARY_STORE)
            if report_only:
                output = output / "dependency-reports"
                name += "-" + uuid.uuid4().hex[:8]
        except (ValueError, OSError) as exc:
            show(str(exc))
            return
        completed = queue.Queue()
        for button in self.packaging_buttons:
            button.configure(state="disabled")
        show("Checking dependencies..." if report_only else "Building executable in the background...\nOutput: " + str(output))
        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="pydrop-executable-") as directory:
                    source = Path(directory) / "program.py"
                    editable = Path(directory) / "project.drop.json"
                    source.write_text(code, encoding="utf-8")
                    editable.write_text(json.dumps(project, indent=2), encoding="utf-8")
                    command = [str(python), "-I", str(resource_path("tools/build_graph.py")), str(source),
                               "--project", str(editable), "--name", name, "--output-dir", str(output)]
                    command += ["--library-store", str(library_store)]
                    if report_only:
                        command.append("--report-only")
                    # PyInstaller's inherited DLL directory must not shadow the
                    # external build interpreter's own Python DLL on Windows.
                    kernel = None
                    if frozen() and sys.platform == "win32":
                        import ctypes
                        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                        kernel.SetDllDirectoryW(None)
                    try:
                        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                   text=True, encoding="utf-8", errors="replace",
                                                   env=external_python_environment(),
                                                   creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                    finally:
                        if kernel:
                            kernel.SetDllDirectoryW(str(resource_path("")))
                    stdout, stderr = process.communicate()
                    completed.put((process.returncode, stdout + ("\n" + stderr if stderr else "")))
            except (OSError, ValueError) as exc:
                completed.put((1, str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        def poll():
            if self.closed or not window.winfo_exists():
                return
            try:
                status, message = completed.get_nowait()
            except queue.Empty:
                window.after(100, poll)
                return
            for button in self.packaging_buttons:
                button.configure(state="normal")
            show(("Completed.\n" if status == 0 else "Build needs attention.\n") + message[-18000:])
            self.status_var.set("Dependency report ready." if status == 0 and report_only else
                                "Graph executable built." if status == 0 else "Executable build failed. See the build window for the log and next steps.")
        window.after(100, poll)
