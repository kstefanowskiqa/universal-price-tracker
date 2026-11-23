$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Tworzenie srodowiska wirtualnego Python (.venv)..." -ForegroundColor Cyan
    py -m venv .venv
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Alembic = Join-Path $ProjectRoot ".venv\Scripts\alembic.exe"

Write-Host "Wykonywanie migracji bazy danych (Alembic)..." -ForegroundColor Cyan
& $Alembic upgrade head

Write-Host "Uruchamianie aplikacji desktopowej Universal Price Tracker..." -ForegroundColor Green
& $Python -m price_tracker.desktop.app
