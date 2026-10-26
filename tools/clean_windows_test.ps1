[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArtifactRoot,
    [string]$Installer,
    [string]$GraphExecutable,
    [string]$ReportDirectory,
    [switch]$SkipInstaller,
    [int]$TimeoutSeconds = 120
)

# Run this script from an artifact-only folder in a fresh Windows VM/Sandbox.
# Python is deliberately removed from PATH. No source checkout is required.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'The distribution smoke test requires Windows.' }
if ($TimeoutSeconds -lt 10) { throw 'TimeoutSeconds must be at least 10.' }
$artifactPath = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$portablePath = Join-Path $artifactPath 'PyDrop'
if (-not (Test-Path -LiteralPath (Join-Path $portablePath 'PyDrop.exe') -PathType Leaf)) {
    throw "Missing desktop bundle: $portablePath\PyDrop.exe. Point ArtifactRoot at the dist folder."
}
if (-not $SkipInstaller -and -not $Installer) {
    $setups = @(Get-ChildItem -LiteralPath $artifactPath -Filter 'PyDrop-*-setup.exe' -File)
    if ($setups.Count -ne 1) { throw 'Specify -Installer; exactly one PyDrop-*-setup.exe is required.' }
    $Installer = $setups[0].FullName
}
if ($Installer) { $Installer = (Resolve-Path -LiteralPath $Installer).Path }
if ($GraphExecutable) { $GraphExecutable = (Resolve-Path -LiteralPath $GraphExecutable).Path }
if (-not $GraphExecutable) { throw 'Specify -GraphExecutable for the exported simulated sensor executable.' }
if (-not $SkipInstaller) {
    $existingInstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E348C29-3838-4D1A-B7FC-1049F654A426}_is1'
    if (Test-Path -LiteralPath $existingInstallKey) {
        throw 'PyDrop is already registered for this Windows user. Run installer tests in a disposable VM/Sandbox or use -SkipInstaller to preserve the existing installation.'
    }
}

$runId = [Guid]::NewGuid().ToString('N')
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "PyDrop-distribution-$runId"
[void](New-Item -ItemType Directory -Path $testRoot)
if (-not $ReportDirectory) { $ReportDirectory = Join-Path $testRoot 'reports' }
[void](New-Item -ItemType Directory -Path $ReportDirectory -Force)
$reportPath = (Resolve-Path -LiteralPath $ReportDirectory).Path
$summary = [ordered]@{
    ok = $false
    scope = 'artifact-only Windows smoke test; OS cleanliness must be established by the operator'
    started_utc = [DateTime]::UtcNow.ToString('o')
    os = [Environment]::OSVersion.VersionString
    test_directory = $testRoot
    checks = [ordered]@{}
}
$environmentNames = @('PATH', 'PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'PYTHONSTARTUP',
    'PYTHONINSPECT', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'LOCALAPPDATA',
    'APPDATA', 'PYTHONNOUSERSITE')
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Quote-ProcessArgument([string]$Value) {
    # Windows file names cannot contain double quotes. None of these arguments
    # end with a directory separator, so ordinary Windows quoting is sufficient.
    if ($Value.Contains('"') -or $Value.EndsWith('\')) { throw "Unsupported process argument: $Value" }
    return '"' + $Value + '"'
}

function Invoke-TestProcess([string]$FilePath, [string[]]$ArgumentValues, [string]$Label,
    [int[]]$AllowedExitCodes = @(0)) {
    $arguments = @($ArgumentValues | ForEach-Object { Quote-ProcessArgument $_ }) -join ' '
    $stdout = Join-Path $reportPath "$Label.stdout.txt"
    $stderr = Join-Path $reportPath "$Label.stderr.txt"
    $startOptions = @{
        FilePath = $FilePath; WorkingDirectory = $testRoot; WindowStyle = 'Hidden';
        PassThru = $true; RedirectStandardOutput = $stdout; RedirectStandardError = $stderr
    }
    if ($arguments) { $startOptions.ArgumentList = $arguments }
    $process = Start-Process @startOptions
    # Keep a process handle before exit. Windows PowerShell 5.1 can otherwise
    # return a null ExitCode after WaitForExit on a fast redirected process.
    $processHandle = $process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "$Label exceeded $TimeoutSeconds seconds; see $stdout and $stderr."
    }
    $process.WaitForExit()
    $process.Refresh()
    if ($AllowedExitCodes -notcontains $process.ExitCode) {
        throw "$Label failed with exit code $($process.ExitCode); see $stdout and $stderr."
    }
    $summary.checks[$Label] = @{ exit_code = $process.ExitCode; stdout = $stdout; stderr = $stderr }
}

function Test-Runtime([string]$Directory, [string]$Label) {
    $runtime = Join-Path $Directory 'PyDropRuntime.exe'
    if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { throw "Missing runtime: $runtime" }
    $runtimeReport = Join-Path $reportPath "$Label.json"
    Invoke-TestProcess $runtime @('--self-test', '--report', $runtimeReport) $Label
    $result = Get-Content -LiteralPath $runtimeReport -Raw | ConvertFrom-Json
    if (-not $result.ok -or -not $result.frozen -or $result.app -ne 'PyDrop') {
        throw "$Label did not pass the frozen application self-test. See $runtimeReport."
    }
    $identity = (Get-Item -LiteralPath (Join-Path $Directory 'PyDrop.exe')).VersionInfo
    if ($identity.ProductName -ne 'PyDrop' -or $identity.ProductVersion -ne $result.version) {
        throw "$Label has incorrect Windows product/version metadata."
    }
    $summary.checks["$Label-windows-identity"] = @{ product = $identity.ProductName; version = $identity.ProductVersion }
}

function Test-Desktop([string]$Directory, [string]$Label) {
    $desktop = Join-Path $Directory 'PyDrop.exe'
    $process = Start-Process -FilePath $desktop -WorkingDirectory $testRoot -WindowStyle Hidden -PassThru
    try {
        # Allow Tcl/Tk and the initial graph to load. A launch-only check does not
        # substitute for the manual editor interaction checklist in BUILDING.md.
        Start-Sleep -Seconds 5
        $process.Refresh()
        if ($process.HasExited) { throw "$Label exited during startup with code $($process.ExitCode)." }
        $summary.checks[$Label] = @{ running_after_seconds = 5 }
        if ($process.CloseMainWindow()) { [void]$process.WaitForExit(10000) }
    }
    finally {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}

try {
    Start-Transcript -LiteralPath (Join-Path $reportPath 'smoke-transcript.txt') -Force | Out-Null
    # The all-users Python launcher can live at $env:SystemRoot\py.exe.
    # Keep the Windows utilities without exposing the Windows-root launcher.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    foreach ($name in $environmentNames) {
        if ($name -notin @('PATH', 'LOCALAPPDATA', 'APPDATA', 'PYTHONNOUSERSITE')) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
    }
    $env:PYTHONNOUSERSITE = '1'
    # Do not touch the operator's projects or node library during a local smoke run.
    $env:LOCALAPPDATA = Join-Path $testRoot 'UserData\Local'
    $env:APPDATA = Join-Path $testRoot 'UserData\Roaming'
    [void](New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:APPDATA -Force)
    $pythonCommands = @(Get-Command python, python3, py -ErrorAction SilentlyContinue)
    if ($pythonCommands.Count) {
        $discoveredPython = ($pythonCommands | ForEach-Object { "$($_.Name): $($_.Definition)" }) -join '; '
        throw "A Python command is still discoverable after PATH isolation: $discoveredPython"
    }
    $summary.checks['python_not_on_path'] = $true
    $summary.checks['source_checkout_not_required'] = $true

    $copiedDesktop = Join-Path $testRoot 'Portable PyDrop'
    Copy-Item -LiteralPath $portablePath -Destination $copiedDesktop -Recurse
    Test-Runtime $copiedDesktop 'portable-runtime'
    Test-Desktop $copiedDesktop 'portable-desktop-start'

    # Copy the entire graph output folder: onedir graphs need their _internal
    # directory; onefile graphs also work. Inputs remain in the artifact folder.
    $graphDirectory = Join-Path $testRoot 'Standalone Graph'
    Copy-Item -LiteralPath (Split-Path -Parent $GraphExecutable) -Destination $graphDirectory -Recurse
    $copiedGraph = Join-Path $graphDirectory (Split-Path -Leaf $GraphExecutable)
    Invoke-TestProcess $copiedGraph @() 'standalone-graph'
    $graphOutput = Get-Content -LiteralPath (Join-Path $reportPath 'standalone-graph.stdout.txt') -Raw
    if ($graphOutput -notmatch '(?i)Temperature \(C\):' -or $graphOutput -notmatch '(?i)Light \(ADC\):') {
        throw 'Standalone sensor graph completed without the expected temperature and light output.'
    }
    $summary.checks['standalone_sensor_output'] = $true

    if (-not $SkipInstaller) {
        $installedPath = Join-Path $testRoot 'Installed PyDrop'
        $installArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installedPath")
        Invoke-TestProcess $Installer $installArgs 'install'
        Test-Runtime $installedPath 'installed-runtime'
        Test-Desktop $installedPath 'installed-desktop-start'
        $projectsPath = Join-Path $env:LOCALAPPDATA 'PyDrop\projects'
        [void](New-Item -ItemType Directory -Path $projectsPath -Force)
        $editableExample = @(Get-ChildItem -LiteralPath $copiedDesktop -Recurse -File -Filter 'sensor_dashboard.nodeforge.json')
        if (-not $editableExample.Count) { throw 'The desktop bundle is missing its editable sensor example.' }
        $sentinel = Join-Path $projectsPath "clean-test-$runId.nodeforge.json"
        Copy-Item -LiteralPath $editableExample[0].FullName -Destination $sentinel
        $sentinelHash = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
        Invoke-TestProcess $Installer $installArgs 'upgrade-reinstall'
        if ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) {
            throw 'Reinstall changed the editable project.'
        }
        $uninstaller = Join-Path $installedPath 'unins000.exe'
        Invoke-TestProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') 'uninstall'
        if (Test-Path -LiteralPath (Join-Path $installedPath 'PyDrop.exe')) {
            throw 'Uninstall returned successfully but the application executable remains installed.'
        }
        if (-not (Test-Path -LiteralPath $sentinel -PathType Leaf) -or
            (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) {
            throw 'Uninstall removed or changed the editable project.'
        }
        $summary.checks['editable_project_preserved'] = @{ path = $sentinel; sha256 = $sentinelHash }
    }
    $summary.ok = $true
    Write-Host "PyDrop distribution smoke tests passed. Reports: $reportPath"
}
catch {
    $summary['error'] = $_.Exception.Message
    Write-Warning $_.Exception.Message
    throw
}
finally {
    $summary['finished_utc'] = [DateTime]::UtcNow.ToString('o')
    $summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $reportPath 'smoke-summary.json') -Encoding UTF8
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    try { Stop-Transcript | Out-Null } catch { }
    # Keep the unique test directory and editable sentinel for investigation.
    # No user project directory is ever recursively removed by this script.
}
