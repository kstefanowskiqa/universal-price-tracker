from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import (
    AVAILABILITY_LABELS,
    configure_table,
    format_iso_local,
    format_money,
    make_item,
)


class DashboardView(QWidget):
    """Przegląd: zwarty pasek metryk, szybkie dodawanie URL, ostatnie zmiany i błędy."""

    def __init__(
        self,
        api: ApiClient,
        palette: ThemePalette,
        *,
        on_quick_add: Callable[[str], None],
        on_open_history: Callable[[int, int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._palette = palette
        self._on_quick_add = on_quick_add
        self._on_open_history = on_open_history

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Nagłówek + szybkie dodawanie po URL
        top_row = QHBoxLayout()
        title = QLabel("Przegląd")
        title.setObjectName("PageTitle")
        top_row.addWidget(title)
        top_row.addStretch(1)

        self.quick_url_edit = QLineEdit()
        self.quick_url_edit.setPlaceholderText("Wklej URL produktu, aby dodać do monitorowania...")
        self.quick_url_edit.setMinimumWidth(360)
        self.quick_url_edit.returnPressed.connect(self._trigger_quick_add)
        top_row.addWidget(self.quick_url_edit)

        add_btn = QPushButton("Dodaj URL")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self._trigger_quick_add)
        top_row.addWidget(add_btn)

        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(refresh_btn)
        root.addLayout(top_row)

        # Kompaktowy pasek metryk tekstowych (zamiast ciężkich kafelków)
        self.metrics_strip = QLabel("Ładowanie danych...")
        self.metrics_strip.setObjectName("MetricsStrip")
        root.addWidget(self.metrics_strip)

        # Dwie tabele w układzie poziomym / pionowym Splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        changes_box = QWidget()
        changes_layout = QVBoxLayout(changes_box)
        changes_layout.setContentsMargins(0, 4, 0, 4)
        changes_layout.setSpacing(6)
        lbl_changes = QLabel("Ostatnie zmiany cen (kliknij dwukrotnie, aby otworzyć wykres)")
        lbl_changes.setObjectName("SectionTitle")
        changes_layout.addWidget(lbl_changes)

        self.changes_table = QTableWidget()
        configure_table(
            self.changes_table,
            ["Produkt", "Sklep", "Poprzednia", "Aktualna", "Zmiana", "Wykryto"],
        )
        self.changes_table.setColumnWidth(0, 290)
        self.changes_table.setColumnWidth(1, 130)
        self.changes_table.setColumnWidth(2, 115)
        self.changes_table.setColumnWidth(3, 115)
        self.changes_table.setColumnWidth(4, 155)
        self.changes_table.cellDoubleClicked.connect(self._on_change_double_click)
        changes_layout.addWidget(self.changes_table)
        splitter.addWidget(changes_box)

        attention_box = QWidget()
        attention_layout = QVBoxLayout(attention_box)
        attention_layout.setContentsMargins(0, 4, 0, 0)
        attention_layout.setSpacing(6)
        lbl_att = QLabel("Wymagają uwagi (błędy pobierania lub brak w magazynie)")
        lbl_att.setObjectName("SectionTitle")
        attention_layout.addWidget(lbl_att)

        self.attention_table = QTableWidget()
        configure_table(
            self.attention_table,
            ["Produkt", "Sklep", "Dostępność", "Błędy", "Kod", "Diagnostyka"],
        )
        self.attention_table.setColumnWidth(0, 260)
        self.attention_table.setColumnWidth(1, 120)
        self.attention_table.setColumnWidth(2, 110)
        self.attention_table.setColumnWidth(3, 60)
        self.attention_table.setColumnWidth(4, 140)
        attention_layout.addWidget(self.attention_table)
        splitter.addWidget(attention_box)

        splitter.setSizes([380, 240])
        root.addWidget(splitter, stretch=1)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def _trigger_quick_add(self) -> None:
        url = self.quick_url_edit.text().strip()
        self.quick_url_edit.clear()
        self._on_quick_add(url)

    def _on_change_double_click(self, row: int, _col: int) -> None:
        item = self.changes_table.item(row, 0)
        if item is None:
            return
        ids = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(ids, tuple) and len(ids) == 2:
            self._on_open_history(int(ids[0]), int(ids[1]))

    def refresh(self) -> None:
        run_in_background(
            self._api.get_dashboard,
            on_success=self._render,
            on_error=lambda err: self.metrics_strip.setText(f"Błąd pobierania danych: {err}"),
        )

    def _render(self, data: dict[str, Any]) -> None:
        self.metrics_strip.setText(
            f"Produkty: {data['total_products']}   ·   "
            f"Monitorowane oferty: {data['total_offers']} "
            f"(aktywne: {data['active_offers']}, wstrzymane: {data['paused_offers']})   ·   "
            f"Spadki (24h): {data['price_drops_24h']}   ·   "
            f"Wzrosty (24h): {data['price_rises_24h']}   ·   "
            f"Wyprzedane: {data['out_of_stock_offers']}   ·   "
            f"Błędy: {data['offers_with_errors']}"
        )

        changes = data.get("recent_changes") or []
        self.changes_table.setRowCount(len(changes))
        for row, ch in enumerate(changes):
            label = str(ch["product_name"])
            if ch.get("variant_label"):
                label = f"{label} ({ch['variant_label']})"
            curr = str(ch.get("currency") or "PLN")
            diff = Decimal(str(ch["change_amount"]))
            pct = Decimal(str(ch["change_percent"]))
            sign = "+" if diff > 0 else ""
            diff_txt = f"{sign}{diff:.2f} {curr} ({sign}{pct:.2f}%)"
            diff_color = self._palette.positive if diff < 0 else self._palette.negative

            self.changes_table.setItem(
                row,
                0,
                make_item(label, user_data=(ch["product_id"], ch["offer_id"])),
            )
            self.changes_table.setItem(row, 1, make_item(str(ch["store_name"])))
            self.changes_table.setItem(
                row, 2, make_item(format_money(ch["previous_price"], curr), align_right=True)
            )
            self.changes_table.setItem(
                row, 3, make_item(format_money(ch["current_price"], curr), align_right=True)
            )
            self.changes_table.setItem(
                row, 4, make_item(diff_txt, align_right=True, color=diff_color)
            )
            self.changes_table.setItem(row, 5, make_item(format_iso_local(ch.get("changed_at"))))

        attention = data.get("attention_items") or []
        self.attention_table.setRowCount(len(attention))
        for row, item in enumerate(attention):
            label = str(item["product_name"])
            if item.get("variant_label"):
                label = f"{label} ({item['variant_label']})"
            avail_key = str(item.get("availability_status") or "unknown")
            avail_txt = AVAILABILITY_LABELS.get(avail_key, avail_key)
            failures = int(item.get("consecutive_failures") or 0)
            err_code = str(item.get("last_error_code") or "none")
            color = self._palette.negative if failures > 0 else self._palette.warning

            self.attention_table.setItem(row, 0, make_item(label))
            self.attention_table.setItem(row, 1, make_item(str(item["store_name"])))
            self.attention_table.setItem(row, 2, make_item(avail_txt, color=color))
            self.attention_table.setItem(row, 3, make_item(str(failures), align_right=True))
            self.attention_table.setItem(row, 4, make_item(err_code))
            self.attention_table.setItem(
                row, 5, make_item(str(item.get("last_error_message") or "—"))
            )
