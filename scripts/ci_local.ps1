<#
.SYNOPSIS
    Run the continuous integration of Auralis locally.

.DESCRIPTION
    The script runs exactly the checks of .github/workflows/ci.yml, in the
    same order and with the same commands, so a green run here means a
    green pipeline.  It exits with a non-zero status as soon as one check
    fails.

    The steps and the jobs of the pipeline they belong to:

        lint          lint          python tools/lint_check.py --require js-syntax
        hygiene       hygiene       python tools/hygiene_check.py
        unit          unit          python -m pytest tests -q -rs --ignore=tests/test_vst3_host.py
        journey       journey       python -m pytest tests/test_journey.py -q
        schema        schema        python tools/validate_schema.py --strict-modules
        schema-codegen schema       python tools/codegen_registry.py --check
        ui-css        ui-css        python qa/check_css_coverage.py
        ui-app        ui-app        python qa/verify_app.py
        ui-contracts  ui-contracts  python -m pytest tests/test_ui_contracts.py -q
        plugin-abi    plugin-abi    the core library, then the conformance tests
        plugin-build  plugin-build  python scripts/ci_plugin_gate.py --strict
        coverage      coverage      the suite with the coverage report

    The core library is built with the compiler of the machine: the
    Microsoft build tools on Windows, the C compiler of the system
    everywhere else.  The pipeline builds it the same way on the runner of
    the same platform.

.PARAMETER NoCoverage
    Do not run the coverage pass.  The coverage run repeats the whole test
    suite, so it is the slow part of the script.

.PARAMETER Only
    Run only the named steps (lint, hygiene, unit, journey, schema, ui-css,
    ui-app, ui-contracts, plugin-abi, plugin-build, coverage).  Every step
    runs when the parameter is not given.

.EXAMPLE
    scripts/ci_local.ps1
    scripts/ci_local.ps1 -NoCoverage
    scripts/ci_local.ps1 -Only lint,schema
#>
[CmdletBinding()]
param(
    [switch]$NoCoverage,
    [string[]]$Only = @()
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    # A native command that fails has to be reported by this script, not by
    # the preference, so that a skipped step is not turned into an error.
    $PSNativeCommandUseErrorActionPreference = $false
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$IsWindowsHost = ($env:OS -eq "Windows_NT")

# ------------------------------------------------------------------ python ---
function Resolve-Python {
    <#
        .SYNOPSIS
            Find the python interpreter of this machine.
        .DESCRIPTION
            The interpreter of the project is the one of the pythoncore
            build, not the one that happens to be on the PATH: the checks
            need numpy and pytest, and the bare python on the PATH is often
            a different one.  AURALIS_PYTHON wins over everything.
    #>
    $fromEnv = $env:AURALIS_PYTHON
    if ($fromEnv -and (Test-Path $fromEnv)) {
        return @{ Exe = (Resolve-Path $fromEnv).Path; Args = @() }
    }

    $local = $env:LOCALAPPDATA
    if ($local) {
        $candidates = Get-ChildItem (Join-Path $local "Python") -Directory -Filter "pythoncore-*" `
            -ErrorAction SilentlyContinue | Sort-Object Name -Descending
        foreach ($candidate in $candidates) {
            $exe = Join-Path $candidate.FullName "python.exe"
            if (Test-Path $exe) {
                return @{ Exe = $exe; Args = @() }
            }
        }
    }

    foreach ($candidate in @("python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            return @{ Exe = $found.Source; Args = @() }
        }
    }
    $launcher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($launcher) {
        return @{ Exe = $launcher.Source; Args = @("-3") }
    }
    throw "no python interpreter was found on PATH"
}

$Python = Resolve-Python

# No PYTHONHOME: the plug-in carries the standard library of its own
# interpreter and the core finds it by itself - the runtime directory of
# the family IS the home.  A run that sets the variable hides the defect
# the variable was there for.

function Invoke-Python {
    <#
        .SYNOPSIS
            Run the python interpreter with the given arguments.
    #>
    param([string[]]$Arguments)
    $all = @($Python.Args) + @($Arguments)
    & $Python.Exe @all
}

function Test-Interpreter {
    <#
        .SYNOPSIS
            Does the interpreter have the modules the checks need?
        .DESCRIPTION
            A missing module makes six steps fail with the same message.
            The run says it once, at the top, and stops.
    #>
    $probe = "import importlib.util; " +
        "missing = [name for name in ('numpy', 'pytest') " +
        "if importlib.util.find_spec(name) is None]; " +
        "print('|'.join(missing))"
    $output = (Invoke-Python @("-c", $probe) 2>&1) -join ""
    $missing = $output.Trim()
    if ($missing) {
        Write-Host "the interpreter $($Python.Exe) cannot import: $missing" -ForegroundColor Red
        Write-Host "the checks need the dependencies of requirements.txt:" -ForegroundColor Red
        Write-Host "    $($Python.Exe) -m pip install -r requirements.txt" -ForegroundColor Red
        exit 2
    }
}

function Invoke-Msvc {
    <#
        .SYNOPSIS
            Run a command inside the Microsoft build environment.
        .DESCRIPTION
            The same wrapper the plug-in build uses: tools\msvc.bat calls
            vcvars64.bat and then runs the command.
    #>
    param([string[]]$Arguments)
    $bat = Join-Path $Root "tools\msvc.bat"
    & cmd /c $bat @Arguments
}

Write-Host "python : $($Python.Exe) $(Invoke-Python @('--version') 2>&1)"
Write-Host "root   : $Root"
Test-Interpreter

# ------------------------------------------------------------------ runner ---
$Results = New-Object System.Collections.Generic.List[object]
$Failed = $false

function Test-Wanted {
    <#
        .SYNOPSIS
            Is the step wanted?  Without -Only every step is wanted.
        .DESCRIPTION
            The names may be given as a list or as one comma separated
            string, because a script that is started with -File only ever
            receives strings.
    #>
    param([string]$Name)
    if ($Only.Count -eq 0) {
        return $true
    }
    foreach ($item in $Only) {
        foreach ($part in ($item -split ",")) {
            if ($part.Trim() -eq $Name) {
                return $true
            }
        }
    }
    return $false
}

function Invoke-CiStep {
    <#
        .SYNOPSIS
            Run one check and record its outcome.
    #>
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action | Out-Host
    $code = $LASTEXITCODE
    if ($null -eq $code) {
        $code = 0
    }
    if ($code -eq 0) {
        Write-Host "    PASS" -ForegroundColor Green
        $Results.Add([pscustomobject]@{ Name = $Name; State = "PASS"; Detail = "exit 0" })
        return
    }
    Write-Host "    FAIL (exit $code)" -ForegroundColor Red
    $Results.Add([pscustomobject]@{ Name = $Name; State = "FAIL"; Detail = "exit $code" })
    $script:Failed = $true
}

function Write-CiSkip {
    <#
        .SYNOPSIS
            Record a step that was skipped, with the reason.
    #>
    param(
        [string]$Name,
        [string]$Reason
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Write-Host "    SKIPPED: $Reason" -ForegroundColor Yellow
    $Results.Add([pscustomobject]@{ Name = $Name; State = "SKIP"; Detail = $Reason })
}

# ------------------------------------------------------------------ checks ---
if (Test-Wanted "lint") {
    Invoke-CiStep "lint" { Invoke-Python @("tools/lint_check.py", "--require", "js-syntax") }
}

if (Test-Wanted "hygiene") {
    Invoke-CiStep "hygiene" { Invoke-Python @("tools/hygiene_check.py") }
}

if (Test-Wanted "unit") {
    Invoke-CiStep "unit" {
        Invoke-Python @("-m", "pytest", "tests", "-q", "-rs", "--ignore=tests/test_vst3_host.py")
    }
}

if (Test-Wanted "journey") {
    Invoke-CiStep "journey" { Invoke-Python @("-m", "pytest", "tests/test_journey.py", "-q") }
}

if (Test-Wanted "schema") {
    Invoke-CiStep "schema" { Invoke-Python @("tools/validate_schema.py", "--strict-modules") }
    Invoke-CiStep "schema-codegen" { Invoke-Python @("tools/codegen_registry.py", "--check") }
}

if (Test-Wanted "ui-css") {
    Invoke-CiStep "ui-css" { Invoke-Python @("qa/check_css_coverage.py") }
}

if (Test-Wanted "ui-app") {
    Invoke-CiStep "ui-app" { Invoke-Python @("qa/verify_app.py") }
}

if (Test-Wanted "ui-contracts") {
    Invoke-CiStep "ui-contracts" { Invoke-Python @("-m", "pytest", "tests/test_ui_contracts.py", "-q") }
}

if (Test-Wanted "plugin-abi") {
    # The core library needs a compiler.  On Windows that is the Microsoft
    # build tools, everywhere else the C compiler of the system.
    Write-Host ""
    Write-Host "==> plugin-abi" -ForegroundColor Cyan
    if ($IsWindowsHost) {
        $build = @($Python.Exe) + @($Python.Args) + @("plugins\abi\build_abi.py", "--msvc")
        Invoke-Msvc $build | Out-Host
        $built = $LASTEXITCODE
    }
    else {
        Invoke-Python @("tools/build_abi.py") | Out-Host
        $built = $LASTEXITCODE
    }
    if ($built -eq 0) {
        if ($IsWindowsHost) {
            # The check of the pipeline: the library has to load.
            Invoke-Python @("tools/build_abi.py", "--check") | Out-Host
            if ($LASTEXITCODE -ne 0) {
                $Results.Add([pscustomobject]@{ Name = "plugin-abi"; State = "FAIL"; Detail = "the library does not load" })
                $script:Failed = $true
            }
        }
        Invoke-CiStep "plugin-abi-tests" {
            Invoke-Python @("-m", "pytest", "tests/test_plugin_abi.py", "-q")
        }
    }
    else {
        Write-Host "    FAIL (the core library did not build)" -ForegroundColor Red
        $Results.Add([pscustomobject]@{ Name = "plugin-abi"; State = "FAIL"; Detail = "the core library did not build" })
        $script:Failed = $true
    }
}

if (Test-Wanted "plugin-build") {
    # The gate builds the core library, the host and the plug-in modules,
    # runs the host against every module and then runs the host gate of the
    # test suite.  --strict: a missing tool is a failure, not a skip.
    Invoke-CiStep "plugin-build" { Invoke-Python @("scripts/ci_plugin_gate.py", "--strict") }
}

if (Test-Wanted "coverage") {
    if ($NoCoverage) {
        Write-CiSkip "coverage" "disabled with -NoCoverage"
    }
    else {
        Invoke-Python @("-c", "import pytest_cov") 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            New-Item -ItemType Directory -Force -Path (Join-Path $Root "var\ci") | Out-Null
            Invoke-CiStep "coverage" {
                Invoke-Python @(
                    "-m", "pytest", "tests", "-q",
                    "--ignore=tests/test_vst3_host.py",
                    "--cov=aurcore",
                    "--cov=aurserver",
                    "--cov-report=term",
                    "--cov-report=xml:var/ci/coverage.xml",
                    "--cov-report=html:var/ci/htmlcov"
                )
            }
        }
        else {
            Write-CiSkip "coverage" "pytest-cov is not installed (pip install pytest-cov)"
        }
    }
}

# ------------------------------------------------------------------ summary ---
Write-Host ""
Write-Host "=============================================================================="
Write-Host " AURALIS LOCAL CI"
Write-Host "=============================================================================="
if ($Results.Count -eq 0) {
    Write-Host "no step was selected" -ForegroundColor Red
    Write-Host "------------------------------------------------------------------------------"
    Write-Host "RESULT: FAIL" -ForegroundColor Red
    exit 1
}
foreach ($result in $Results) {
    $colour = switch ($result.State) {
        "PASS" { "Green" }
        "FAIL" { "Red" }
        default { "Yellow" }
    }
    Write-Host ("  {0,-18} {1,-5} {2}" -f $result.Name, $result.State, $result.Detail) -ForegroundColor $colour
}
Write-Host "------------------------------------------------------------------------------"
if ($Failed) {
    Write-Host "RESULT: FAIL" -ForegroundColor Red
    exit 1
}
Write-Host "RESULT: PASS" -ForegroundColor Green
exit 0
