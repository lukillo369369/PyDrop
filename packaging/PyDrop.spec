# PyInstaller spec: one application directory with a GUI and console worker.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
icon = str(root / "assets" / "pydrop.ico")
version_file = os.environ.get("PYDROP_VERSION_FILE")
data_files = []
for source in sorted(root.glob("*.py")):
    data_files.append((str(source), "."))
data_files.append((str(root / "custom_nodes.json"), "."))
for name in ("build_graph.py",):
    source = root / "tools" / name
    if source.is_file():
        data_files.append((str(source), "tools"))
for source in sorted(root.glob("requirements-*.txt")):
    data_files.append((str(source), "."))
for name in ("assets", "examples"):
    data_files.append((str(root / name), name))
for source in sorted(root.glob("*.md")):
    data_files.append((str(source), "."))
license_directory = os.environ.get("PYDROP_LICENSE_DIR")
if license_directory:
    data_files.append((license_directory, "third_party_licenses"))

hidden = collect_submodules("serial")
editor = Analysis([str(root / "nodeforge.py")], pathex=[str(root)], binaries=[],
                  datas=data_files, hiddenimports=hidden, hookspath=[],
                  hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
runtime = Analysis([str(root / "runtime_entry.py")], pathex=[str(root)], binaries=[],
                   datas=[], hiddenimports=hidden, hookspath=[],
                   hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
editor_pyz = PYZ(editor.pure)
runtime_pyz = PYZ(runtime.pure)
editor_exe = EXE(editor_pyz, editor.scripts, [], exclude_binaries=True, name="PyDrop",
                 debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
                 console=False, disable_windowed_traceback=False, icon=icon,
                 version=version_file)
runtime_exe = EXE(runtime_pyz, runtime.scripts, [], exclude_binaries=True, name="PyDropRuntime",
                  debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
                  console=True, icon=icon, version=version_file)
bundle = COLLECT(editor_exe, runtime_exe, editor.binaries, runtime.binaries,
                 editor.datas, runtime.datas, strip=False, upx=False, name="PyDrop")
