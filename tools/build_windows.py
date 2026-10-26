"""Build the PyDrop Windows desktop bundle and per-user Inno Setup installer.

Use the project's locked requirements in an isolated Windows CPython environment.
This script writes only generated files beneath build/ and the output directory;
editable project files and saved user data are never deleted or rewritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_metadata import APP_DESCRIPTION, APP_NAME, APP_VERSION, COPYRIGHT, PUBLISHER, VERSION_PARTS

INNO_VERSION = "6.7.3"
DEFAULT_EPOCH = "1767225600"  # Fixed release timestamp; override SOURCE_DATE_EPOCH deliberately.


class BuildError(Exception):
    """An actionable distribution build failure."""


def _sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def _locked_packages():
    packages = {}
    for line in (ROOT/"requirements-build.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            if "==" not in line:
                raise BuildError("requirements-build.txt must contain exact name==version pins.")
            name, version = line.split("==", 1)
            packages[name.strip()] = version.strip()
    return packages


def _find_iscc(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    elif os.environ.get("ISCC"):
        candidates.append(Path(os.environ["ISCC"]))
    else:
        command = shutil.which("ISCC.exe") or shutil.which("iscc")
        if command:
            candidates.append(Path(command))
        candidates.append(ROOT/".build-tools"/"InnoSetup"/"ISCC.exe")
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env])/"Inno Setup 6"/"ISCC.exe")
                candidates.append(Path(os.environ[env])/"Programs"/"Inno Setup 6"/"ISCC.exe")
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _check_inno_version(compiler):
    """Ask ISPP's encoded version; ISCC /? and PE resources omit its version."""
    parts = tuple(int(part) for part in INNO_VERSION.split("."))
    encoded = (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8)
    # An intentional preprocessor error avoids creating an installer or relying
    # on localized compiler banner text. ISPP expands conditions before #error.
    probe_text = (f"#if PREPROCVER != 0x{encoded:08x}\n"
                  "#error PYDROP_INNO_VERSION_MISMATCH\n"
                  "#else\n#error PYDROP_INNO_VERSION_OK\n#endif\n")
    try:
        with tempfile.TemporaryDirectory(prefix="pydrop-inno-version-") as temporary:
            probe_path = Path(temporary)/"version-probe.iss"
            probe_path.write_text(probe_text, encoding="utf-8", newline="\n")
            probe = subprocess.run([str(compiler), str(probe_path)], capture_output=True,
                                   text=True, errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"Unable to query Inno Setup compiler {compiler}: {exc}") from exc
    output = probe.stdout+probe.stderr
    if "PYDROP_INNO_VERSION_OK" not in output or "PYDROP_INNO_VERSION_MISMATCH" in output:
        raise BuildError(f"Reproducible installers require Inno Setup {INNO_VERSION}; compiler at {compiler} failed the exact-version preprocessor check. Use --iscc with the pinned compiler or --skip-installer.\n{output.strip()}")


def preflight(skip_installer=False, iscc=None):
    if sys.platform != "win32":
        raise BuildError("Windows desktop packages must be built on Windows. PyInstaller does not cross-compile. Use a Windows machine or the Windows CI workflow.")
    if sys.version_info[:2] < (3, 10) or sys.version_info[:2] > (3, 15):
        raise BuildError("Use supported CPython 3.10-3.15 with Tcl/Tk installed. Create a fresh build environment and install requirements-build.txt.")
    if platform.python_implementation() != "CPython":
        raise BuildError("The release build requires CPython; install the Windows CPython distribution with Tcl/Tk.")
    if platform.machine().lower() not in ("amd64", "x86_64", "x86", "i386", "i686"):
        raise BuildError("Use x64 or x86 Windows CPython for this installer. On Windows ARM use x64 Python emulation. Native ARM64 packaging is not configured.")
    mismatches = []
    for name, required in _locked_packages().items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = "missing"
        if installed != required:
            mismatches.append(f"{name}: expected {required}, found {installed}")
    if mismatches:
        raise BuildError("Build dependencies differ from the release lock:\n  "+"\n  ".join(mismatches)+"\nInstall with this interpreter: \""+sys.executable+'\" -m pip install -r requirements-build.txt')
    try:
        import tkinter
        import _tkinter
        tk = tkinter.Tcl()
        if not _tkinter.TK_VERSION:
            raise RuntimeError("Tk version unavailable")
        tk.eval("info patchlevel")
    except Exception as exc:
        raise BuildError(f"Tcl/Tk is missing or unusable ({exc}). Modify the Windows CPython installation to include Tcl/Tk and recreate the build environment.") from exc
    missing = [name for name in ("nodeforge.py", "runtime_entry.py", "app_runtime.py", "packaging/PyDrop.spec", "assets/pydrop.ico") if not (ROOT/name).is_file()]
    if missing:
        raise BuildError("The source checkout is incomplete; missing: "+", ".join(missing))
    compiler = _find_iscc(iscc)
    if not skip_installer and compiler is None:
        raise BuildError(f"Inno Setup {INNO_VERSION} compiler ISCC.exe was not found. Install that version, set ISCC, pass --iscc PATH, or use --skip-installer for a portable folder.")
    if compiler and not skip_installer:
        _check_inno_version(compiler)
    return compiler


def _run(command, log, environment=None):
    print("Running:", subprocess.list2cmdline([str(part) for part in command]), flush=True)
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(part) for part in command], cwd=ROOT, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        tail = []
        for line in process.stdout:
            print(line, end="", flush=True)
            stream.write(line)
            stream.flush()
            tail.append(line.rstrip())
            tail = tail[-35:]
        result = process.wait()
    if result:
        raise BuildError(f"Build command exited with code {result}. Full log: {log}\n"+"\n".join(tail))


def _version_file(destination):
    version = (*VERSION_PARTS, 0)
    values = {"CompanyName": PUBLISHER, "FileDescription": APP_DESCRIPTION,
              "FileVersion": APP_VERSION, "InternalName": APP_NAME,
              "LegalCopyright": COPYRIGHT, "OriginalFilename": "PyDrop.exe",
              "ProductName": APP_NAME, "ProductVersion": APP_VERSION}
    strings = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in values.items())
    destination.write_text(f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
      flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
{strings}
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8", newline="\n")


def _collect_licenses(destination, compiler=None):
    """Retain installed license files without adding build packages to runtime."""
    destination.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for name in _locked_packages():
        distribution = importlib.metadata.distribution(name)
        collected = []
        for item in distribution.files or ():
            if not item.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            # Keep nested vendor notice paths, including their distribution dir.
            relative = Path(name)/Path(*[part for part in item.parts if part not in ("..", ".")])
            target = destination/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            collected.append(relative.as_posix())
        inventory[name] = {"version": distribution.version, "license_files": collected}
    for source in sorted((ROOT/"packaging"/"licenses").glob("*")):
        if source.is_file():
            shutil.copyfile(source, destination/source.name)
    python_license = Path(sys.base_prefix)/"LICENSE.txt"
    if not python_license.is_file():
        raise BuildError(f"CPython license file is missing: {python_license}. Use a complete Windows CPython installation.")
    shutil.copyfile(python_license, destination/"CPython-LICENSE.txt")
    if compiler:
        for source in compiler.parent.glob("*"):
            if source.is_file() and source.name.upper().startswith(("LICENSE", "COPYING")):
                shutil.copyfile(source, destination/("InnoSetup-"+source.name))
    (destination/"inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True)+"\n", encoding="utf-8", newline="\n")
    return inventory


def _source_hashes():
    sources = list(ROOT.glob("*.py"))+list(ROOT.glob("*.md"))+list(ROOT.glob("requirements-*.txt"))
    sources += [ROOT/"build_windows.ps1"]
    for name in ("tools", "packaging", "assets", "examples"):
        sources.extend(path for path in (ROOT/name).rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.suffix != ".pyc")
    return {path.relative_to(ROOT).as_posix(): _sha256(path) for path in sorted(set(sources)) if path.is_file()}


def _zip_bundle(bundle, destination, epoch):
    date = datetime.fromtimestamp(max(epoch, 315532800), timezone.utc)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(bundle.parent).as_posix(), date.timetuple()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())


def _validate_output_directory(output):
    output = output.expanduser().resolve()
    # Prevent accidentally asking PyInstaller to overwrite a source directory.
    if output == ROOT or output in ROOT.parents or (output.exists() and (output/"nodeforge.py").exists()):
        raise BuildError("Choose a dedicated generated output directory (for example dist), not the source checkout or its parent.")
    if (output/"PyDrop").exists():
        raise BuildError(f"Application bundle already exists: {output/'PyDrop'}. It may contain editable projects. Choose a fresh --output directory (PowerShell: -Output); existing bundles are never deleted or replaced.")
    return output


def build(output, skip_installer=False, iscc=None):
    # Refuse replacement before toolchain checks or subprocesses can modify data.
    output = _validate_output_directory(output)
    compiler = preflight(skip_installer, iscc)
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT/"build"/"windows"
    work.mkdir(parents=True, exist_ok=True)
    version = work/"version_info.txt"
    _version_file(version)
    license_directory = work/"third_party_licenses"
    license_inventory = _collect_licenses(license_directory, compiler)
    environment = os.environ.copy()
    environment.update({"PYTHONHASHSEED": "0", "PYDROP_VERSION_FILE": str(version),
                        "PYDROP_LICENSE_DIR": str(license_directory),
                        "SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_EPOCH)})
    try:
        epoch = int(environment["SOURCE_DATE_EPOCH"])
        datetime.fromtimestamp(epoch, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise BuildError("SOURCE_DATE_EPOCH must be a valid integer Unix timestamp.") from exc
    pyinstaller = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--distpath", str(output), "--workpath", str(work/"pyinstaller"),
                   str(ROOT/"packaging"/"PyDrop.spec")]
    _run(pyinstaller, work/"pyinstaller.log", environment)
    bundle = output/"PyDrop"
    for name in ("PyDrop.exe", "PyDropRuntime.exe"):
        if not (bundle/name).is_file():
            raise BuildError(f"PyInstaller completed but expected artifact is missing: {bundle/name}. See {work/'pyinstaller.log'}.")
    architecture = "x64" if struct.calcsize("P") == 8 else "x86"
    installer = None
    if not skip_installer:
        installer = output/f"PyDrop-{APP_VERSION}-win-{architecture}-setup.exe"
        _run([str(compiler), f"/DAppVersion={APP_VERSION}", f"/DBuildArch={architecture}",
              f"/DSourceDir={bundle}", f"/DOutputDir={output}", str(ROOT/"packaging"/"PyDrop.iss")],
             work/"installer.log", environment)
        if not installer.is_file():
            raise BuildError(f"Inno Setup completed but installer is missing: {installer}. See {work/'installer.log'}.")
    archive = output/f"PyDrop-{APP_VERSION}-win-{architecture}-portable.zip"
    _zip_bundle(bundle, archive, epoch)
    manifest = {"schema_version": 1, "application": APP_NAME, "version": APP_VERSION,
                "platform": "windows", "architecture": architecture,
                "python": sys.version, "source_date_epoch": epoch,
                "dependencies": _locked_packages(), "inno_setup": INNO_VERSION if installer else None,
                "license_inventory": license_inventory,
                "sources": _source_hashes(), "bundle": "PyDrop", "portable_archive": archive.name,
                "installer": installer.name if installer else None,
                "validation": "Build completed; run tools/clean_windows_test.ps1 on a clean Windows system before release."}
    manifest_path = output/"build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n", encoding="utf-8", newline="\n")
    artifacts = sorted(path for path in bundle.rglob("*") if path.is_file())+[archive, manifest_path]
    if installer:
        artifacts.append(installer)
    checksums = "".join(f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in artifacts)
    (output/"SHA256SUMS.txt").write_text(checksums, encoding="utf-8", newline="\n")
    print(f"\nBuilt {APP_NAME} {APP_VERSION} ({architecture}): {bundle}")
    if installer:
        print(f"Installer: {installer}")
    print(f"Portable archive: {archive}\nDependency/source manifest: {manifest_path}\nChecksums: {output/'SHA256SUMS.txt'}")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"dist", help="Dedicated generated output directory (default: dist)")
    parser.add_argument("--skip-installer", action="store_true", help="Build the portable application folder/ZIP without Inno Setup")
    parser.add_argument("--iscc", help=f"Path to Inno Setup {INNO_VERSION} ISCC.exe (or set ISCC)")
    args = parser.parse_args(argv)
    try:
        build(args.output, args.skip_installer, args.iscc)
    except (BuildError, OSError) as exc:
        print(f"PyDrop build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
