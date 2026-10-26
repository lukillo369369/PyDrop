# Third-party notices

PyDrop's Windows distribution retains dependency notices in
`_internal/third_party_licenses`. Its generated `inventory.json` lists license
files collected from the exact installed build dependencies. Keep these notices
with copies of the application and its portable archive.

CPython, its standard library, Tcl/Tk, and pyserial are runtime components.
`CPython-LICENSE.txt` is copied from the interpreter used for the build, including
the additional notices supplied by that Python distribution. Tcl/Tk notices are
also included explicitly. pyserial's BSD license is included explicitly because
the version 3.5 wheel does not contain a separate license file.

PyInstaller builds the executables and supplies their bootloader. Its
`COPYING.txt`, including the bootloader exception, is retained. altgraph,
packaging, pefile, pyinstaller-hooks-contrib, pywin32-ctypes, and setuptools are
build dependencies; collecting their notices does not install these tools in the
runtime application. Inno Setup builds the installer; its installed license text
is retained when available. Exact dependency versions are in
`requirements-build.txt` and the generated build manifest.

Vendored notice sources, copied without modification:

- [pyserial 3.5 license](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt)
  → `packaging/licenses/pyserial-3.5-LICENSE.txt`.
- [Tcl 9.0.4 license](https://github.com/tcltk/tcl/blob/core-9-0-4/license.terms)
  → `packaging/licenses/Tcl-license.terms`.
- [Tk 9.0.4 license](https://github.com/tcltk/tk/blob/core-9-0-4/license.terms)
  → `packaging/licenses/Tk-license.terms`.

The validated Windows build uses Tcl/Tk 9.0. Builds with another Python/Tcl/Tk
distribution must retain that distribution's corresponding notices as well.
This document records third-party components; it does not assign a license to
PyDrop's own source code or to the user's graphs.

