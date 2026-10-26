"""Versioned executable-node files and an explicit, data-only installation catalog.

No implementation or dependency module is imported by this module. Validation
compiles syntax only. Installed code is emitted inside functions for execution
by the graph runner, never evaluated by library browsing or project loading.
"""
import ast
import copy
import hashlib
import importlib.machinery
import importlib.metadata
import json
import keyword
import math
from pathlib import Path
import re
import sys
import textwrap
from app_runtime import library_store_path

from engineering_units import UnitError, unit_spec

LIBRARY_FORMAT = "pydrop-node-library"
PORT_TYPES = ("Number", "Text", "Boolean", "Quantity", "Any")
DEFAULT_LIBRARY_STORE = library_store_path()
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


class LibraryError(ValueError):
    """A library, installation, or dependency issue that can be shown directly."""


def _text(value, label, maximum=200, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or "\x00" in value:
        raise LibraryError(f"{label} must be {'0' if empty else '1'}–{maximum} characters.")
    if not empty and any(ord(c) < 32 for c in value):
        raise LibraryError(f"{label} must be a single line.")
    return value


def _id(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LibraryError(f"{label} must start with a lowercase letter and use lowercase letters, digits, dots, hyphens, or underscores (64 max).")
    return value


def _version(value, label):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise LibraryError(f"{label} needs an exact major.minor.patch version, such as 1.0.0.")
    return value


def default_value(spec, text):
    """Defaults use inspector text; Quantity returns a numeric magnitude here."""
    _text(text, "Input default", 2000, empty=True)
    kind = spec["type"]
    if kind in ("Text", "Any"):
        return text
    if kind == "Boolean":
        if text.lower() not in ("true", "false"):
            raise LibraryError(f"{spec['name']} needs a True or False default.")
        return text.lower() == "true"
    try:
        result = float(text)
    except (ValueError, OverflowError):
        raise LibraryError(f"{spec['name']} needs a finite numeric default.") from None
    if not math.isfinite(result) or abs(result) > 1e100:
        raise LibraryError(f"{spec['name']} needs a finite default of magnitude at most 1e100.")
    return result


def _ports(items, *, inputs):
    if not isinstance(items, list) or not (0 if inputs else 1) <= len(items) <= 8:
        raise LibraryError("Use zero to eight inputs and one to eight outputs.")
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"name", "type", "default", "unit"}:
            raise LibraryError("Each port needs a name, type, optional unit, and an input default.")
        name = item.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name) or keyword.iskeyword(name) or name in ("run", "next"):
            raise LibraryError("Port names must be Python argument names (32 max); run and next are reserved.")
        if name in seen or item.get("type") not in PORT_TYPES:
            raise LibraryError("Each port name must be unique on its side; choose Number, Text, Boolean, Quantity, or Any.")
        seen.add(name)
        port = {"name": name, "type": item["type"]}
        if item["type"] == "Quantity":
            try:
                unit_spec(item.get("unit"))
            except UnitError as exc:
                raise LibraryError(str(exc)) from None
            port["unit"] = item["unit"]
        elif "unit" in item:
            raise LibraryError("Only Quantity ports have a unit.")
        if inputs:
            fallback = "False" if item["type"] == "Boolean" else ("" if item["type"] in ("Text", "Any") else "0")
            port["default"] = item.get("default", fallback)
            default_value(port, port["default"])
        elif "default" in item:
            raise LibraryError("Output ports do not have defaults.")
        result.append(port)
    return result


def _requirements(items):
    if not isinstance(items, list) or len(items) > 32:
        raise LibraryError("Use up to 32 dependency declarations.")
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"module", "package", "version"} or not isinstance(item.get("module"), str) or not _MODULE.fullmatch(item["module"]):
            raise LibraryError("A dependency needs an absolute Python module name and optional package/version.")
        dependency = {"module": item["module"]}
        if "package" in item:
            if not isinstance(item["package"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", item["package"]):
                raise LibraryError("Use a Python distribution name for package, such as numpy.")
            dependency["package"] = item["package"]
        if "version" in item:
            if "package" not in dependency:
                raise LibraryError("An exact dependency version also needs its distribution package name.")
            dependency["version"] = _text(item["version"], "Dependency version", 80)
        if dependency not in result:
            result.append(dependency)
    return result


def implementation_source(definition, function_name):
    arguments = ", ".join(port["name"] for port in definition["inputs"])
    return f"def {function_name}({arguments}):\n" + textwrap.indent(definition["implementation"], "    ") + "\n"


def validate_library(data):
    if not isinstance(data, dict) or data.get("format") != LIBRARY_FORMAT or data.get("schema_version") != 1:
        raise LibraryError("Expected a pydrop-node-library file with schema_version 1. This is executable Python, not a numeric formula library.")
    if set(data) - {"format", "schema_version", "id", "name", "version", "requirements", "nodes"}:
        raise LibraryError("Unknown library fields. Expected id, name, version, requirements, and nodes.")
    result = {"format": LIBRARY_FORMAT, "schema_version": 1,
              "id": _id(data.get("id"), "Library ID"), "name": _text(data.get("name"), "Library name", 80),
              "version": _version(data.get("version"), "Library version"),
              "requirements": _requirements(data.get("requirements", [])), "nodes": []}
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise LibraryError("A library contains one to 100 node definitions.")
    seen = set()
    for item in nodes:
        if not isinstance(item, dict) or set(item) - {"id", "version", "name", "category", "description", "inputs", "outputs", "implementation", "requirements"}:
            raise LibraryError("Invalid node definition fields.")
        node = {"id": _id(item.get("id"), "Definition ID"),
                "version": _version(item.get("version"), "Definition version"),
                "name": _text(item.get("name"), "Node name", 80),
                "category": _text(item.get("category"), "Category", 80),
                "description": _text(item.get("description", ""), "Description", 2000, empty=True),
                "inputs": _ports(item.get("inputs"), inputs=True), "outputs": _ports(item.get("outputs"), inputs=False),
                "implementation": _text(item.get("implementation"), "Python implementation", 20000, empty=True),
                "requirements": _requirements(item.get("requirements", []))}
        if node["id"] in seen:
            raise LibraryError("Definition IDs must be unique within a library.")
        seen.add(node["id"])
        if any(ord(character) < 32 and character not in "\t\r\n" for character in node["implementation"]):
            raise LibraryError("Python implementation may contain tabs and newlines, but not other control characters.")
        try:
            # Compiling a function body checks syntax without evaluating it.
            tree = ast.parse(implementation_source(node, "_library_syntax_check"))
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
                raise LibraryError("Implementation must stay inside its function body; module-level code is not permitted.")
            compile(tree, "<library definition>", "exec")
            if any(isinstance(part, ast.ImportFrom) and part.level for part in ast.walk(tree)):
                raise LibraryError("Use absolute imports in node implementations; relative imports have no library package context.")
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise LibraryError(f"{node['name']}: invalid Python implementation: {exc}") from None
        result["nodes"].append(node)
    return result


def _read_json(path):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise LibraryError("Library or registry exceeds the 1 MB size limit.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LibraryError(f"Cannot read library JSON: {exc}") from None


def load_library(path):
    """Browsing parses data and checks syntax only; it never runs the code."""
    return validate_library(_read_json(path))


def content_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_library(path, data):
    """Authoring a file does not install it or grant execution permission."""
    validated = validate_library(data)
    _write_json(path, validated)
    return validated


def module_available(name):
    """Find module specs without importing parents (find_spec on dots imports)."""
    if name in sys.modules:
        return True
    parts, search = name.split("."), sys.path
    for index in range(len(parts)):
        full = ".".join(parts[:index + 1])
        spec = None
        if index == 0:
            spec = importlib.machinery.BuiltinImporter.find_spec(full) or importlib.machinery.FrozenImporter.find_spec(full)
        spec = spec or importlib.machinery.PathFinder.find_spec(full, search)
        if spec is None:
            return False
        if index < len(parts) - 1:
            search = spec.submodule_search_locations
            if search is None:
                return False
    return True


def dependency_issues(library, definition=None):
    definitions = library["nodes"] if definition is None else [definition]
    requirements = copy.deepcopy(library["requirements"])
    for node in definitions:
        requirements += node["requirements"]
        tree = ast.parse(implementation_source(node, "_dependency_scan"))
        # Static imports are checked as well as explicitly declared dependencies.
        for part in ast.walk(tree):
            names = [alias.name for alias in part.names] if isinstance(part, ast.Import) else ([part.module] if isinstance(part, ast.ImportFrom) else [])
            requirements += [{"module": name} for name in names if name]
    issues = []
    for requirement in requirements:
        module = requirement["module"]
        package, version = requirement.get("package"), requirement.get("version")
        if not module_available(module):
            issues.append(f"Missing Python module '{module}'" + (f" (package {package}{'==' + version if version else ''})" if package else "") + ". Install it in the Python environment used to run this project.")
        if package:
            try:
                installed = importlib.metadata.version(package)
                if version and installed != version:
                    issues.append(f"Dependency {package} requires version {version}; installed version is {installed}.")
            except importlib.metadata.PackageNotFoundError:
                issues.append(f"Missing Python package '{package}{'==' + version if version else ''}'.")
    return list(dict.fromkeys(issues))


def make_reference(library, definition):
    return {"library_id": library["id"], "library_version": library["version"], "library_digest": content_digest(library),
            "definition_id": definition["id"], "definition_version": definition["version"],
            **{key: copy.deepcopy(definition[key]) for key in ("name", "category", "description", "inputs", "outputs")},
            "defaults": {port["name"]: port["default"] for port in definition["inputs"]}}


def validate_reference(props):
    required = {"library_id", "library_version", "library_digest", "definition_id", "definition_version", "name", "category", "description", "inputs", "outputs", "defaults"}
    if not isinstance(props, dict) or set(props) != required:
        raise LibraryError("Library node references contain metadata and defaults only; project files cannot install or embed Python implementations.")
    _id(props["library_id"], "Library ID")
    _id(props["definition_id"], "Definition ID")
    _version(props["library_version"], "Library version")
    _version(props["definition_version"], "Definition version")
    if not isinstance(props["library_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", props["library_digest"]):
        raise LibraryError("Invalid installed-library fingerprint.")
    for key in ("name", "category", "description"):
        _text(props[key], key, 2000 if key == "description" else 80, empty=key == "description")
    inputs = _ports(props["inputs"], inputs=True)
    _ports(props["outputs"], inputs=False)
    if not isinstance(props["defaults"], dict) or set(props["defaults"]) != {port["name"] for port in inputs}:
        raise LibraryError("Library node defaults must match its input names.")
    for port in inputs:
        default_value(port, props["defaults"][port["name"]])
    return copy.deepcopy(props)


class LibraryCatalog:
    def __init__(self, store=None):
        self.store = Path(store) if store is not None else DEFAULT_LIBRARY_STORE
        self.libraries, self.issues, self._entries = {}, [], []
        registry = self.store / "registry.json"
        if not registry.exists():
            return
        try:
            data = _read_json(registry)
            if not isinstance(data, dict) or data.get("format") != "pydrop-installed-libraries" or data.get("version") != 1 or not isinstance(data.get("libraries"), list) or len(data["libraries"]) > 100:
                raise LibraryError("Invalid installed-library registry.")
            self._entries = data["libraries"]
            for item in self._entries:
                try:
                    if not isinstance(item, dict) or set(item) != {"id", "version", "digest", "file"}:
                        raise LibraryError("Invalid installed-library entry.")
                    if not isinstance(item["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["digest"]):
                        raise LibraryError("Invalid library fingerprint in registry.")
                    # Registry entries cannot redirect reads outside the store.
                    if item["file"] != item["digest"] + ".pydrop-lib.json":
                        raise LibraryError("Invalid installed-library filename.")
                    library = load_library(self.store / item["file"])
                    key = library["id"], library["version"]
                    if (item["id"], item["version"]) != key or content_digest(library) != item["digest"] or key in self.libraries:
                        raise LibraryError("Installed library changed or has duplicate identity. Reinstall the reviewed file explicitly.")
                    self.libraries[key] = library
                except (LibraryError, OSError) as exc:
                    self.issues.append(str(exc))
        except (LibraryError, OSError) as exc:
            self.issues.append(str(exc))

    def resolve(self, props, *, check_dependencies=True):
        validate_reference(props)
        library = self.libraries.get((props["library_id"], props["library_version"]))
        label = f"{props['library_id']}@{props['library_version']} / {props['definition_id']}@{props['definition_version']}"
        if library is None:
            raise LibraryError(f"Missing library {label}. Open Libraries, review the file, and explicitly install this exact version.")
        if content_digest(library) != props["library_digest"]:
            raise LibraryError(f"Library fingerprint mismatch for {label}. Install the exact library used by this project; versions cannot silently replace code.")
        definition = next((node for node in library["nodes"] if node["id"] == props["definition_id"] and node["version"] == props["definition_version"]), None)
        if definition is None:
            raise LibraryError(f"Missing definition {label}.")
        expected = make_reference(library, definition)
        if any(props[key] != expected[key] for key in expected if key != "defaults"):
            raise LibraryError(f"Port schema or metadata mismatch for {label}. This project does not match the installed definition.")
        if check_dependencies:
            issues = dependency_issues(library, definition)
            if issues:
                raise LibraryError(label + ": " + " ".join(issues))
        return definition

    def palette(self):
        result = {}
        for library in self.libraries.values():
            for definition in library["nodes"]:
                label = f"{definition['name']} [{library['id']}@{library['version']}/{definition['id']}@{definition['version']}]"
                result[label] = make_reference(library, definition)
        return result


def install_library(path, store=None, *, allow_executable=False, expected_digest=None):
    if allow_executable is not True:
        raise LibraryError("Executable Python library: explicit installation consent is required. Browsing and saving do not authorize execution.")
    library = load_library(path)
    digest = content_digest(library)
    if expected_digest is not None and digest != expected_digest:
        raise LibraryError("The library changed after preview. Browse and review it again before installation.")
    catalog = LibraryCatalog(store)
    if catalog.issues:
        raise LibraryError("Repair the installed-library registry before installing: " + " ".join(catalog.issues))
    key = library["id"], library["version"]
    old = catalog.libraries.get(key)
    if old is not None:
        if content_digest(old) != digest:
            raise LibraryError("This library version is already installed with different contents. Increase the library and changed definition versions.")
        return catalog
    if len(catalog._entries) >= 100:
        raise LibraryError("The installed-library limit is 100 versions.")
    for installed in catalog.libraries.values():
        if installed["id"] != library["id"]:
            continue
        previous = {(node["id"], node["version"]): node for node in installed["nodes"]}
        for node in library["nodes"]:
            if (node["id"], node["version"]) in previous and previous[node["id"], node["version"]] != node:
                raise LibraryError(f"Definition {node['id']} changed without a new definition version.")
    filename = digest + ".pydrop-lib.json"
    _write_json(catalog.store / filename, library)
    entries = catalog._entries + [{"id": library["id"], "version": library["version"], "digest": digest, "file": filename}]
    _write_json(catalog.store / "registry.json", {"format": "pydrop-installed-libraries", "version": 1, "libraries": entries})
    return LibraryCatalog(catalog.store)


def implementation_name(props):
    suffix = content_digest({key: props[key] for key in ("library_digest", "definition_id", "definition_version")})[:24]
    return "_library_impl_" + suffix


def reference_uses_units(props):
    return any(port["type"] == "Quantity" for port in props["inputs"] + props["outputs"])


def validate_port_types(graph, error_type):
    """Reject known incompatible wires, retaining runtime checks for Any/calls."""
    if not any(node.kind == "Library" for node in graph.nodes.values()):
        return

    def output_type(node, pin, seen):
        if node.id in seen:
            return None
        seen = seen | {node.id}
        if node.kind == "Library":
            return next(port["type"] for port in node.props["outputs"] if port["name"] == pin)
        if node.kind == "Variable":
            return node.props["type"]
        if node.kind in ("Quantity", "Unit Convert", "Force", "Torque", "Ohm's Law", "Electrical Power"):
            return "Quantity"
        if node.kind in ("Compare", "Boolean Logic"):
            return "Boolean"
        if node.kind in ("Input", "Text Join"):
            return "Text"
        if node.kind == "Convert":
            return node.props["type"]
        if node.kind == "Random":
            return "Number"
        if node.kind == "Assign":
            edge = graph.input_edge(node.id, "value")
            return output_type(graph.nodes[edge.source], edge.output, seen) if edge else "Text"
        return None

    for edge in graph.edges:
        source, target = graph.nodes[edge.source], graph.nodes[edge.target]
        expected = None
        if target.kind == "Library":
            spec = next((port for port in target.props["inputs"] if port["name"] == edge.input), None)
            expected = (spec["type"],) if spec else None
        elif source.kind == "Library":
            if target.kind in ("Math", "Function", "Custom", "Clamp"):
                expected = ("Number", "Quantity")
            elif target.kind in ("Quantity", "Random"):
                expected = ("Number",)
            elif target.kind == "Boolean Logic":
                expected = ("Boolean",)
            elif target.kind in ("Force", "Torque", "Ohm's Law", "Electrical Power"):
                expected = ("Quantity",)
        if not expected or "Any" in expected:
            continue
        actual = output_type(source, edge.output, set())
        if actual and actual != "Any" and actual not in expected:
            raise error_type(f"{target.title}.{edge.input} expects {' or '.join(expected)}; received {actual} from {source.title}.{edge.output}.", target.id)
