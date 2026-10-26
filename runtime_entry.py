"""Console companion for the windowed Windows application; no system Python needed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import traceback

from app_metadata import APP_NAME, APP_VERSION
from app_runtime import frozen, resource_path, script_command, user_data_dir


def self_test():
    """Exercise the installed runtime and real child-process protocol offline."""
    from graph_core import Graph, compile_graph, demo_graph
    from example_graphs import sensor_dashboard_demo, interactive_calculator_demo
    checks = {}
    details = {}
    resources = ("engineering_units.py", "library_runtime.py", "data_runtime.py",
                 "data_ui.py", "serial_runtime.py", "custom_nodes.json", "assets/pydrop.ico",
                 "examples/sensor_dashboard.nodeforge.json", "tools/build_graph.py", "graph_packaging.py")
    checks["compiler_resources"] = all(resource_path(name).is_file() for name in resources)
    details["missing_resources"] = [name for name in resources if not resource_path(name).is_file()]
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        checks["tkinter"] = True
    except Exception as exc:
        checks["tkinter"] = False
        details["tkinter"] = str(exc)
    try:
        import serial
        checks["serial_dependency"] = callable(serial.Serial)
    except (ImportError, AttributeError) as exc:
        checks["serial_dependency"] = False
        details["serial_dependency"] = str(exc)
    with tempfile.TemporaryDirectory(prefix="pydrop-self-test-") as directory:
        work = Path(directory)
        for name, factory, answer, expected in (
            ("force", demo_graph, None, "Force (N): 24.525"),
            ("simulation", sensor_dashboard_demo, None, "Temperature (C): 23.5"),
            ("input", interactive_calculator_demo, "2\n3\n", "Sum: 5.0"),
        ):
            try:
                graph = factory()
                path = work / (name + ".drop.json")
                graph.save(path)
                loaded = Graph.load(path)
                if loaded.to_dict() != graph.to_dict():
                    raise ValueError("Editable graph round-trip changed its contents")
                script = work / (name + ".py")
                script.write_text(compile_graph(loaded).code, encoding="utf-8")
                result = subprocess.run(script_command(script), input=answer, capture_output=True,
                                        text=True, encoding="utf-8", cwd=work, timeout=30,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                checks[name] = result.returncode == 0 and expected in result.stdout
                details[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
            except Exception as exc:
                checks[name] = False
                details[name] = str(exc)
        try:
            from data_runtime import _data_read
            table = _data_read(str(resource_path("examples/experimental_measurements.csv")), ",", "", "", False, False)
            checks["csv_resource"] = bool(table["data"])
        except Exception as exc:
            checks["csv_resource"] = False
            details["csv_resource"] = str(exc)
    return {"ok": all(checks.values()), "app": APP_NAME, "version": APP_VERSION,
            "frozen": frozen(), "checks": checks, "details": details,
            "user_data_dir": str(user_data_dir())}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--pydrop":
        # Console users retain the ordinary graph CLI and its exit status.
        import nodeforge
        sys.argv = [sys.argv[0], *args[1:]]
        return nodeforge.main()
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION} bundled graph runtime")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--script", type=Path)
    actions.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args(args)
    if options.self_test:
        report = self_test()
        shown = json.dumps(report, indent=2)
        if options.report:
            options.report.parent.mkdir(parents=True, exist_ok=True)
            options.report.write_text(shown + "\n", encoding="utf-8")
        print(shown)
        return 0 if report["ok"] else 1
    script = options.script.resolve(strict=True)
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
