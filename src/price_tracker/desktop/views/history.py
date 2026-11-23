from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
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
    PriceHistoryChart,
    configure_table,
    format_iso_local,
    format_money,
    make_item,
)

TIME_RANGES: list[tuple[str, str]] = [
    ("24h", "24h"),
    ("7d", "7d"),
    ("30d", "30d"),
    ("90d", "90d"),
    ("180d", "180d"),
    ("Cały okres", "all"),
]


class HistoryView(QWidget):
    """Szczegółowy wykres historii cen, statystyki okresowe i porównanie wielu sklepów."""

    def __init__(
        self,
        api: ApiClient,
        palette: ThemePalette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._palette = palette
        self._products: list[dict[str, Any]] = []
        self._current_range = "30d"
        self._pending_selection: tuple[int, int] | None = None
        self._request_seq = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # Wybór produktu, oferty i trybu porównania
        top = QHBoxLayout()
        title = QLabel("Historia cen")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addSpacing(12)

        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(260)
        self.product_combo.currentIndexChanged.connect(self._on_product_changed)
        top.addWidget(self.product_combo, stretch=1)

        self.offer_combo = QComboBox()
        self.offer_combo.setMinimumWidth(210)
        self.offer_combo.currentIndexChanged.connect(self._load_chart_data)
        top.addWidget(self.offer_combo)

        self.compare_check = QCheckBox("Porównaj wszystkie sklepy")
        self.compare_check.stateChanged.connect(self._on_compare_toggled)
        top.addWidget(self.compare_check)
        root.addLayout(top)

        # Przyciski zakresów czasowych
        range_row = QHBoxLayout()
        self._range_buttons: dict[str, QPushButton] = {}
        for label, key in TIME_RANGES:
            btn = QPushButton(label)
            btn.setObjectName("RangeButton")
            btn.clicked.connect(lambda _checked=False, r=key: self._set_range(r))
            range_row.addWidget(btn)
            self._range_buttons[key] = btn
        range_row.addStretch(1)
        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self._load_chart_data)
        range_row.addWidget(refresh_btn)
        root.addLayout(range_row)
        self._sync_range_buttons()

        # Kompaktowy pasek rzeczywistych statystyk
        self.stats_strip = QLabel("Wybierz produkt, aby wyświetlić rzeczywistą historię pomiarów.")
        self.stats_strip.setObjectName("MetricsStrip")
        root.addWidget(self.stats_strip)

        # Wykres PyQtGraph + tabela rzeczywistych pomiarów
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.chart = PriceHistoryChart(palette)
        splitter.addWidget(self.chart)

        self.points_table = QTableWidget()
        configure_table(
            self.points_table,
            ["Data pomiaru", "Sklep / wariant", "Cena", "Cena regularna", "Dostępność", "Źródło"],
        )
        self.points_table.setColumnWidth(0, 155)
        self.points_table.setColumnWidth(1, 200)
        self.points_table.setColumnWidth(2, 120)
        self.points_table.setColumnWidth(3, 120)
        self.points_table.setColumnWidth(4, 120)
        splitter.addWidget(self.points_table)
        splitter.setSizes([410, 210])
        root.addWidget(splitter, stretch=1)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.chart.apply_palette(palette)

    def select_offer(self, product_id: int, offer_id: int) -> None:
        self._pending_selection = (product_id, offer_id)
        self.refresh()

    def refresh(self) -> None:
        run_in_background(
            self._api.list_products,
            on_success=self._populate_products,
            on_error=lambda err: self.stats_strip.setText(f"Błąd pobierania produktów: {err}"),
        )

    def _populate_products(self, products: list[dict[str, Any]]) -> None:
        self._products = products
        prev_pid = (
            self._pending_selection[0]
            if self._pending_selection
            else self.product_combo.currentData()
        )
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        for prod in products:
            self.product_combo.addItem(str(prod["name"]), int(prod["id"]))
        if prev_pid is not None:
            idx = self.product_combo.findData(int(prev_pid))
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.product_combo.blockSignals(False)
        self._on_product_changed()

    def _on_product_changed(self) -> None:
        pid = self.product_combo.currentData()
        self.offer_combo.blockSignals(True)
        self.offer_combo.clear()
        if pid is not None:
            product = next((p for p in self._products if int(p["id"]) == int(pid)), None)
            if product is not None:
                for o in product.get("offers") or []:
                    lbl = str(o["store_name"])
                    if o.get("variant_label"):
                        lbl = f"{lbl} ({o['variant_label']})"
                    self.offer_combo.addItem(lbl, int(o["id"]))
        if self._pending_selection is not None:
            target_oid = self._pending_selection[1]
            idx = self.offer_combo.findData(target_oid)
            if idx >= 0:
                self.offer_combo.setCurrentIndex(idx)
            self._pending_selection = None
        self.offer_combo.blockSignals(False)
        self._load_chart_data()

    def _on_compare_toggled(self) -> None:
        self.offer_combo.setEnabled(not self.compare_check.isChecked())
        self._load_chart_data()

    def _set_range(self, time_range: str) -> None:
        self._current_range = time_range
        self._sync_range_buttons()
        self._load_chart_data()

    def _sync_range_buttons(self) -> None:
        for key, btn in self._range_buttons.items():
            btn.setProperty("active", key == self._current_range)
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)

    def _load_chart_data(self) -> None:
        pid = self.product_combo.currentData()
        oid = self.offer_combo.currentData()
        selected_range = self._current_range
        self._request_seq += 1
        req_seq = self._request_seq

        if pid is None:
            self.chart.set_series([], time_range=selected_range)
            self.points_table.setRowCount(0)
            self.stats_strip.setText("Brak produktów w bazie.")
            return

        if self.compare_check.isChecked():
            target_pid = int(pid)

            def _on_comp(data: dict[str, Any]) -> None:
                if req_seq == self._request_seq:
                    self._render_comparison(data, selected_range)

            run_in_background(
                lambda: self._api.get_product_comparison(target_pid, selected_range),
                on_success=_on_comp,
                on_error=lambda err: self.stats_strip.setText(f"Błąd: {err}"),
            )
        elif oid is not None:
            target_oid = int(oid)

            def _on_single(data: dict[str, Any]) -> None:
                if req_seq == self._request_seq:
                    self._render_single(data, selected_range)

            run_in_background(
                lambda: self._api.get_offer_history(target_oid, selected_range),
                on_success=_on_single,
                on_error=lambda err: self.stats_strip.setText(f"Błąd: {err}"),
            )

    def _render_single(self, data: dict[str, Any], time_range: str) -> None:
        self.chart.set_series([data], time_range=time_range)
        s = data["stats"]
        curr = s.get("currency") or "PLN"
        range_label = dict((k, lbl) for lbl, k in TIME_RANGES).get(time_range, time_range)

        last_chg = "—"
        if s.get("last_change_amount") is not None and s.get("last_change_percent") is not None:
            amt = Decimal(str(s["last_change_amount"]))
            pct = Decimal(str(s["last_change_percent"]))
            sign = "+" if amt > 0 else ""
            last_chg = f"{sign}{amt:.2f} {curr} ({sign}{pct:.2f}%)"

        self.stats_strip.setText(
            f"Zakres: {range_label}   ·   "
            f"Pomiary: {s['points_count']}   ·   "
            f"Aktualna: {format_money(s.get('current_price'), curr)}   ·   "
            f"Najniższa: {format_money(s.get('min_price'), curr)}   ·   "
            f"Najwyższa: {format_money(s.get('max_price'), curr)}   ·   "
            f"Średnia: {format_money(s.get('avg_price'), curr)}   ·   "
            f"Ostatnia zmiana: {last_chg}"
        )
        self._fill_points_table([data])

    def _render_comparison(self, data: dict[str, Any], time_range: str) -> None:
        offers = data.get("offers") or []
        self.chart.set_series(offers, time_range=time_range)
        range_label = dict((k, lbl) for lbl, k in TIME_RANGES).get(time_range, time_range)
        best_id = data.get("lowest_current_offer_id")
        best_label = "brak"
        for o in offers:
            if o["offer_id"] == best_id:
                curr = o["stats"].get("currency") or "PLN"
                best_label = (
                    f"{o['store_name']} ({format_money(o['stats'].get('current_price'), curr)})"
                )
        total_pts = sum(int(o["stats"]["points_count"]) for o in offers)
        self.stats_strip.setText(
            f"Zakres: {range_label}   ·   "
            f"Porównywane oferty: {len(offers)}   ·   "
            f"Łącznie pomiarów w zakresie: {total_pts}   ·   "
            f"Najniższa dostępna cena: {best_label}"
        )
        self._fill_points_table(offers)

    def _fill_points_table(self, offers: list[dict[str, Any]]) -> None:
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for offer in offers:
            store_lbl = str(offer["store_name"])
            if offer.get("variant_label"):
                store_lbl = f"{store_lbl} ({offer['variant_label']})"
            for pt in offer.get("points") or []:
                rows.append((str(pt["recorded_at"]), store_lbl, pt))

        rows.sort(key=lambda item: item[0], reverse=True)
        self.points_table.setRowCount(len(rows))
        for idx, (_ts, store_lbl, pt) in enumerate(rows):
            curr = str(pt.get("currency") or "PLN")
            avail_key = str(pt.get("availability_status") or "unknown")
            self.points_table.setItem(idx, 0, make_item(format_iso_local(pt.get("recorded_at"))))
            self.points_table.setItem(idx, 1, make_item(store_lbl))
            self.points_table.setItem(
                idx, 2, make_item(format_money(pt.get("price"), curr), align_right=True)
            )
            self.points_table.setItem(
                idx, 3, make_item(format_money(pt.get("regular_price"), curr), align_right=True)
            )
            self.points_table.setItem(
                idx, 4, make_item(AVAILABILITY_LABELS.get(avail_key, avail_key))
            )
            self.points_table.setItem(idx, 5, make_item(str(pt.get("source_layer") or "")))
