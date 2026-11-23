from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import configure_table, format_iso_local, make_item


class LogsView(QWidget):
    """Filtrowalny podgląd ustrukturyzowanych logów backendu z eksportem do pliku."""

    def __init__(
        self,
        api: ApiClient,
        palette: ThemePalette,
        *,
        on_status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._palette = palette
        self._on_status = on_status
        self._last_entries: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Logi diagnostyczne")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.level_combo = QComboBox()
        self.level_combo.addItem("Wszystkie poziomy", "all")
        for lvl in ("debug", "info", "warning", "error"):
            self.level_combo.addItem(lvl.upper(), lvl)
        self.level_combo.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.level_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filtruj po zdarzeniu, domenie, ID...")
        self.search_edit.setMinimumWidth(240)
        self.search_edit.returnPressed.connect(self.refresh)
        header.addWidget(self.search_edit)

        export_btn = QPushButton("Eksportuj JSON")
        export_btn.clicked.connect(self._export_logs)
        header.addWidget(export_btn)

        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.table = QTableWidget()
        configure_table(self.table, ["Czas", "Poziom", "Moduł", "Zdarzenie", "Szczegóły"])
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 220)
        root.addWidget(self.table, stretch=1)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def refresh(self) -> None:
        level = str(self.level_combo.currentData() or "all")
        search = self.search_edit.text().strip() or None
        run_in_background(
            lambda: self._api.get_logs(level=level, search=search, limit=250),
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd pobierania logów: {err}"),
        )

    def _render(self, entries: list[dict[str, Any]]) -> None:
        self._last_entries = entries
        self.table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            lvl = str(item.get("level") or "info").lower()
            color = (
                self._palette.negative
                if lvl in {"error", "critical"}
                else self._palette.warning
                if lvl == "warning"
                else self._palette.text_secondary
            )
            details = item.get("details") or {}
            details_txt = "  ".join(f"{k}={v}" for k, v in details.items()) if details else "—"

            self.table.setItem(row, 0, make_item(format_iso_local(item.get("timestamp"))))
            self.table.setItem(row, 1, make_item(lvl.upper(), color=color))
            self.table.setItem(row, 2, make_item(str(item.get("logger") or "—")))
            self.table.setItem(row, 3, make_item(str(item.get("event") or "")))
            self.table.setItem(row, 4, make_item(details_txt))

    def _export_logs(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Eksport logów diagnostycznych",
            "price_tracker_logs.json",
            "Pliki JSON (*.json)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._last_entries, fh, indent=2, ensure_ascii=False)
        self._on_status(f"Wyeksportowano {len(self._last_entries)} wpisów logów.")
