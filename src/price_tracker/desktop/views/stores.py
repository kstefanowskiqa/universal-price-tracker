from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import EditStoreDialog, configure_table, make_item


class StoresView(QWidget):
    """Zarządzanie wykrytymi sklepami, adapterami, limitami zapytań i selektorami CSS."""

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
        self._stores: list[dict[str, Any]] = []
        self._adapters: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Sklepy i adaptery")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        edit_btn = QPushButton("Konfiguruj zaznaczony")
        edit_btn.setObjectName("PrimaryButton")
        edit_btn.clicked.connect(self._edit_selected)
        header.addWidget(edit_btn)

        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.adapters_strip = QLabel("Dostępne adaptery: ...")
        self.adapters_strip.setObjectName("MetricsStrip")
        root.addWidget(self.adapters_strip)

        self.table = QTableWidget()
        configure_table(
            self.table,
            [
                "Sklep",
                "Domena",
                "Adapter",
                "Oferty",
                "Skuteczność",
                "Odstęp (s)",
                "Tryb JS",
                "Aktywny",
                "Ostatni błąd",
            ],
        )
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 125)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 95)
        self.table.setColumnWidth(5, 85)
        self.table.setColumnWidth(6, 75)
        self.table.setColumnWidth(7, 75)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._edit_selected())
        root.addWidget(self.table, stretch=1)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def refresh(self) -> None:
        def _work() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            return self._api.list_stores(), self._api.list_adapters()

        run_in_background(
            _work,
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd pobierania sklepów: {err}"),
        )

    def _render(self, data: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        stores, adapters = data
        self._stores = stores
        self._adapters = adapters

        names = ", ".join(f"{a['display_name']} ({a['adapter_key']})" for a in adapters)
        self.adapters_strip.setText(f"Zarejestrowane adaptery: {names}")

        self.table.setRowCount(len(stores))
        for row, st in enumerate(stores):
            rate = st.get("success_rate_percent")
            rate_txt = f"{rate}%" if rate is not None else "—"
            enabled = bool(st["is_enabled"])
            self.table.setItem(row, 0, make_item(str(st["name"])))
            self.table.setItem(row, 1, make_item(str(st["domain"])))
            self.table.setItem(row, 2, make_item(str(st["adapter_key"])))
            self.table.setItem(
                row,
                3,
                make_item(f"{st['active_offers_count']}/{st['offers_count']}", align_right=True),
            )
            self.table.setItem(row, 4, make_item(rate_txt, align_right=True))
            self.table.setItem(
                row, 5, make_item(str(st["rate_limit_seconds"]), align_right=True)
            )
            self.table.setItem(row, 6, make_item("Tak" if st["requires_js"] else "Auto"))
            self.table.setItem(
                row,
                7,
                make_item(
                    "Tak" if enabled else "Nie",
                    color=self._palette.positive if enabled else self._palette.text_muted,
                ),
            )
            self.table.setItem(row, 8, make_item(str(st.get("last_error_summary") or "—")))

    def _edit_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._stores)):
            return
        dlg = EditStoreDialog(self._api, self._stores[row], self._adapters, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_status("Zaktualizowano konfigurację sklepu.")
            self.refresh()
