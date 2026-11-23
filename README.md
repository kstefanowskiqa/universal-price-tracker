# Universal Price Tracker

Wieloplatformowa aplikacja do monitorowania cen produktów w dowolnych sklepach internetowych, analizy rzeczywistej historii cen oraz wysyłania powiadomień przez Telegram Bot API.

## Architektura

- **Backend (`src/price_tracker/`):** FastAPI + SQLAlchemy 2.0 (async) + Alembic, 6-warstwowy silnik ekstrakcji cen (`httpx` + `selectolax` + leniwy `Playwright` fallback), ochrona SSRF, bazodanowy harmonogram zadań z limiterem per-domena i exponential backoff, stanowy silnik reguł alertów oraz klient Telegram Bot API.
- **Klient desktopowy (`src/price_tracker/desktop/`):** PySide6 + PyQtGraph (motywy Dark/Light, 8 widoków roboczych). Komunikuje się z backendem wyłącznie przez REST API — może uruchamiać backend lokalnie na Windows 11 albo łączyć się ze zdalnym serwerem VPS / Docker.

## Szybki start na Windows 11

```powershell
# 1. Środowisko i zależności
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,scraping,dev]"
.\.venv\Scripts\playwright.exe install chromium

# 2. Uruchomienie aplikacji desktopowej (automatycznie migruje bazę i startuje lokalny backend)
.\scripts\run_desktop.ps1
```
