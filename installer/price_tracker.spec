# -*- mode: python ; coding: utf-8 -*-
import configparser
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
ASSETS_DIR = SRC_DIR / "price_tracker" / "desktop" / "assets"
ICON_PATH = ASSETS_DIR / "upt_logo.ico"

alembic_cfg = configparser.ConfigParser(defaults={"here": str(PROJECT_ROOT)})
alembic_cfg.read(PROJECT_ROOT / "alembic.ini", encoding="utf-8")
raw_script_loc = alembic_cfg.get("alembic", "script_location", fallback="migrations")
alembic_script_path = Path(raw_script_loc)
if not alembic_script_path.is_absolute():
    alembic_script_path = (PROJECT_ROOT / alembic_script_path).resolve()

if not alembic_script_path.exists():
    raise FileNotFoundError(
        f"Nie znaleziono katalogu migracji Alembic: {alembic_script_path}"
    )

datas = [
    (str(PROJECT_ROOT / "alembic.ini"), "."),
    (str(alembic_script_path), "alembic_migrations"),
    (str(ASSETS_DIR), "price_tracker/desktop/assets"),
]
datas += collect_data_files("playwright")

hiddenimports = [
    "aiosqlite",
    "sqlite3",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.ext.asyncio",
    "alembic",
    "alembic.runtime.migration",
    "alembic.script",
    "keyring.backends.Windows",
    "win32ctypes.core",
    "win32ctypes.core.ctypes",
    "win32ctypes.pywin32",
    "selectolax.parser",
    "PySide6.QtSvg",
    "PySide6.QtXml",
    "pyqtgraph",
    "logging.config",
]
hiddenimports += collect_submodules("price_tracker")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("playwright")

excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "pytest",
    "mypy",
    "ruff",
    "tkinter",
]

a = Analysis(
    [str(PROJECT_ROOT / "installer" / "entrypoint.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalPriceTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="UniversalPriceTracker",
)
