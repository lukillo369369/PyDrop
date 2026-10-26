# Validation

## Windows desktop distribution — 2026-10-26

Built PyDrop 0.1.0 x64 on Windows 11 build 26300 with CPython 3.15.1,
PyInstaller 6.22.3, the exact requirements-build.txt lock, and Inno Setup 6.7.3.
Produced the per-user installer, complete portable folder/ZIP, build manifest,
third-party notices/licenses and SHA256SUMS.txt. Product name/version resources
were verified as PyDrop / 0.1.0. The native vector icon was visually inspected.

Also built the simulated sensor graph as a separate onefile console executable,
with dependency report, build log, hashes, exported Python and an unchanged
editable graph copy. Artifacts are in dist/graph-release/SimulatedSensor.

All **88 focused tests passed**: 23 distribution runtime, executable builder and
packaging UI tests; 62 core/data/example/serial/function/custom-formula
regressions; and three build-directory preservation checks. These verify frozen
resource paths, exclusive first-run template copying, user edits surviving an
upgrade, a separate bundled graph runner, external build-environment isolation,
static dependency analysis without executing source, retained build diagnostics,
project/source preservation, and refusal to replace an existing desktop bundle.

The artifact-only Windows smoke run passed with Python removed from PATH,
Python environment settings cleared, an isolated user-data directory, and only
copied release artifacts in the test working directory. Both portable and
installed runtimes passed all seven self-tests: compiler source resources, Tk,
pyserial, force calculation, simulated serial dashboard, interactive input and
CSV resources. Both desktop processes remained running after startup. The
standalone sensor executable printed the expected readings. Silent install,
reinstall and uninstall returned zero; the editable project sentinel retained
the same SHA256 after reinstall/uninstall, and the application executable was
removed. Windows product/version resources matched the runtime version.

A further frozen-editor integration run passed all seven checks: packaged module
loading, isolated user paths, sensor dashboard Run, custom-formula result 110,
the external Python dependency-report builder launched from the frozen GUI,
editable snapshot preservation, and no Tk callback errors. Evidence is retained
in dist/packaged-editor-check.json; the harness is build/verify_packaged_editor.py.

Evidence: dist/smoke-reports-final/smoke-summary.json, the adjacent runtime JSON
reports/stdout/stderr/transcript, dist/build-manifest.json, dist/SHA256SUMS.txt,
and the graph build manifest/dependency report. The first harness run found a
Windows PowerShell 5.1 null ExitCode issue; retaining the process handle fixed it,
and the full installer lifecycle passed on the final run.

**A fresh Windows OS has not been tested here.** Windows Sandbox is absent and
no clean VM was supplied. This successful development-host test establishes
artifact independence from Python on PATH, not a clean-OS certification. The
Sandbox configuration, artifact-only smoke script and CI workflow are supplied
in BUILDING.md; the workflow has not been run remotely. Perform the documented
fresh-VM/Sandbox and visible editor checks before calling the release clean-OS
validated. Existing unrelated library/editor-tools suite limitations remain
documented in the serial validation entry below.

## Arduino serial nodes — 2026-10-26

Added Serial Connect, Serial Send, Serial Read Line, Parse Value, and Serial
Disconnect, optional pyserial hardware access, and a dependency-free simulator.
Hardware I/O runs in the existing child process. Port discovery runs in a worker
thread. Live status is queued to Tk; Stop and run completion clear the connection
indicator. Reads, writes, input buffers, and simulation queues are bounded.

All **56 focused checks passed** with the configured Windows/Python 3.15.1
interpreter: 46 serial runtime/compiler/editor checks, four example checks, and
six existing keyboard/function-editor regressions. They cover explicit connection
requirements, simulation without pyserial, matching command/response protocol,
read timeout branches, partial lines, malformed UTF-8/numbers, unplugged-device
cleanup and reconnect, live status, asynchronous port discovery, Stop during
startup/read, completion status races, function draft timeout budgets, and
independent exported programs. Another 77 core/function/data/engineering compiler
checks passed during implementation.

Full discovery ran 335 checks: 297 passed, with 33 errors and five failures.
The 33 errors come from existing editor-tools/library GUI suites passing the
unsupported `library_store_path` constructor argument. One failure expects
existing library CLI flags absent from this version of nodeforge.py. Four
synthetic keyboard failures all passed in the isolated final run above.
These unrelated library/editor-tools integrations were not changed.

The simulated dashboard is distributed as an editable graph and a menu example.
The Arduino sketch was inspected against the simulator's READ / LED ON / LED OFF
protocol. No physical board was connected; hardware behavior was verified with
fake serial transports, and the sketch still needs upload/testing on a board.

## Restored direct mouse controls — 2026-10-25

Restored ordinary left-drag movement for node cards and left-drag panning on
empty canvas. Shift-drag retains additive box selection. A node-body press now
cancels an unfinished wire and begins moving in the same gesture. Dragging an
already selected card preserves its unapplied inspector draft and does not open
a property-validation dialog. Stationary clicks still apply on reselect.

Function-call opening and group double-click actions now wait until release,
allowing a quick second press to start a drag instead. The on-screen guides,
shortcut reference, README, and quick-start instructions describe these controls.

All **89 desktop tests passed** on Windows/Python 3.15.1. Five additional Tk
regressions cover plain background panning, moving with a pending wire, preserving
an invalid selected-card draft while moving, rapid function-call dragging, and
stationary function-call double-click opening. Existing movement, palette drops,
connections, groups, search, and undo/redo checks passed. Verification used
automated Tk mouse events; a manual desktop capture could not be completed.

```sh
python -m unittest discover -s tests -p '*gui*.py'
```

## Rapid click-and-drag fix — 2026-10-25

Reproduced a Tk event-dispatch regression: clicking a node and quickly pressing
again to drag routed the second press to the double-click handler, which skipped
normal drag setup. Unhandled double-clicks now use the ordinary canvas press
handler; group collapse and opening reusable functions keep their dedicated
double-click actions. The quick-start guide now describes box selection and the
middle/Alt-drag panning controls correctly.

All **84 desktop tests passed** on Windows/Python 3.15.1, including three new
regressions using timed Tk mouse events for rapid multi-node dragging with undo,
box selection, and group collapse. Existing first-click dragging, palette drops,
pin wiring, and function double-click tests also passed. No dependencies changed.

```sh
python -m unittest discover -s tests -p '*gui*.py'
```

## Graph editing tools — 2026-10-25

The complete Windows/Python 3.15.1 suite passed: **253 tests**, including all
existing desktop, compiler, engineering, reusable-function, and Python-library
checks. The 59 new checks comprise 28 graph-editing model tests, 11 automatic
layout tests, and 20 desktop interaction tests. No new dependencies were added.

Verified Space search, box/additive/toggle selection, selection dragging, cut,
copy/paste and duplicate with internal wires, unique names, reusable-function
closure, and data-only executable-library references. Undo/redo restores each
compound edit in one step; cancelling an unfinished drag cannot leave stale
node IDs after undo or project replacement.

Alignment/distribution and explicit automatic layout preserve code and wiring.
Layout tests verify execution order, ordered branch lanes, variable-height card
packing, disconnected components, deterministic crossing reduction, and fixed
unselected obstacles. Loading, adding nodes, fitting, panning, and minimap
navigation preserve manual node coordinates and do not invoke layout.

Group checks cover persistence, membership cleanup, clipboard remapping,
creation/rename/collapse/ungroup history, hidden internal cards/wires, projected
external connections, and moving multiple collapsed groups together. Group
metadata does not change generated program behavior. Entry/Text/Combobox/Spinbox
shortcuts retain native editing; new Edit/Layout/Undo/Redo controls remain visible
at the minimum 1050×700 window size. The minimap reflects collapsed groups.

Commands:

```sh
python -m unittest discover -s tests
python -m unittest discover -s tests -p test_graph_editing.py -v
python -m unittest discover -s tests -p test_graph_layout.py -v
python -m unittest discover -s tests -p test_editor_tools_gui.py -v
```

Desktop behavior was verified with automated Tk interactions; no new screenshot
review was performed.

## Versioned executable node libraries — 2026-10-25

Verified on Windows with the project's Python 3.15.1 interpreter. All 24 new
library checks passed: 19 backend/CLI tests and five desktop tests. Four affected
legacy custom-node/function-call checks and two existing search/typing checks
also passed on targeted reruns. The earlier 132-test non-GUI regression batch
passed before the final CLI check was added.

The complete 194-test suite was exercised. That run exposed an inspector
regression (library fields were eagerly built for legacy nodes), a control-
character validation gap, an outdated singleton search expectation, and one
intermittent Windows typing-focus failure. The inspector and validation were
corrected; search now checks exact-name priority while allowing related results.
All failed cases passed targeted reruns, including the typing-focus case. The
entire long suite was not repeated after those fixes.

Coverage includes:

- Authoring and saving reusable library files without installation or execution.
- Browsing implementations and scanning dependencies without importing their
  modules, including a dotted package with a detectable import side effect.
- Explicit installation consent, reviewed fingerprints, changed-file rejection,
  exact version coexistence, immutable definition versions, and tamper detection.
- Missing library/package reports when opening projects, with editable placeholder
  nodes and preserved port schemas/wires. Project files cannot embed implementations.
- Number/Text/Boolean/Quantity/Any ports, compatible units, named multiple outputs,
  atomic wire rejection, runtime type/shape errors, and reusable function bodies.
- Isolated standalone exports and CLI inspect/install/export/run boundaries.
- Palette category/name search, metadata/default editing, and the scrollable
  authoring form's Save control. Larger programs run through temporary files
  instead of exceeding Windows' command-line argument size limit.

The bundled sample library remains uninstalled. Test installation registries and
side-effect markers use temporary directories; no user library was installed or
changed. No new external Python dependency and no screenshot review were needed.

Focused commands:

```sh
python -m unittest discover -s tests -p test_node_libraries.py -v
python -m unittest discover -s tests -p test_node_library_gui.py -v
```

## Engineering quantities — 2026-10-25

Verified with the project's Python 3.15.1 interpreter on Windows. The final
batch passed all 119 checks: 114 non-GUI tests and five focused engineering
desktop tests. This batch excluded the older `test_gui.py` suite, which had
reported five failures and one cleanup error before this feature; those older
desktop failures have not been revalidated or addressed here.

The new 18 unit tests cover supported unit scales, compound-unit parsing,
arithmetic and formula dimensions, incompatible conversion, finite magnitudes,
atomic wire replacement, defaults, persistence, and unitless compatibility.
Standalone files run with `-I` in temporary directories outside the project,
including a runtime mismatch through reusable function arguments and an attempt
to discard units through Convert to Number. Exports embed the same runtime;
no additional Python packages are required.

All example lessons round-trip and execute, including the four new editable
engineering graphs (Force, Torque, Ohm's Law, and Electrical Power). Desktop
checks cover rendering/inspection/execution of all four lessons, editable unit
fields, property rejection without changing the live graph, rejected canvas
wires retaining the original connection, and search for every new node name.
No new screenshot review was performed.

Focused commands:

```sh
python -m unittest discover -s tests -p test_engineering.py -v
python -m unittest discover -s tests -p test_engineering_gui.py -v
python -m unittest discover -s tests -p test_examples.py -v
```

Tested with Python 3.12 and Tk 9.0 on Linux using a virtual desktop.

- 25 compiler, data-flow, persistence, formula, and standalone-export checks.
- 12 desktop interaction checks, including actual Tk mouse events for dragging
  nodes from the palette, moving nodes, and connecting pins.
- Verified UI output for successful execution and a division-by-zero error.
- Visually inspected the interface at 1480 × 900 and 1050 × 700.

## Windows verification — 2026-10-24

Verified with the project's Python 3.15.1 environment on Windows:

- All 37 existing tests passed (25 core and 12 desktop tests).
- All 14 desktop tests passed after adding error-feedback regression coverage.
- Verified creating nodes with palette mouse events, click/drag connections,
  saving and reopening graphs, standalone Python export, and graph execution.
- Verified compile errors identify the function that must run first.
- Verified runtime division errors select the failed node, open its inspector,
  preserve its error highlight after execution animation, and explain the fix.
- Verified an upstream Math failure identifies the Math node rather than Print.
- Verified correcting the formula and running successfully clears the highlight.

Commands: `python -m unittest discover -s tests -v` and
`python -m unittest discover -s tests -p test_gui.py -v`.
These are automated Tk interaction checks; no new visual screenshot review was
performed. macOS remains unverified. The Windows launcher uses the installed
Python launcher or `python` on PATH. No third-party Python packages are needed.

## Interface refinement verification

All 16 desktop tests passed after adding the minimap, centered zoom controls,
larger node headers, connection emphasis, port feedback, and tooltips.
The two new checks verify that minimap navigation and zoom preserve graph data
and that pending wires identify matching port types. Existing editing,
persistence, export, execution, and error-highlighting checks also passed.
Screenshot capture failed, so no visual screenshot review is claimed.

## Searchable node picker

The 16 existing desktop checks passed with the node picker integrated. Three
picker checks passed on a targeted rerun after fixing focus setup in the Escape
key test: type-to-search, alias filtering, empty results, arrow navigation,
Escape and outside-click cancellation, existing Main exclusion, placement at
zoomed canvas coordinates, and undo. Typing f starts a Function search without
triggering the Fit shortcut. No additional node types are introduced here.

## Convert, Text Join, and interactive Input

Verified 35 distinct core/example tests and 23 desktop tests across the main
runs and targeted follow-ups. New coverage includes Number/Text/Boolean
conversion, invalid and non-finite numeric text, exact failed-node reporting,
text composition with numeric and Unicode values, input execution ordering,
sequential questions, blank responses, cancellation, save/reopen, standalone
export, and the interactive command-line runner.

Desktop checks verified the searchable new node types and inspector fields,
the input panel, Send/Cancel behavior, waiting beyond the active execution
limit without being killed, and conversion errors selecting the Convert node.
A separate timeout test confirmed noninteractive execution is still bounded.
The original desktop workflows also passed. No screenshot review was performed.

Try Examples > Interactive calculator and answer 2 and 3 to see Sum: 5.0.
An editable copy is examples/interactive_calculator.nodeforge.json.

## Boolean Logic, Clamp, and Random

Verified 47 distinct core/example tests and all 25 desktop tests. The initial
core run had one timeout while launching the existing temperature-conversion
export; both example tests passed on a targeted rerun, covering all 12 lessons.
All other core tests passed in the initial run.

New checks cover AND/OR/NOT truth tables, NOT ignoring b, strict Boolean input
errors, combined comparison/enabled decisions, clamp endpoints and equal bounds,
connected bound overrides, reversed bounds, integer versus decimal random
ranges, blank and fixed seeds, repeated seeded runs, shared random results,
execution ordering, and invalid seed/mode properties. Each new lesson also
passed save/reopen and standalone-export execution checks.

Desktop checks cover searchable new nodes, property editing, all three new
examples, seeded reruns, Decimal mode, and exact failed-node highlighting with
helpful messages. Existing desktop workflows passed. No screenshot review was
performed.

New Examples menu entries and saved graphs:
- Temperature controller: examples/controlled_temperature.nodeforge.json
- Limit power: examples/clamp_power.nodeforge.json
- Dice roller: examples/dice.nodeforge.json

## Typed Python to editable nodes

All 8 statement-translator tests and all 28 desktop tests passed. Coverage
includes constant assignments, literal output, arithmetic precedence, text
joining, Boolean logic, comparisons, float/str conversions, nested input, and
seeded random calls. Unsupported statements, duplicate names, reserved names,
unknown references, and node-limit failures leave the original graph unchanged.

Desktop tests verify typing a first character on the canvas and pressing Enter,
configured node placement, preview matching, reference wiring, undo/redo of a
whole inserted group, execution after connecting Main, save/reopen, and keeping
invalid input available for correction. Existing editor workflows also passed.

Translation handles supported single statements and pydrop's existing node
semantics; it is not a full Python-file importer. No screenshot review was
performed.

## Typing accessibility fix

All 9 targeted desktop checks passed. A permanent code/node-name Entry and Add
button now appear above the canvas. Actual Tk key events verified typing and
Enter insertion without a prior canvas click, view-center placement, clearing
successful input, Ctrl+Space focus, invalid-code recovery, and existing floating
picker placement, undo, persistence, and cancellation. Small mouse movements no
longer clear the canvas typing target; an actual pan still clears it.

Close and reopen a running pydrop window to load this UI change.

## Short scripts, automatic wiring, layout, and value inspection

All 65 core/translator/example tests passed (55 regression tests and 10 script
workflow tests). All 36 desktop tests passed in the main run; targeted follow-ups
also passed for script imports, selected-action wiring, nodes added by name, and
large-group visibility. This covers 37 distinct desktop checks.

Verified expression and Input assignments, named references across multiple
statements, line-specific failures with no partial import, preserved execution
successors when inserting after Main or a selected action, automatic Main creation
for connected action imports, and graph save/reopen. Imported cards were checked
for overlap; a 40-node group was verified fully inside the viewport after import
and Fit graph. Imports undo/redo as one group.

Desktop checks verified Script preview without graph mutation, Add group,
interactive answers, invalid-source recovery, captured value/type display,
clearing stale values after an edit, and existing keyboard and editor workflows.
Runtime metadata is kept separate from printed program output; standalone exports
do not emit inspection metadata. No screenshot review was performed.

## Drop custom-language frontend

All 9 Drop language tests and 5 targeted script-editor tests passed. Coverage
includes let/ask/say statements, arithmetic precedence, parentheses, comparison
phrases, booleans, quoted keyword text, Unicode, comments, keyword case handling,
original line numbers in errors, atomic failures, custom statement keywords,
interactive .drop execution, and standalone Python export.

Desktop checks verified selecting Drop, loading its example, preview without
mutation, importing and answering its question, running the program, preserving
source when switching languages, invalidating stale previews, and existing
Python script import/automatic wiring/value-inspection workflows.

The parser only translates supported syntax into the existing node system. The
language name, statement keywords, word operators, and sample are editable in
drop_language.py. Sample sources: examples/shopping.drop and examples/logic.drop.
No screenshot review was performed.

## Create node and reusable custom calculations

All 81 core/compiler/language/example checks and all 42 desktop checks passed.
The 3 creator-focused desktop checks also passed after refining search priority
for template names containing words such as and.

Verified 1-3 named numeric inputs, defaults, connected-value overrides, formula
validation/normalization, preserved math helpers, exact failed-node reporting,
portable graph save/reopen, standalone export without a template library,
atomic library saving, reload, duplicate-name rejection, and malformed-library
errors. Existing compiler regressions passed after sharing the execution path.

Desktop checks exercised the top Create node button, visible creation controls,
Test formula result, Create & add, palette/search reuse, automatic execution
insertion, duplicate/invalid definitions without partial writes, undo/redo,
per-instance edits, and preservation of saved template defaults. All test
library writes used temporary directories. No screenshot review was performed.
# Reusable function graphs

The full 145-test regression run passed. After the final tracing-import change,
all 15 function compiler checks and 9 targeted desktop/tracing checks passed,
including two additional regressions: argument names matching tracing imports
and creation through the actual dialog. This covers 147 distinct checks.

Verified named inputs and multiple return pins, independent call defaults,
connected argument overrides, standalone callable Python functions, two calls to
one shared body, function-local scope, nested calls, branch-specific returns,
missing returns, missing/stale definitions, recursion rejection, interactive
function bodies, scoped tracing, and malformed nested libraries. Legacy graphs
retain their original serialized shape and execution behavior.

Desktop checks exercised the creation dialog, Function Call pins and inspector,
actual double-click navigation, modal nested editing, applying/discarding drafts,
running drafts without changing the project, last-invocation value inspection,
project undo/redo, and save/reopen. Draft and serialized properties are deeply
copied so canceled edits cannot alter the shared definition. The two-rectangle
example is included in the menu and examples/reusable_rectangle.nodeforge.json.
No screenshot review was performed.

# Project save workflow

The 127-test suite completed with 126 passes and one existing custom-node test
failure caused by loading the user's local library into a test expecting an
empty library. That test now clears its in-memory templates when switching to
its temporary library; the targeted rerun passed. No user library was modified.

All four new desktop checks passed: Save As preserves the original and switches
later saves to the chosen destination; cancellation and write failure retain
the project path and saved-state marker; Open discovers both .drop.json and
.nodeforge.json; and Save As has a shortcut and respects invalid inspector edits.
No screenshot review was performed.
