"""Paths and subprocess commands shared by source and frozen desktop builds."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def frozen():
    return bool(getattr(sys, "frozen", False))


def configure_windows_identity():
    if sys.platform == "win32":
        import ctypes
        from app_metadata import APP_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def resource_path(name):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def user_data_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "PyDrop"


def custom_library_path():
    if not frozen():
        return resource_path("custom_nodes.json")
    target = user_data_dir() / "custom_nodes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Exclusive creation preserves existing templates during upgrades.
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(resource_path("custom_nodes.json").read_text(encoding="utf-8"))
        except FileExistsError:
            pass
    return target


def library_store_path():
    return user_data_dir() / "installed_node_libraries" if frozen() else resource_path("installed_node_libraries")


def project_directory():
    if not frozen():
        return resource_path("")
    target = user_data_dir() / "projects"
    target.mkdir(parents=True, exist_ok=True)
    return target


def script_command(script, *, unbuffered=True):
    """A frozen GUI executable is a bootloader, not a Python interpreter."""
    if frozen():
        runtime = Path(sys.executable).with_name("PyDropRuntime.exe" if sys.platform == "win32" else "PyDropRuntime")
        if not runtime.is_file():
            raise OSError("PyDropRuntime is missing. Reinstall PyDrop or copy the complete application folder.")
        return [str(runtime), "--script", str(script)]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-I", "-X", "utf8", *(["-u"] if unbuffered else []), str(script)]


def external_python_environment():
    """Keep a bundled Tcl/Python installation out of an external build SDK."""
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "_MEIPASS2"):
        env.pop(key, None)
    for key in list(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def resolve_build_python(path):
    path = str(path).strip()
    if not path:
        raise ValueError("Choose a Python build interpreter with PyInstaller installed. The desktop app's bundled runner is not a build SDK.")
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        found = shutil.which(path)
        if found:
            resolved = Path(found)
    if not resolved.is_file():
        raise ValueError("Python build interpreter was not found. Browse to python.exe in your build environment.")
    if resolved.name.lower() in ("pydrop.exe", "pydropruntime.exe"):
        raise ValueError("Select a Python interpreter, such as a virtual environment's python.exe, instead of PyDrop.exe.")
    return resolved.resolve()
