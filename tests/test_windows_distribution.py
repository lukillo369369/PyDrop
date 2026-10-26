"""Protection checks for release builds that might encounter editable projects."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pydrop_windows_build", ROOT/"tools"/"build_windows.py")
build_windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_windows)


class DesktopBuildPreservationTests(unittest.TestCase):
    def test_existing_bundle_preserves_editable_project_before_invoking_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/"release"
            bundle = output/"PyDrop"
            bundle.mkdir(parents=True)
            project = bundle/"my-project.drop.json"
            project.write_text('{"editable": true}\n', encoding="utf-8")
            original = project.read_bytes()
            with mock.patch.object(build_windows, "preflight") as preflight, mock.patch.object(build_windows, "_run") as run:
                with self.assertRaisesRegex(build_windows.BuildError, "already exists"):
                    build_windows.build(output, skip_installer=True)
                preflight.assert_not_called()
                run.assert_not_called()
            self.assertEqual(project.read_bytes(), original)

    def test_fresh_output_validation_creates_no_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/"fresh-release"
            self.assertEqual(build_windows._validate_output_directory(output), output.resolve())
            self.assertFalse(output.exists())

    def test_source_checkout_is_rejected(self):
        with self.assertRaisesRegex(build_windows.BuildError, "source checkout"):
            build_windows._validate_output_directory(ROOT)


@unittest.skipUnless(os.name == "nt", "Windows command discovery is required")
class SmokePathIsolationTests(unittest.TestCase):
    HARNESS = r'''
param([string]$SmokeScript, [string]$FixtureRoot)
$ErrorActionPreference = 'Stop'
# Load these against the real Windows directory before using the fake layout.
Import-Module Microsoft.PowerShell.Management, Microsoft.PowerShell.Utility
# Windows PowerShell initializes its .NET JSON serializer against SystemRoot.
$null = @{ preload = $true } | ConvertTo-Json
$realSystemRoot = $env:SystemRoot
$env:SystemRoot = Join-Path $FixtureRoot 'Windows'
$originalPath = "$FixtureRoot\Python;$FixtureRoot\WindowsApps;$env:SystemRoot;" + $env:PATH
$env:PATH = $originalPath
$initialCommands = @(Get-Command python, python3, py -CommandType Application -ErrorAction SilentlyContinue |
    ForEach-Object { $_.Definition })
$global:ArtifactLaunched = $false
$global:IsolatedPath = $null
# Stop before launching any real executable or installer.
function Start-Process {
    $global:ArtifactLaunched = $true
    $global:IsolatedPath = $env:PATH
    throw 'artifact-launch-sentinel'
}
$failure = $null
try {
    & $SmokeScript -ArtifactRoot (Join-Path $FixtureRoot 'artifacts') `
        -GraphExecutable (Join-Path $FixtureRoot 'artifacts\graph.exe') `
        -ReportDirectory (Join-Path $FixtureRoot 'reports') -SkipInstaller
}
catch { $failure = $_.Exception.Message }
finally { $env:SystemRoot = $realSystemRoot }
@{
    error = $failure
    launched = $global:ArtifactLaunched
    isolated_path = $global:IsolatedPath
    path_restored = ($env:PATH -ceq $originalPath)
    initial_commands = $initialCommands
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $FixtureRoot 'result.json') -Encoding UTF8
'''

    def run_smoke_guard(self, shell, *, system_python=False):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            for directory in ("Windows/System32/Wbem", "Windows/System32/WindowsPowerShell/v1.0",
                              "Python", "WindowsApps", "artifacts/PyDrop", "temp"):
                (fixture/directory).mkdir(parents=True, exist_ok=True)
            for executable in ("Windows/py.exe", "Python/python.exe", "WindowsApps/python3.exe",
                               "artifacts/PyDrop/PyDrop.exe", "artifacts/PyDrop/PyDropRuntime.exe",
                               "artifacts/graph.exe"):
                (fixture/executable).touch()
            if system_python:
                (fixture/"Windows/System32/python.exe").touch()
            harness = fixture/"harness.ps1"
            harness.write_text(self.HARNESS, encoding="utf-8")
            environment = os.environ.copy()
            environment.update(TEMP=str(fixture/"temp"), TMP=str(fixture/"temp"))
            completed = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness), str(ROOT/"tools/clean_windows_test.ps1"), str(fixture)],
                env=environment, capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads((fixture/"result.json").read_text(encoding="utf-8-sig"))
            summary = json.loads((fixture/"reports/smoke-summary.json").read_text(encoding="utf-8-sig"))
            result["fixture"] = str(fixture)
            result["summary"] = summary
            return result

    def available_shells(self):
        shells = [path for name in ("pwsh", "powershell") if (path := shutil.which(name))]
        if not shells:
            self.skipTest("PowerShell is unavailable")
        return shells

    def test_windows_root_launcher_and_user_python_are_removed(self):
        for shell in self.available_shells():
            with self.subTest(shell=shell):
                result = self.run_smoke_guard(shell)
                fixture = Path(result["fixture"])
                self.assertIn(str(fixture/"Windows/py.exe"), result["initial_commands"])
                self.assertEqual(result["error"], "artifact-launch-sentinel")
                self.assertTrue(result["launched"])
                self.assertTrue(result["path_restored"])
                self.assertTrue(result["summary"]["checks"]["python_not_on_path"])
                isolated = result["isolated_path"].split(";")
                self.assertIn(str(fixture/"Windows/System32"), isolated)
                for directory in ("Windows", "Python", "WindowsApps"):
                    self.assertNotIn(str(fixture/directory), isolated)

    def test_python_in_allowed_directory_still_fails_with_its_path(self):
        for shell in self.available_shells():
            with self.subTest(shell=shell):
                result = self.run_smoke_guard(shell, system_python=True)
                self.assertIn("A Python command is still discoverable after PATH isolation", result["error"])
                self.assertIn(str(Path(result["fixture"])/"Windows/System32/python.exe"), result["error"])
                self.assertFalse(result["launched"])
                self.assertTrue(result["path_restored"])
                self.assertNotIn("python_not_on_path", result["summary"]["checks"])


if __name__ == "__main__":
    unittest.main()

