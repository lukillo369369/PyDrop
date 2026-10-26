# Building and distributing PyDrop

PyDrop 0.1.0 is the Windows desktop product name. The source entry point remains
`nodeforge.py`, and existing editable `.nodeforge.json` projects remain supported.
The desktop bundle has a PyDrop icon and Windows product/file version metadata.
Recipients need neither Python nor pip to launch the packaged editor or a
packaged graph.

## Build machine

Use **Windows 10/11, CPython 3.15.1 with Tcl/Tk, and Windows PowerShell 5.1 or
newer** for the pinned Windows build. Match the Python architecture to the target
architecture; the release recipe uses x64. Use a normal CPython installation,
not the embeddable distribution. Building the installer additionally requires
**Inno Setup 6.7.3** (`ISCC.exe`). A portable desktop build can skip that step.

PyInstaller builds for its host OS and Python architecture ([official manual](https://pyinstaller.org/en/stable/)). Build Windows `.exe`
files on Windows; a Linux/macOS environment cannot cross-compile them. An x64
build does not become an ARM64 or x86 native build by changing its filename.
Native extension modules and serial device drivers must match the target system.
Running Arduino hardware still requires the board's Windows driver and access to
its port. The supplied sensor simulation needs neither a board nor a driver.

The source editor also supports other Python versions as described in README.
The pinned release recipe is intentionally narrower. A Python or dependency
upgrade requires regenerating the lock and rerunning the executable tests.

## Dependency inventory

| Dependency | Pinned version | Purpose |
|---|---|---|
| CPython, standard library, Tcl/Tk | Python 3.15.1 | Editor, graph compiler, process runtime and UI |
| pyserial | 3.5 | Hardware serial ports; included in the desktop distribution |
| PyInstaller | 6.22.3 | Builds the desktop and standalone graph executables |
| pyinstaller-hooks-contrib | 2026.9 | Package discovery hooks used by PyInstaller |
| altgraph | 0.17.5 | PyInstaller dependency graph |
| packaging | 26.3 | Build version parsing |
| pefile | 2024.8.26 | Windows executable analysis |
| pywin32-ctypes | 0.2.3 | Windows resource access while building |
| setuptools | 84.2.1 | Build tooling |
| Inno Setup | 6.7.3 | Optional Windows installer compiler |

`requirements-build.txt` locks Python build dependencies;
`requirements-runtime.txt` lists pyserial separately. The editor, calculation, CSV, plotting and
simulated serial code otherwise use Python's standard library. Tkinter comes
from the Python installer rather than pip. Installed/custom Python node
libraries can introduce additional dependencies; graph dependency reports list
their imports. Install those packages in the build interpreter before packaging.
The standard desktop runner includes the standard library, Tk and pyserial.
An external build interpreter's packages do not become part of the installed
desktop runner. Use source mode or rebuild the desktop bundle with the required
modules when a node library needs additional packages inside the editor.

Keep the bundled license/notice files with the distribution. Python, Tcl/Tk,
pyserial, PyInstaller and its build dependencies retain their respective
licenses; PyInstaller's executable exception permits distributing its output.
Additional node libraries and their packages retain their own licenses.

## Desktop and installer

From a complete source checkout in PowerShell:

```powershell
& 'C:\Path\To\Python315\python.exe' -m venv .venv-build
& '.\.venv-build\Scripts\python.exe' -m pip install -r requirements-build.txt
.\build_windows.ps1 -Python '.\.venv-build\Scripts\python.exe' -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

The wrapper validates the pinned interpreter and installed dependencies. It does
not download packages or modify the chosen Python environment during a build.
If PowerShell disallows local scripts, invoke the same builder directly:
`& '.\.venv-build\Scripts\python.exe' tools\build_windows.py --iscc 'C:\Path\To\ISCC.exe'`.

For the portable desktop only:

```powershell
.\build_windows.ps1 -Python '.\.venv-build\Scripts\python.exe' -SkipInstaller
```

The output directory defaults to `dist` and can be changed with `-Output`.
Each desktop build requires a fresh output directory: if `OUTPUT\PyDrop` already exists, the build refuses to replace it so saved editable projects are preserved; use a new `-Output` path for a rebuild.
Distribute the **entire** `dist\PyDrop` directory, including `_internal`, for a
portable installation. The default installer artifact is
`dist\PyDrop-0.1.0-win-x64-setup.exe` (the architecture reflects the build Python).
The installer installs per user, creates a Start Menu entry, and provides an
uninstaller. It does not install Python globally.

`dist\build-manifest.json` records the interpreter, dependency versions and build
inputs. `dist\SHA256SUMS.txt` records artifact checksums. Keep those files and
build logs with each release. Rebuild from the same source, pinned interpreter
and dependency versions. This is a reproducible build recipe, not a promise of
byte-identical PE files: Windows tools and executable timestamps can affect
binary hashes. Compare manifests and rerun validation when rebuilding.

The checked-in recipe produces unsigned executables and installers. For a public
release, publisher signing is a separate release step; Windows may show an
unknown-publisher/SmartScreen prompt for unsigned downloads. Record checksums
after signing because signing changes the files.

## Package an exported graph separately

In the editor, save the editable project first, then click **Build EXE** beside
the generated Python/output tabs. Choose the build Python, executable name and
output folder, then choose **Dependency report** or **Build executable**. The
packaging interpreter needs the locked build dependencies; an
installed desktop runtime is not a replacement for a developer Python toolchain.
The same operation is available without the editor:

```powershell
# Inspect dependencies without requiring PyInstaller or executing the graph.
& 'C:\Path\To\BuildPython\python.exe' tools\build_graph.py examples\sensor_dashboard.nodeforge.json --name SimulatedSensor --output-dir dist\graph --report-only

# Use a fresh output/name after a report-only run, or choose a different output directory.
& 'C:\Path\To\BuildPython\python.exe' tools\build_graph.py examples\sensor_dashboard.nodeforge.json --name SimulatedSensor --output-dir dist\graph-release

# An exported Python program is also accepted; retain its editable companion.
& 'C:\Path\To\BuildPython\python.exe' tools\build_graph.py exported.py --project project.nodeforge.json --name MyProgram --output-dir dist\programs
```

The graph build creates `OUTPUT\NAME\` containing:

- `executable\NAME.exe`, a console executable by default; `--onedir` instead
  creates `executable\NAME\NAME.exe` and its required companion files.
- `source\`, with the exported Python and an unchanged copy of the editable
  project when supplied. Packaging never replaces or deletes the original files.
- `dependency-report.json`, describing imports, required/optional modules,
  available distributions and warnings.
- `build-manifest.json` and `build-log.txt`, recording the build and diagnostics.

Console mode keeps printed output and `input()` usable. Graphs that use table or
plot previews can still create Tk windows. A packaged program is an output
artifact; reopen its saved editable project in PyDrop to change it, then rebuild.

Review dependency warnings before distribution. Simulation-only serial graphs
can run without pyserial; a hardware serial graph requires it. A missing required
import stops the build with the module and a suggested corrective action.
Syntax errors, invalid graphs, missing PyInstaller, unsupported target OS and
failed PyInstaller runs produce actionable errors. Failed output is retained in
a `NAME.failed-...` directory with its dependency report and log. A name already
present in the output directory is rejected to protect previous outputs.

Static analysis cannot fully infer dynamic imports, plugins or external files.
The report analyzes the supplied script and declared library dependencies;
it does not recursively audit the source of imported local helper modules.
Use repeatable `--hidden-import MODULE` flags for dynamically imported modules
and `--add-data 'SOURCE=RELATIVE_DEST'` for runtime data. Dependencies must be
installed in the chosen build interpreter. Paths such as CSV inputs, output
locations and serial ports remain deployment settings: include the needed data
files and choose paths usable on the recipient's machine. Test the exact
executable from an artifact-only directory before sharing it.

## Verify on a clean Windows machine

`tools\clean_windows_test.ps1` needs only the built artifacts and PowerShell.
It removes Python from its process PATH, clears Python environment settings,
uses an isolated user-data directory, and copies the applications into paths
containing spaces. It runs both frozen self-tests, starts the desktop, runs the
standalone sensor graph, and tests install/reinstall/uninstall while checking
that an editable project survives unchanged. It retains JSON reports, stdout,
stderr and the transcript. It never recursively deletes a project directory.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\clean_windows_test.ps1 `
    -ArtifactRoot dist `
    -GraphExecutable dist\graph-release\SimulatedSensor\executable\SimulatedSensor.exe `
    -ReportDirectory dist\smoke-reports
```

A reduced-PATH smoke run on a development PC verifies bundle independence, but
does **not** establish that the operating system is clean. For that requirement,
use a fresh Windows VM or Windows Sandbox without installing Python:

1. Create `C:\PyDropSandbox\input` and `C:\PyDropSandbox\reports` on the host.
2. Copy `dist\PyDrop` to `input\PyDrop`, the installer to `input`, and
   `tools\clean_windows_test.ps1` to `input`. Copy the onefile sensor executable
   to `input\graph\SimulatedSensor.exe`. Do not copy the source checkout or a venv.
3. Edit both host paths in `packaging\windows-sandbox.wsb` if necessary, then
   open that file on a Windows host with Sandbox enabled. Its input mapping is
   read-only; reports are written to the separate report folder. Networking is
   disabled. See Microsoft's [Sandbox configuration examples](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-sample-configuration)
   for the required host feature and mapping behavior.
4. Inspect `reports\smoke-summary.json`: `ok` must be true. Retain the runtime
   reports, transcript, OS version and released artifact hashes as evidence.
5. In the same fresh VM, launch the installed or portable editor visibly. Open
   the sensor example, run it, confirm temperature/light and disconnected status,
   edit a value, save/reopen a project outside the install folder, and export
   Python. Exercise Input and a CSV/plot example. Close the editor normally.
   For a hardware release test, also connect/unplug an Arduino and confirm the
   editor stays responsive and explains the disconnection.

The automated desktop check verifies startup and continued process life; the
manual interaction check verifies the visible UI. The GitHub Actions workflow
`.github\workflows\windows-distribution.yml` builds on a fresh hosted Windows
runner and performs the artifact-only smoke test. Its runner has development
tools installed, so keep a separate Sandbox/VM result for the clean-OS release
claim. See `VALIDATION.md` for what was actually executed for this revision.

## Build failures

- **Python/Tk unavailable:** select a full, matching-architecture CPython install
  with Tcl/Tk. Recreate the build environment with the pinned versions.
- **PyInstaller/dependency missing:** install the locked build requirements into
  the interpreter used by the build. Packages installed into another venv do not
  satisfy this environment.
- **Inno Setup missing:** pass `-Iscc` with the compiler path, or use
  `-SkipInstaller` when only the portable bundle is needed.
- **Missing graph import/data:** inspect `dependency-report.json`, install the
  required distribution, and supply hidden imports/data explicitly when needed.
- **Compiler failure or frozen startup failure:** inspect the retained build
  log and smoke stderr. Reproduce from an artifact-only folder with Python
  removed from PATH. Do not discard `_internal` from an onedir distribution.
- **A different OS/architecture is requested:** build on that OS with the matching
  interpreter and dependencies; do not rename another platform's executable.
