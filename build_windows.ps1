[CmdletBinding()]
param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$SkipInstaller,
    [string]$Iscc = "",
    [string]$Output = "dist"
)
$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($Python)) {
    $Python = Join-Path $ProjectRoot $Python
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python not found: $Python. Create .venv with CPython (including Tcl/Tk), install requirements-build.txt, or pass -Python with the interpreter path."
}
$BuildArguments = @((Join-Path $ProjectRoot "tools\build_windows.py"), "--output", $Output)
if ($SkipInstaller) { $BuildArguments += "--skip-installer" }
if ($Iscc) { $BuildArguments += @("--iscc", $Iscc) }
& $Python @BuildArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

