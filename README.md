<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="src/price_tracker/desktop/assets/logo_dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="src/price_tracker/desktop/assets/logo_light.svg">
  <img alt="Universal Price Tracker" src="src/price_tracker/desktop/assets/logo_light.svg" width="96" height="96">
</picture>

# Universal Price Tracker

**A production-grade desktop & self-hosted system for tracking product prices and availability across online stores, analyzing real price history, and receiving instant Telegram alerts.**

[![Latest Release](https://img.shields.io/github/v/release/kstefanowskiqa/universal-price-tracker?style=flat-square&color=0ea5e9)](https://github.com/kstefanowskiqa/universal-price-tracker/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6%20%2B%20PyQtGraph-41cd52?style=flat-square&logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0%20Async-d71f00?style=flat-square)](https://www.sqlalchemy.org/)
[![Code Style: Ruff](https://img.shields.io/badge/lint-ruff-261230?style=flat-square)](https://docs.astral.sh/ruff/)
[![Type Checked: Mypy Strict](https://img.shields.io/badge/mypy-strict-2a6db2?style=flat-square)](https://mypy-lang.org/)

[Download for Windows 11](https://github.com/kstefanowskiqa/universal-price-tracker/releases/latest) · [Architecture](#architecture) · [Quick Start](#quick-start) · [Docker / VPS](#self-hosted-deployment-docker--postgresql) · [Development](#development--verification)

</div>

---

## Overview

**Universal Price Tracker** is engineered for reliable, long-term price monitoring across arbitrary e-commerce stores—not just single-site scripts. It cleanly separates its **async FastAPI backend** from its **native PySide6 desktop client**, allowing you to run everything locally out of the box on Windows 11 or deploy the monitoring engine 24/7 on a Linux VPS with Docker and PostgreSQL.

Every recorded price point is real, stored with exact `Decimal` precision and UTC timestamps, and guarded against malformed scrapes, anti-bot pages, and alert spam.

---

## Key Features

### Multi-Layer Price Extraction Engine

- **6-layer extraction pipeline:** Store-specific adapters → JSON-LD (Schema.org) → Microdata → Open Graph → CSS selectors → Semantic HTML heuristics (resistant to installment and related-product noise), with a lazy **Playwright** fallback for JavaScript-rendered pages.
- **Extensible store adapters:** Ships with `UniversalAdapter`, declarative per-domain CSS overrides, and reference store adapters (including **x-kom**, **H&M**, and **Victoria's Secret**).
- **Transparent failure diagnostics:** Classifies scrape issues cleanly (`BLOCKED_WAF`, `BLOCKED_CAPTCHA`, `RATE_LIMITED`, `AUTH_REQUIRED`, `TIMEOUT`, `PARSE_PRICE_NOT_FOUND`, etc.) and uses a **Sanity Guard** so bad readings never pollute price history.
- **Database-backed scheduler & queue:** Persistent `next_check_at` scheduling with global and per-domain concurrency/rate limits, jitter, exponential backoff retries, restart recovery, and automated job history retention.

### Product Catalog & Real Price Analytics

- **Multi-store & multi-variant data model:** Separates canonical products from individual tracked store URLs and variants (`UNIQUE(url, variant_label)`), complete with categories, custom tags, search, and manual overrides.
- **Exact financial & time handling:** Custom `MoneyDecimal` column type (rejects `float` to prevent rounding errors) and strict `UTCDateTime` timestamps.
- **Interactive charts & metrics:** Append-only real measurement history (no synthetic interpolation), min/max/average price statistics, absolute and percentage deltas, 6 time windows (`24h`, `7d`, `30d`, `90d`, `180d`, `All`), and multi-store comparison on step charts (`stepMode`).

### Smart Notifications (Telegram Bot API)

- **7 alert rule types:** Price drop, price increase, target price threshold, percentage drop, back in stock, out of stock, and monitoring error.
- **Anti-spam state machine:** Stateful hysteresis, configurable cooldown periods, and deduplication (`dedup_key`) prevent duplicate notifications on consecutive checks.
- **Secure secret storage:** Telegram bot tokens are stored in **Windows Credential Manager** locally (via `keyring`) or injected via environment variables on servers—never saved in SQLite or source code.

### Modern Desktop Client (PySide6 + PyQtGraph)

- **8 integrated workspaces:** Dashboard, Products, Price History, Notifications, Stores, Jobs, Settings, and Logs.
- **Polished UX:** Dark and Light themes, tabular numbers in tables, responsive background API workers, and theme-aware **Step Drop** branding.
- **Zero-config local mode or remote mode:** Automatically runs Alembic migrations and manages a local `127.0.0.1` FastAPI backend with a session token, or connects to a remote Docker/VPS backend from **Settings**.

---

## Architecture

The desktop client never imports database or scraping modules directly—all communication goes strictly through the authenticated REST API:

```text
┌─────────────────────────────────────────┐
│       Desktop Client (PySide6)          │
│  8 Views · PyQtGraph · Dark/Light UI    │
└───────────────────┬─────────────────────┘
                    │ Authenticated REST API (Local 127.0.0.1 or Remote HTTPS)
┌───────────────────▼─────────────────────┐
│        Backend API (FastAPI)            │
│  Catalog · Analytics · Monitoring · Sys │
├───────────────────┬─────────────────────┤
│  Scheduler Worker │  Notification Svc   │
│  Per-Domain Limit │  Hysteresis · Dedup │
│  Retry / Backoff  │  Telegram Bot API   │
├───────────────────┴─────────────────────┤
│        6-Layer Scraping Engine          │
│  httpx + selectolax · Playwright · SSRF │
├─────────────────────────────────────────┤
│   SQLAlchemy 2.0 (Async) + Alembic      │
│   SQLite (WAL) local | PostgreSQL 16    │
└─────────────────────────────────────────┘
```

### Security & Engineering Highlights

- **SSRF Protection:** Validates URLs, DNS resolution, HTTP redirects, and Playwright requests against private/loopback network ranges.
- **Structured Logging:** `structlog` JSON file logging with automatic secret/token masking.
- **Clean Packaging:** Built for Windows 11 using PyInstaller + Inno Setup 6, with Playwright automatically falling back to Windows 11's built-in **Microsoft Edge** when needed.

---

## Quick Start

### Option 1: Windows 11 Installer (Recommended)

1. Download **`UniversalPriceTracker-Setup-0.1.0.exe`** from the [Latest Release](https://github.com/kstefanowskiqa/universal-price-tracker/releases/latest).
2. Run the installer (installs per-user under `%LOCALAPPDATA%\Programs\Universal Price Tracker` — no admin privileges or Python installation required).
3. Launch **Universal Price Tracker** from the Start Menu or Desktop shortcut.

> **Note on Windows SmartScreen:** Because release binaries are not yet signed with a commercial code-signing certificate, click **More info → Run anyway** on first launch. Your database, logs, and settings are stored in `%LOCALAPPDATA%` and preserved across future upgrades.

---

### Option 2: Run from Source (Windows 11 / Development)

**Prerequisites:** Python **3.12+**

```powershell
# 1. Clone the repository
git clone [https://github.com/kstefanowskiqa/universal-price-tracker.git](https://github.com/kstefanowskiqa/universal-price-tracker.git)
cd universal-price-tracker

# 2. Create a virtual environment and install dependencies
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,scraping,dev]"
.\.venv\Scripts\playwright.exe install chromium

# 3. Launch the desktop app (auto-migrates SQLite and starts the local backend)
.\scripts\run_desktop.ps1
```

You can also launch the app directly via Python:

```powershell
.\.venv\Scripts\python.exe -m price_tracker.desktop.app
```

---

### Option 3: Self-Hosted Deployment (Docker & PostgreSQL)

Deploy the backend and scheduler 24/7 on any Linux server or VPS using Docker Compose:

```bash
# 1. Clone the repository and prepare environment variables
git clone [https://github.com/kstefanowskiqa/universal-price-tracker.git](https://github.com/kstefanowskiqa/universal-price-tracker.git)
cd universal-price-tracker
cp .env.example .env
```

Edit `.env` and set strong values for:

- `PT_API_TOKEN`
- `POSTGRES_PASSWORD`
- `PT_SECRET_TELEGRAM_BOT_TOKEN` _(optional, if using Telegram alerts)_

Start the stack (PostgreSQL 16 + FastAPI backend with automatic Alembic migrations and Playwright Chromium):

```bash
docker compose up -d --build
```

The API listens on `127.0.0.1:8765` by default (ready to place behind a TLS reverse proxy such as Caddy or Nginx). To connect your Windows desktop app to your server, open **Settings → Connection / Server**, switch to **Remote backend server**, enter your HTTPS URL and `PT_API_TOKEN`, and restart the client.

---

## Telegram Notifications Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the bot token.
2. Retrieve your Telegram `chat_id` (for example, send a message to your bot and check `getUpdates`).
3. Open **Settings → Telegram Bot API** in the desktop app, enter your Chat ID and bot token (saved securely in Windows Credential Manager), and click **Send test message**.
4. Configure per-offer alert rules in the **Notifications** tab.

---

## Configuration

See [`.env.example`](.env.example) for all supported environment variables:

| Variable                            | Default               | Description                                                        |
| :---------------------------------- | :-------------------- | :----------------------------------------------------------------- |
| `PT_ENVIRONMENT`                    | `local`               | Runtime mode (`local` or `server` / `production`).                 |
| `PT_DATABASE_URL`                   | _(SQLite in AppData)_ | Database connection string (e.g., `postgresql+asyncpg://...`).     |
| `PT_API_HOST`                       | `127.0.0.1`           | Host interface for the FastAPI server.                             |
| `PT_API_PORT`                       | `8765`                | Port for the FastAPI server.                                       |
| `PT_API_TOKEN`                      | _(auto in local)_     | Bearer token required for API requests (mandatory in server mode). |
| `PT_LOG_LEVEL`                      | `INFO`                | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).           |
| `PT_LOG_JSON_FILE`                  | `true`                | Enables rotating structured JSON log files.                        |
| `PT_DEFAULT_CHECK_INTERVAL_SECONDS` | `3600`                | Default monitoring interval in seconds.                            |
| `PT_MAX_CONCURRENT_HTTP_CHECKS`     | `5`                   | Maximum simultaneous HTTP check workers.                           |
| `PT_MAX_CONCURRENT_BROWSER_CHECKS`  | `2`                   | Maximum simultaneous Playwright browser checks.                    |
| `PT_SECRET_TELEGRAM_BOT_TOKEN`      | _(empty)_             | Telegram bot token for server/Docker deployments.                  |

---

## Development & Verification

### Running the Full Quality Suite

The verification script executes **Ruff**, **Mypy (`--strict`)**, and all **89 Pytest tests** (unit, integration, Alembic migrations, store adapters, and offscreen PySide6 GUI E2E tests):

```powershell
.\scripts\run_full_verification.ps1
```

### Building the Windows Installer

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed on Windows:

```powershell
.\scripts\build_installer.ps1
```

The standalone installer is generated under `dist_installer\UniversalPriceTracker-Setup-<version>.exe`.

---

## Project Structure

```text
universal-price-tracker/
├── src/price_tracker/
│   ├── api/            # FastAPI app, REST routers, Pydantic schemas, auth
│   ├── config/         # Pydantic settings, OS paths, SecretStore (keyring/env)
│   ├── core/           # Domain enums, exceptions, SSRF guard
│   ├── db/             # SQLAlchemy 2.0 async models, MoneyDecimal, Alembic migrations
│   ├── desktop/        # PySide6 app, 8 views, PyQtGraph charts, themes, SVG/ICO assets
│   ├── logging/        # Structlog configuration and token masking
│   ├── notifications/  # Alert rule evaluator, deduplication, Telegram Bot client
│   ├── scheduler/      # DB-backed worker queue and per-domain rate limiter
│   ├── scraping/       # 6-layer extractors, price parser, adapters, Playwright manager
│   ├── services/       # Catalog and price analytics services
│   └── server.py       # Backend entry point
├── tests/              # Unit, integration, adapter, and offscreen GUI E2E tests (89 tests)
├── installer/          # PyInstaller spec, entry point, and Inno Setup script
├── scripts/            # PowerShell helpers for dev, verification, branding, and packaging
├── Dockerfile          # Production Linux container image (Python 3.12 + Chromium)
├── docker-compose.yml  # Backend + PostgreSQL 16 stack
├── alembic.ini         # Alembic migration configuration
├── pyproject.toml      # Project metadata, dependencies, Ruff/Mypy/Pytest config
└── .env.example        # Environment variable template
```
