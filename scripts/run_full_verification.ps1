$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Ruff = Join-Path $ProjectRoot ".venv\Scripts\ruff.exe"
$Mypy = Join-Path $ProjectRoot ".venv\Scripts\mypy.exe"
$Pytest = Join-Path $ProjectRoot ".venv\Scripts\pytest.exe"

Write-Host "[1/3] Ruff (linting)..." -ForegroundColor Cyan
& $Ruff check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/3] Mypy --strict (type checking)..." -ForegroundColor Cyan
& $Mypy src
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/3] Pytest (unit + integration + E2E GUI offscreen)..." -ForegroundColor Cyan
$prevQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Pytest -v -m "not live"
    $pytestExit = $LASTEXITCODE
} finally {
    if ($null -eq $prevQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $prevQtPlatform
    }
}
if ($pytestExit -ne 0) { exit $pytestExit }

Write-Host "Pelna weryfikacja zakonczona sukcesem." -ForegroundColor Green
