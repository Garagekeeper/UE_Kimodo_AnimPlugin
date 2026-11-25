<#
.SYNOPSIS
  Kimodo Animation plugin tests.

.DESCRIPTION
  unit         Scripts/panel.py logic with a stubbed `unreal` module. Any Python 3.11. About 1 s.
  integration  kimodo_worker.py vs the kimodo_generate.py CLI (same seed -> same BVH). Needs Kimodo installed. About 1 min.
  editor       Headless UnrealEditor-Cmd runs of the panel: takes, xN, worker lifecycle, root motion, take cache.
               Needs Kimodo installed and a project with SKM_Manny_Simple whose Plugins folder has this plugin
               (KimodoTest by default). Close that project's editor first. About 5-8 min.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1             # unit only
  powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1 -All
  powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1 -Editor -Only take_cache
#>
param(
    [switch]$Integration,
    [switch]$Editor,
    [switch]$All,
    [string]$Only = "",
    [string]$Project = "C:\Ju\UEProjects\KimodoTest\KimodoTest.uproject",
    [string]$Engine = "C:\Program Files\Epic Games\UE_5.8"
)

# unittest와 UnrealEditor-Cmd는 결과를 stderr로도 쓴다. Windows PowerShell 5.1에서 "Stop"이면 그 줄이 오류로 바뀌어
# 스크립트가 멈추므로 Continue로 두고, 성공 여부는 종료 코드와 결과 파일로만 판단한다.
$ErrorActionPreference = "Continue"
$plugin = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $env:LOCALAPPDATA "KimodoAnimation\venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { Join-Path $Engine "Engine\Binaries\ThirdParty\Python3\Win64\python.exe" }
$env:PYTHONIOENCODING = "utf-8"
$failures = @()

function Invoke-Suite([string]$name, [string]$folder) {
    Write-Host "== $name ($python)" -ForegroundColor Cyan
    Push-Location $plugin
    try {
        & $python -I -m unittest discover -s $folder -v 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { $script:failures += $name }
    } finally { Pop-Location }
}

Invoke-Suite "unit" "Tests/unit"
if ($Integration -or $All) { Invoke-Suite "integration" "Tests/integration" }

if ($Editor -or $All) {
    $cmd = Join-Path $Engine "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
    if (Get-Process UnrealEditor -ErrorAction SilentlyContinue) {
        Write-Warning "An Unreal Editor is running. If it has $Project open, close it first."
    }
    $results = Join-Path ([IO.Path]::GetTempPath()) ("kimodo_editor_tests_{0}.jsonl" -f (Get-Date -Format yyyyMMdd_HHmmss))
    $env:KIMODO_PLUGIN_DIR = $plugin
    $env:KIMODO_TEST_RESULTS = $results
    $scripts = Get-ChildItem (Join-Path $plugin "Tests\editor") -Filter "test_*.py" |
        Where-Object { -not $Only -or $_.BaseName -eq "test_$Only" }
    foreach ($script in $scripts) {
        $name = $script.BaseName -replace '^test_', ''
        Write-Host "== editor: $name" -ForegroundColor Cyan
        $started = Get-Date
        & $cmd $Project -run=pythonscript "-script=$($script.FullName)" -unattended -nop4 -nosplash -NullRHI -stdout 2>&1 |
            Where-Object { $_ -match 'KIMODO_TEST' -and $_ -notmatch 'LogInit: Display' } | ForEach-Object { "$_" }
        $entries = @()
        if (Test-Path $results) {
            $entries = Get-Content $results -Encoding utf8 | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.test -eq $name }
        }
        $done = $entries | Where-Object { $_.done }
        $failed = @($entries | Where-Object { $_.PSObject.Properties.Name -contains 'ok' -and -not $_.ok })
        $passed = @($entries | Where-Object { $_.ok })
        $seconds = [int]((Get-Date) - $started).TotalSeconds
        if (-not $done) {
            Write-Host "   no completion record (editor crashed or the script did not run)" -ForegroundColor Red
            $failures += "editor:$name"
        } elseif ($failed.Count -gt 0) {
            $failed | ForEach-Object { Write-Host "   FAIL $($_.check): $($_.detail)" -ForegroundColor Red }
            $failures += "editor:$name"
        } else {
            Write-Host "   $($passed.Count) checks passed in ${seconds}s" -ForegroundColor Green
        }
    }
    Write-Host "editor results: $results"
}

if ($failures.Count) {
    Write-Host "FAILED: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "ALL PASSED" -ForegroundColor Green
