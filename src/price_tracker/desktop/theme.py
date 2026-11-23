from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ThemeName = Literal["dark", "light"]


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: ThemeName
    bg_app: str
    bg_sidebar: str
    bg_surface: str
    bg_elevated: str
    bg_hover: str
    bg_selected: str
    border: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_text: str
    positive: str
    negative: str
    warning: str
    chart_grid: str


DARK_PALETTE = ThemePalette(
    name="dark",
    bg_app="#111214",
    bg_sidebar="#15171B",
    bg_surface="#181A1F",
    bg_elevated="#1E2128",
    bg_hover="#232730",
    bg_selected="#252B36",
    border="#262A33",
    border_strong="#343A46",
    text_primary="#ECEEF2",
    text_secondary="#A6AEBC",
    text_muted="#737B8A",
    accent="#4C82C4",
    accent_hover="#5A91D4",
    accent_text="#FFFFFF",
    positive="#3FB978",
    negative="#E06262",
    warning="#D99B3C",
    chart_grid="#2A2F3A",
)

LIGHT_PALETTE = ThemePalette(
    name="light",
    bg_app="#F6F7F9",
    bg_sidebar="#EFF1F5",
    bg_surface="#FFFFFF",
    bg_elevated="#F9FAFB",
    bg_hover="#ECEFF4",
    bg_selected="#E4EAF3",
    border="#DDE1E8",
    border_strong="#C7CDD8",
    text_primary="#171A21",
    text_secondary="#4B5362",
    text_muted="#7A8291",
    accent="#2F66A9",
    accent_hover="#26558F",
    accent_text="#FFFFFF",
    positive="#1E8750",
    negative="#C43E3E",
    warning="#B57614",
    chart_grid="#E2E6ED",
)

CHART_SERIES_COLORS = (
    "#4C82C4",
    "#3FB978",
    "#D99B3C",
    "#C96B8B",
    "#6AA6A0",
    "#9B82C9",
)

_SVG_PATHS: dict[str, str] = {
    "dashboard": (
        '<rect x="3" y="3" width="7" height="7" rx="1"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1"/>'
    ),
    "products": (
        '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8'
        'a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>'
        '<path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>'
    ),
    "history": (
        '<path d="M3 3v18h18"/>'
        '<path d="m7 14 4-4 3 3 6-6"/>'
    ),
    "notifications": (
        '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
    ),
    "stores": (
        '<path d="M3 9h18l-1.5-5h-15Z"/>'
        '<path d="M4 9v11h16V9"/>'
        '<path d="M9 20v-6h6v6"/>'
    ),
    "jobs": (
        '<line x1="8" y1="6" x2="21" y2="6"/>'
        '<line x1="8" y1="12" x2="21" y2="12"/>'
        '<line x1="8" y1="18" x2="21" y2="18"/>'
        '<circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06'
        'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09'
        'a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06'
        'a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09'
        'a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06'
        'a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09'
        'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06'
        'a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09'
        'a1.65 1.65 0 0 0-1.51 1Z"/>'
    ),
    "logs": (
        '<polyline points="4 17 10 11 4 5"/>'
        '<line x1="12" y1="19" x2="20" y2="19"/>'
    ),
}


def get_palette(name: ThemeName) -> ThemePalette:
    return LIGHT_PALETTE if name == "light" else DARK_PALETTE


def make_svg_icon(name: str, color: str, size: int = 16) -> QIcon:
    body = _SVG_PATHS.get(name, _SVG_PATHS["dashboard"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def build_stylesheet(p: ThemePalette) -> str:
    """Buduje spójny arkusz QSS pozbawiony gradientów, ciężkich cieni i zbędnych ozdobników."""
    return f"""
    QMainWindow, QDialog {{
        background-color: {p.bg_app};
        color: {p.text_primary};
        font-family: "Segoe UI Variable", "Segoe UI", "Inter", sans-serif;
        font-size: 13px;
    }}
    QWidget {{
        color: {p.text_primary};
        font-family: "Segoe UI Variable", "Segoe UI", "Inter", sans-serif;
        font-size: 13px;
    }}
    QFrame#Sidebar {{
        background-color: {p.bg_sidebar};
        border-right: 1px solid {p.border};
    }}
    QLabel#AppTitle {{
        color: {p.text_primary};
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 0.2px;
        padding: 4px 6px;
    }}
    QLabel#PageTitle {{
        color: {p.text_primary};
        font-size: 16px;
        font-weight: 600;
    }}
    QLabel#SectionTitle {{
        color: {p.text_secondary};
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.4px;
    }}
    QLabel#MutedText {{
        color: {p.text_muted};
        font-size: 12px;
    }}
    QLabel#MetricsStrip {{
        background-color: {p.bg_surface};
        border: 1px solid {p.border};
        border-radius: 5px;
        padding: 8px 12px;
        color: {p.text_secondary};
        font-size: 12px;
    }}
    QPushButton#NavButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
        color: {p.text_secondary};
        text-align: left;
        padding: 7px 10px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#NavButton:hover {{
        background-color: {p.bg_hover};
        color: {p.text_primary};
    }}
    QPushButton#NavButton[active="true"] {{
        background-color: {p.bg_selected};
        border: 1px solid {p.border_strong};
        color: {p.text_primary};
        font-weight: 600;
    }}
    QPushButton {{
        background-color: {p.bg_elevated};
        border: 1px solid {p.border_strong};
        border-radius: 5px;
        color: {p.text_primary};
        padding: 6px 12px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p.bg_hover};
    }}
    QPushButton:disabled {{
        color: {p.text_muted};
        border-color: {p.border};
    }}
    QPushButton#PrimaryButton {{
        background-color: {p.accent};
        border: 1px solid {p.accent};
        color: {p.accent_text};
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {p.accent_hover};
        border-color: {p.accent_hover};
    }}
    QPushButton#DangerButton {{
        background-color: transparent;
        border: 1px solid {p.negative};
        color: {p.negative};
    }}
    QPushButton#DangerButton:hover {{
        background-color: {p.negative};
        color: #FFFFFF;
    }}
    QPushButton#RangeButton {{
        padding: 4px 10px;
        font-size: 12px;
    }}
    QPushButton#RangeButton[active="true"] {{
        background-color: {p.accent};
        border-color: {p.accent};
        color: {p.accent_text};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
        background-color: {p.bg_surface};
        border: 1px solid {p.border_strong};
        border-radius: 5px;
        color: {p.text_primary};
        padding: 5px 8px;
        selection-background-color: {p.accent};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border-color: {p.accent};
    }}
    QTableWidget {{
        background-color: {p.bg_surface};
        alternate-background-color: {p.bg_elevated};
        border: 1px solid {p.border};
        border-radius: 5px;
        gridline-color: {p.border};
        selection-background-color: {p.bg_selected};
        selection-color: {p.text_primary};
    }}
    QTableWidget::item {{
        padding: 4px 8px;
    }}
    QHeaderView::section {{
        background-color: {p.bg_elevated};
        color: {p.text_secondary};
        border: none;
        border-bottom: 1px solid {p.border};
        border-right: 1px solid {p.border};
        padding: 6px 8px;
        font-size: 12px;
        font-weight: 600;
        text-align: left;
    }}
    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: 5px;
        background-color: {p.bg_surface};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {p.bg_elevated};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        padding: 6px 14px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }}
    QTabBar::tab:selected {{
        background-color: {p.bg_surface};
        color: {p.text_primary};
        border-bottom-color: {p.bg_surface};
        font-weight: 600;
    }}
    QStatusBar {{
        background-color: {p.bg_sidebar};
        border-top: 1px solid {p.border};
        color: {p.text_secondary};
        font-size: 12px;
    }}
    QSplitter::handle {{
        background-color: {p.border};
    }}
    QScrollBar:vertical {{
        background: {p.bg_app};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border_strong};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    """
