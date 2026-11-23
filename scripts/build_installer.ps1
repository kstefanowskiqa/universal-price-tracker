$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"

Write-Host "[1/4] Sprawdzanie zasobow graficznych (ikony)..." -ForegroundColor Cyan
$IcoPath = Join-Path $ProjectRoot "src\price_tracker\desktop\assets\upt_logo.ico"
if (-not (Test-Path $IcoPath)) {
    & $Python .\scripts\build_branding_assets.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[2/4] Sprawdzanie PyInstaller w .venv..." -ForegroundColor Cyan
if (-not (Test-Path $PyInstaller)) {
    & $Python -m pip install -e ".[desktop,scraping,build]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[3/4] Pakowanie aplikacji przez PyInstaller..." -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "build", "dist"
& $PyInstaller --clean --noconfirm .\installer\price_tracker.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[4/4] Kompilacja instalatora Inno Setup..." -ForegroundColor Cyan
$IsccCandidates = @(
    (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }

$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    Write-Host ""
    Write-Host "Aplikacja przenosna gotowa w: dist\UniversalPriceTracker\UniversalPriceTracker.exe" -ForegroundColor Yellow
    Write-Host "Nie znaleziono Inno Setup 6 (ISCC.exe), aby skompilowac plik Setup .exe." -ForegroundColor Yellow
    Write-Host "Zainstaluj Inno Setup 6 poleceniem:" -ForegroundColor Cyan
    Write-Host "  winget install -e --id JRSoftware.InnoSetup" -ForegroundColor White
    Write-Host "a nastepnie uruchom ponownie: .\scripts\build_installer.ps1" -ForegroundColor Cyan
    exit 0
}

& $Iscc .\installer\setup.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Instalator gotowy w: dist_installer\UniversalPriceTracker-Setup-0.1.0.exe" -ForegroundColor Green
