"""Static dependency reports and opt-in PyInstaller builds of exported graphs.

Analysis never imports or executes the program being packaged. PyInstaller runs
only when a build is explicitly requested. Editable inputs are copied alongside
the result and are never changed or replaced by the executable.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.machinery
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize


REPORT_FORMAT = "pydrop-executable-dependencies"
MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")


class BuildError(ValueError):
    """A build failure with locations of retained diagnostic files."""

    def __init__(self, message, *, log_path=None, report_path=None):
        super().__init__(message)
        self.log_path = str(log_path) if log_path else None
        self.report_path = str(report_path) if report_path else None


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _module_available(name, source_dir):
    """Find specs without importing package parents or the target program."""
    search = [str(source_dir), *sys.path]
    for index, _ in enumerate(name.split(".")):
        fullname = ".".join(name.split(".")[:index + 1])
        try:
            spec = None
            if index == 0:
                spec = (importlib.machinery.BuiltinImporter.find_spec(fullname)
                        or importlib.machinery.FrozenImporter.find_spec(fullname))
            spec = spec or importlib.machinery.PathFinder.find_spec(fullname, search)
        except (ImportError, AttributeError, ValueError, OSError):
            return False
        if spec is None:
            return False
        if index < len(name.split(".")) - 1:
            search = spec.submodule_search_locations
            if search is None:
                return False
    return True


class _ImportScan(ast.NodeVisitor):
    def __init__(self, tree):
        self.importlib_names = {"importlib"}
        self.import_module_names = set()
        self.imports = []
        self.unknown = []
        self.functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        self.importlib_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
                self.import_module_names.update(alias.asname or alias.name for alias in node.names
                                                if alias.name == "import_module")

    def _add(self, name, line, kind):
        if name:
            self.imports.append({"module": name, "line": line, "kind": kind,
                                 "function": self.functions[-1] if self.functions else None})

    def visit_Import(self, node):
        for alias in node.names:
            self._add(alias.name, node.lineno, "static")

    def visit_ImportFrom(self, node):
        if node.level:
            self.unknown.append(f"Relative import on line {node.lineno} needs its package context; package an exported self-contained script instead.")
        else:
            self._add(node.module, node.lineno, "static")

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        func = node.func
        dynamic = (isinstance(func, ast.Name) and func.id in self.import_module_names | {"__import__"})
        dynamic = dynamic or (isinstance(func, ast.Attribute) and func.attr == "import_module"
                              and isinstance(func.value, ast.Name) and func.value.id in self.importlib_names)
        if dynamic:
            value = node.args[0] if node.args else next((part.value for part in node.keywords if part.arg == "name"), None)
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and MODULE_NAME.fullmatch(value.value):
                self._add(value.value, node.lineno, "dynamic-literal")
            else:
                self.unknown.append(f"Dynamic import on line {node.lineno} cannot be resolved statically. Supply --hidden-import MODULE and check its package data.")
        self.generic_visit(node)


def _graph_metadata(graph, catalog):
    """Return only data from reviewed installed-library declarations."""
    from graph_core import Graph
    result, warnings = [], []
    scopes = [graph, *[Graph.from_dict(item["graph"], _body=True) for item in graph.functions.values()]]
    for current in scopes:
        for node in current.nodes.values():
            if node.kind != "Library":
                continue
            reference = node.props
            try:
                definition = catalog.resolve(reference, check_dependencies=False)
                library = catalog.libraries[(reference["library_id"], reference["library_version"])]
                for requirement in [*library["requirements"], *definition["requirements"]]:
                    result.append({**requirement, "library": reference["library_id"],
                                   "library_version": reference["library_version"], "node": node.id})
            except ValueError as exc:
                warnings.append(str(exc))
    return result, warnings


def _prepare_source(source, project=None, library_store=None):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise BuildError(f"Source file does not exist: {source}")
    graph_path = None
    graph = None
    declared, warnings, errors = [], [], []
    if source.suffix.lower() == ".py":
        try:
            with tokenize.open(source) as stream:
                code = stream.read()
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise BuildError(f"Cannot read Python source {source}: {exc}") from None
        if project:
            graph_path = Path(project).expanduser().resolve()
    elif source.name.lower().endswith(".json"):
        if project:
            raise BuildError("A graph source already is the editable project; omit --project.")
        graph_path = source
        code = ""
    else:
        raise BuildError("Choose an exported .py script or an editable .drop.json / .nodeforge.json project.")
    if graph_path:
        from graph_core import Graph, compile_graph
        from node_libraries import LibraryCatalog
        try:
            graph = Graph.load(graph_path)
            catalog = LibraryCatalog(library_store)
            declared, graph_warnings = _graph_metadata(graph, catalog)
            warnings.extend(graph_warnings)
            if source.suffix.lower() == ".py":
                warnings.append("The companion editable project is preserved as provided. Its contents are not used to regenerate or replace the supplied Python export.")
            if source.suffix.lower() != ".py":
                try:
                    compilation = compile_graph(graph, library_catalog=catalog)
                    code = compilation.code
                    warnings.extend(compilation.warnings)
                except (ValueError, OSError) as exc:
                    errors.append(f"Cannot export editable graph: {exc}")
        except (ValueError, OSError) as exc:
            raise BuildError(f"Cannot load editable graph {graph_path}: {exc}") from None
    else:
        warnings.append("Only exported Python was supplied. The original editable graph cannot be reconstructed; use --project FILE to preserve it alongside the executable.")
    try:
        tree = ast.parse(code, filename=str(source))
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise BuildError(f"Python source cannot be packaged: {exc}") from None
    return source, graph_path, code, tree, declared, warnings, errors


def _report(prepared, hidden_imports):
    source, graph_path, code, tree, declared, warnings, errors = prepared
    warnings = list(warnings)
    scan = _ImportScan(tree)
    scan.visit(tree)
    warnings.extend(scan.unknown)
    for name in hidden_imports:
        if not isinstance(name, str) or not MODULE_NAME.fullmatch(name):
            raise BuildError(f"Invalid hidden import {name!r}; use a dotted Python module name.")
        scan.imports.append({"module": name, "line": None, "kind": "explicit", "function": None})
    for item in declared:
        scan.imports.append({"module": item["module"], "line": None, "kind": "declared", "function": None})
    serial_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "_serial_connect"]
    simulation_only = bool(serial_calls) and all(len(node.args) >= 4 and isinstance(node.args[3], ast.Constant)
                                                and node.args[3].value == "Simulation" for node in serial_calls)
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)
    installed = importlib.metadata.packages_distributions()
    imports = []
    missing = []
    for name in sorted({item["module"] for item in scan.imports}):
        occurrences = [item for item in scan.imports if item["module"] == name]
        top = name.split(".")[0]
        local = ((source.parent / (top + ".py")).is_file() or (source.parent / top / "__init__.py").is_file())
        kind = "local" if local else ("stdlib" if top in stdlib else "third-party")
        optional_serial = (top == "serial" and simulation_only and all(
            item["kind"] == "dynamic-literal" and item["function"] in ("_serial_library", "_serial_ports")
            for item in occurrences))
        required = not optional_serial
        available = _module_available(name, source.parent)
        packages = list(installed.get(top, []))
        for item in declared:
            if item["module"].split(".")[0] == top and item.get("package") and item["package"] not in packages:
                packages.append(item["package"])
        if top == "serial" and "pyserial" not in packages:
            packages.append("pyserial")
        distributions = []
        for package in sorted(packages):
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version = None
            distributions.append({"name": package, "version": version})
        if not available and required:
            missing.append(name)
        entry = {"module": name, "kind": kind, "required": required, "available": available,
                 "distributions": distributions, "occurrences": occurrences,
                 "hidden_import": any(item["kind"] in ("dynamic-literal", "declared", "explicit") for item in occurrences)}
        if optional_serial:
            entry["reason"] = "The exported graph explicitly uses Simulation. Hardware would require pyserial."
            if not available:
                warnings.append(f"Optional hardware module {name} is unavailable; this executable will support serial simulation only.")
        imports.append(entry)
    for item in declared:
        if item.get("package"):
            try:
                installed_version = importlib.metadata.version(item["package"])
            except importlib.metadata.PackageNotFoundError:
                errors.append(f"Missing declared package {item['package']}" + (f"=={item['version']}" if item.get("version") else ""))
            else:
                if item.get("version") and installed_version != item["version"]:
                    errors.append(f"Declared dependency {item['package']} requires {item['version']}; installed {installed_version}.")
    if any(item["kind"] == "local" for item in imports):
        warnings.append("Local Python modules are bundled by PyInstaller; preserve their source separately when editing or rebuilding outside this checkout.")
    warnings.append("Static analysis cannot discover every plugin, native driver, or data file. Test the executable on a clean target computer; use --hidden-import and --add-data for additional requirements.")
    if "_DATA_BASE" in code:
        warnings.append("CSV assets are not copied automatically. Supply --add-data SOURCE=RELATIVE_DEST for each input file; absolute paths in a graph remain machine-specific. Write CSV output to a writable external location.")
    return {"format": REPORT_FORMAT, "version": 1, "source": str(source), "source_sha256": _sha256(source),
            "editable_project": str(graph_path) if graph_path else None,
            "editable_project_sha256": _sha256(graph_path) if graph_path else None,
            "python": {"executable": sys.executable, "version": platform.python_version(), "architecture": platform.machine()},
            "platform": sys.platform, "analysis": "AST and installed distribution metadata; source was not executed",
            "imports": imports, "declared_requirements": declared, "missing_required": missing,
            "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)),
            "hidden_imports": sorted({item["module"] for item in imports if item["hidden_import"] and item["available"]})}


def dependency_report(source, *, project=None, hidden_imports=(), library_store=None):
    """Analyze source/graph without executing it, using this Python environment."""
    return _report(_prepare_source(source, project, library_store), hidden_imports)


def _build_name(value, source):
    name = value or source.name.removesuffix(".json").removesuffix(".drop").removesuffix(".nodeforge").removesuffix(".py")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,79}", name) or name.endswith((" ", ".")):
        raise BuildError("Executable name must start with a letter or digit and use up to 80 letters, digits, spaces, dots, underscores, or hyphens.")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise BuildError("Choose an executable name that is not a Windows reserved device name.")
    return name


def _data_files(items, source_dir):
    result = []
    for value in items:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            origin, dest = value
        elif isinstance(value, str) and "=" in value:
            origin, dest = value.rsplit("=", 1)
        else:
            raise BuildError("Use --add-data SOURCE=RELATIVE_DEST, for example measurements.csv=data.")
        origin = Path(origin).expanduser()
        origin = origin.resolve() if origin.is_absolute() else (source_dir / origin).resolve()
        normalized = str(dest).replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts or ":" in normalized or not normalized:
            raise BuildError(f"Asset destination must be a relative directory inside the bundle: {dest}")
        if not origin.is_file():
            raise BuildError(f"Asset does not exist or is not a file: {origin}")
        result.append((origin, normalized))
    return result


def _failure_hint(output):
    lower = output.lower()
    if "no module named pyinstaller" in lower:
        return "Install the pinned build dependencies in this Python environment: python -m pip install -r requirements-build.txt."
    if "permission" in lower or "access is denied" in lower:
        return "Close the previous executable and choose a writable output directory. Check whether security software quarantined the new build."
    if "no module named" in lower or "modulenotfounderror" in lower:
        return "Install the missing package in the selected build Python environment. For runtime imports use --hidden-import MODULE; for package files use --add-data SOURCE=DEST."
    if "not supported" in lower or "unsupported" in lower:
        return "Check that the pinned PyInstaller version supports this Python version and operating system. Windows executables must be built on Windows."
    return "Read build-log.txt for the failing module or hook. Confirm dependencies are installed in the selected Python environment, then retry in a new output directory."


def build_executable(source, *, name=None, output_dir="dist/graphs", project=None,
                     report_only=False, onedir=False, hidden_imports=(), add_data=(), icon=None,
                     library_store=None, target_platform="auto"):
    """Build into an isolated staging directory, publishing only a complete result.

    Existing output is never overwritten. Failed diagnostics are retained in a
    separate ``NAME.failed-*`` directory. The exported program is not run.
    """
    source = Path(source).expanduser().resolve()
    name = _build_name(name, source)
    output_root = Path(output_dir).expanduser().resolve()
    destination = output_root / name
    if destination.exists():
        raise BuildError(f"Output already exists: {destination}. Choose another --name or --output-dir; existing builds are preserved.")
    # Never place generated files inside a source file or replace input paths.
    if output_root == source or destination == source:
        raise BuildError("Output path would replace the source file. Choose a separate output directory.")
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{name}.building-", dir=output_root))
    log = stage / "build-log.txt"
    report_path = stage / "dependency-report.json"
    log.write_text("PyDrop standalone graph build\nSource analysis never executes the exported program.\n", encoding="utf-8")
    command = None
    try:
        if target_platform not in ("auto", "windows"):
            raise BuildError("Target platform must be auto or windows.")
        if target_platform == "windows" and sys.platform != "win32":
            raise BuildError("Windows executables must be built on Windows with Windows Python. PyInstaller cannot cross-compile from this platform.")
        prepared = _prepare_source(source, project, library_store)
        report = _report(prepared, hidden_imports)
        _write_json(report_path, report)
        _, graph_path, code, _, _, _, _ = prepared
        source_folder = stage / "source"
        source_folder.mkdir()
        script = source_folder / (source.name if source.suffix.lower() == ".py" else name + ".py")
        if source.suffix.lower() == ".py":
            shutil.copy2(source, script)
        else:
            script.write_text(code, encoding="utf-8")
        if graph_path:
            graph_copy = source_folder / graph_path.name
            if graph_copy == script:
                raise BuildError("Editable project and exported source have conflicting filenames.")
            shutil.copy2(graph_path, graph_copy)
        assets = _data_files(add_data, source.parent)
        asset_manifest = []
        for index, (origin, dest) in enumerate(assets):
            copy_path = stage / "assets" / str(index) / origin.name
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, copy_path)
            asset_manifest.append({"source": str(origin), "sha256": _sha256(copy_path),
                                   "destination": dest, "preserved": str(copy_path.relative_to(stage))})
        report["bundled_assets"] = asset_manifest
        _write_json(report_path, report)
        errors = list(report["errors"])
        if report["missing_required"]:
            errors.append("Missing required Python modules: " + ", ".join(report["missing_required"]) + ". Install their packages in the selected build Python environment. See dependency-report.json for distribution names.")
        if errors and not report_only:
            raise BuildError("\n".join(errors))
        executable_relative = None
        tool_version = None
        if not report_only:
            try:
                tool_version = importlib.metadata.version("pyinstaller")
            except importlib.metadata.PackageNotFoundError:
                raise BuildError("PyInstaller is not installed in " + sys.executable + ". Install the pinned build dependencies with this interpreter: -m pip install -r requirements-build.txt.") from None
            command = [sys.executable, "-X", "utf8", "-m", "PyInstaller", "--noconfirm", "--clean",
                       "--onedir" if onedir else "--onefile", "--console", "--name", name,
                       "--distpath", str(stage / "executable"), "--workpath", str(stage / "work"),
                       "--specpath", str(stage / "spec"), "--paths", str(source.parent)]
            if icon:
                icon_path = Path(icon).expanduser().resolve()
                if not icon_path.is_file():
                    raise BuildError(f"Icon file does not exist: {icon_path}")
                command += ["--icon", str(icon_path)]
            for module in report["hidden_imports"]:
                command += ["--hidden-import", module]
            for item in asset_manifest:
                command += ["--add-data", str(stage / item["preserved"]) + os.pathsep + item["destination"]]
            command.append(str(script))
            with log.open("a", encoding="utf-8") as stream:
                stream.write("\nCommand (JSON argv): " + json.dumps(command) + "\n\n")
                stream.flush()
                try:
                    completed = subprocess.run(command, cwd=source.parent, stdout=stream, stderr=subprocess.STDOUT,
                                               encoding="utf-8", errors="replace", check=False)
                except OSError as exc:
                    raise BuildError(f"Cannot start the build Python: {exc}") from None
            if completed.returncode:
                output = log.read_text(encoding="utf-8", errors="replace")
                raise BuildError(f"PyInstaller exited with code {completed.returncode}. " + _failure_hint(output))
            suffix = ".exe" if sys.platform == "win32" else ""
            executable_relative = Path("executable") / (Path(name) / (name + suffix) if onedir else Path(name + suffix))
            if not (stage / executable_relative).is_file():
                raise BuildError("PyInstaller returned success but no expected executable was produced. Inspect build-log.txt.")
        manifest = {"format": "pydrop-standalone-build", "version": 1, "name": name,
                    "mode": "report-only" if report_only else ("onedir" if onedir else "onefile"),
                    "platform": sys.platform, "python": report["python"], "pyinstaller": tool_version,
                    "executable": str(executable_relative) if executable_relative else None,
                    "source": {"original": str(source), "sha256": _sha256(source),
                               "exported": str(script.relative_to(stage)), "exported_sha256": _sha256(script)},
                    "editable_project": ({"original": str(graph_path), "preserved": str(graph_copy.relative_to(stage)),
                                          "sha256": _sha256(graph_path)} if graph_path else None),
                    "assets": asset_manifest, "command": command,
                    "files": {str(item.relative_to(stage)): _sha256(item) for item in source_folder.iterdir() if item.is_file()}}
        _write_json(stage / "build-manifest.json", manifest)
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\nDependency report complete.\n" if report_only else "\nBuild complete. The exported program was not executed.\n")
        # rename is atomic on the same filesystem; all build artifacts already exist.
        stage.rename(destination)
        return {"output_dir": str(destination), "executable": str(destination / executable_relative) if executable_relative else None,
                "report": str(destination / "dependency-report.json"), "log": str(destination / "build-log.txt"),
                "manifest": str(destination / "build-manifest.json"), "warnings": report["warnings"]}
    except (BuildError, OSError, ValueError) as exc:
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\nERROR: " + str(exc) + "\n")
        failed = output_root / (name + ".failed-" + stage.name.rsplit("-", 1)[-1])
        try:
            stage.rename(failed)
        except OSError:
            failed = stage
        raise BuildError(str(exc), log_path=failed / "build-log.txt",
                         report_path=failed / "dependency-report.json" if (failed / "dependency-report.json").exists() else None) from None


def _parser():
    parser = argparse.ArgumentParser(description="Package an exported PyDrop graph with PyInstaller, retaining editable sources and dependency diagnostics.")
    parser.add_argument("source", help="Exported .py or editable .drop.json project; never executed during analysis")
    parser.add_argument("--name", help="Executable/build folder name")
    parser.add_argument("--output-dir", default="dist/graphs", help="Parent of the new build folder (existing builds are never overwritten)")
    parser.add_argument("--project", help="Original editable graph to retain beside a .py export")
    parser.add_argument("--report-only", action="store_true", help="Analyze and preserve sources without needing PyInstaller or building")
    parser.add_argument("--onedir", action="store_true", help="Produce a directory bundle instead of the default single executable")
    parser.add_argument("--hidden-import", action="append", default=[], metavar="MODULE")
    parser.add_argument("--add-data", action="append", default=[], metavar="SOURCE=RELATIVE_DEST", help="Input asset; source is relative to the exported script/project")
    parser.add_argument("--icon", help="Optional executable icon (.ico on Windows)")
    parser.add_argument("--library-store", help="Reviewed installed-node library catalog used by graph projects")
    parser.add_argument("--target-platform", choices=("auto", "windows"), default="auto")
    parser.add_argument("--python", dest="python_executable", help="Build using this external Python interpreter and its installed dependencies")
    return parser


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(arguments)
    if args.python_executable:
        python = Path(args.python_executable).expanduser().resolve()
        if not python.is_file():
            parser.error(f"Build Python does not exist: {python}")
        # Delegate analysis too, so versions and dependency availability refer to
        # the selected interpreter rather than the editor's environment.
        forwarded = []
        skip = False
        for value in arguments:
            if skip:
                skip = False
                continue
            if value == "--python":
                skip = True
                continue
            if not value.startswith("--python="):
                forwarded.append(value)
        command = [str(python), "-X", "utf8", str(Path(__file__).resolve()), *forwarded]
        try:
            return subprocess.run(command, check=False).returncode
        except OSError as exc:
            print(f"Build error: cannot start selected Python: {exc}", file=sys.stderr)
            return 2
    try:
        result = build_executable(args.source, name=args.name, output_dir=args.output_dir, project=args.project,
                                  report_only=args.report_only, onedir=args.onedir, hidden_imports=args.hidden_import,
                                  add_data=args.add_data, icon=args.icon, library_store=args.library_store,
                                  target_platform=args.target_platform)
    except BuildError as exc:
        print("Build error: " + str(exc), file=sys.stderr)
        if exc.log_path:
            print("Build log: " + exc.log_path, file=sys.stderr)
        if exc.report_path:
            print("Dependency report: " + exc.report_path, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
