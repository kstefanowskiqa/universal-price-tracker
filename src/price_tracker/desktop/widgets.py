from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import CHART_SERIES_COLORS, ThemePalette, get_palette

INTERVAL_OPTIONS: list[tuple[str, int | None]] = [
    ("Domyślny (globalny)", None),
    ("5 minut", 300),
    ("15 minut", 900),
    ("30 minut", 1800),
    ("1 godzina", 3600),
    ("6 godzin", 21600),
    ("12 godzin", 43200),
    ("24 godziny", 86400),
]

AVAILABILITY_LABELS: dict[str, str] = {
    "in_stock": "Dostępny",
    "out_of_stock": "Wyprzedany",
    "preorder": "Przedsprzedaż",
    "limited": "Ostatnie sztuki",
    "unknown": "Nieznany",
}

ALERT_TYPE_OPTIONS: list[tuple[str, str]] = [
    ("Osiągnięcie ceny docelowej (≤ próg)", "target_price_below"),
    ("Spadek ceny", "price_drop"),
    ("Wzrost ceny", "price_rise"),
    ("Spadek ceny o procent (%)", "percent_drop"),
    ("Powrót do dostępności", "back_in_stock"),
    ("Wyprzedanie produktu", "out_of_stock"),
    ("Błąd monitorowania", "monitoring_error"),
]


def format_iso_local(raw_iso: str | None) -> str:
    if not raw_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(raw_iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw_iso


def format_money(raw_amount: Any, currency: str | None = None) -> str:
    if raw_amount is None or raw_amount == "":
        return "—"
    try:
        val = Decimal(str(raw_amount)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return str(raw_amount)
    curr = currency or "PLN"
    return f"{val:,.2f} {curr}".replace(",", " ")


def configure_table(table: QTableWidget, headers: list[str]) -> None:
    """Stosuje spójny, natywny układ tabeli (bez ręcznej edycji komórek, zaznaczanie wiersza)."""
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSortingEnabled(False)
    table.setShowGrid(True)
    v_header = table.verticalHeader()
    if v_header is not None:
        v_header.setVisible(False)
        v_header.setDefaultSectionSize(28)
    h_header = table.horizontalHeader()
    if h_header is not None:
        h_header.setStretchLastSection(True)
        h_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)


def make_item(
    text: str,
    *,
    align_right: bool = False,
    color: str | None = None,
    user_data: Any = None,
) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    else:
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    if user_data is not None:
        item.setData(Qt.ItemDataRole.UserRole, user_data)
    return item

RANGE_WINDOW_SECONDS: dict[str, int] = {
    "24h": 24 * 3600,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
    "180d": 180 * 86400,
}


def compute_chart_bounds(
    all_x: list[float],
    all_y: list[float],
    time_range: str,
    *,
    now_ts: float | None = None,
) -> tuple[tuple[float, float], tuple[float, float] | None]:
    """Wyznacza jawny zakres osi X (wg wybranego okresu) i osi Y (z czytelnym marginesem)."""
    current_ts = now_ts if now_ts is not None else datetime.now(UTC).timestamp()
    span = RANGE_WINDOW_SECONDS.get(time_range)

    if span is not None:
        x_start = current_ts - float(span)
        x_end = current_ts
        if all_x:
            x_start = min(x_start, min(all_x))
            x_end = max(x_end, max(all_x))
        pad_x = max((x_end - x_start) * 0.02, 60.0)
        x_bounds = (x_start - pad_x, x_end + pad_x)
    elif all_x:
        min_x = min(all_x)
        max_x = max(all_x)
        if max_x - min_x < 60.0:
            x_bounds = (min_x - 3600.0, max_x + 3600.0)
        else:
            pad_x = (max_x - min_x) * 0.05
            x_bounds = (min_x - pad_x, max_x + pad_x)
    else:
        x_bounds = (current_ts - 86400.0, current_ts)

    if not all_y:
        return x_bounds, None

    min_y = min(all_y)
    max_y = max(all_y)
    margin_y = (
        max(abs(min_y) * 0.05, 5.0)
        if max_y - min_y < 0.01
        else (max_y - min_y) * 0.12
    )
    y_bounds = (max(0.0, min_y - margin_y), max_y + margin_y)
    return x_bounds, y_bounds


class PriceHistoryChart(QWidget):
    """Interaktywny wykres cen PyQtGraph z osią czasu, trybem schodkowym i kursorem."""

    def __init__(self, palette: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._series_points: list[tuple[str, list[float], list[float], str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._hover_label = QLabel("Najedź kursorem na wykres, aby odczytać dokładny pomiar.")
        self._hover_label.setObjectName("MutedText")
        layout.addWidget(self._hover_label)

        axis = pg.DateAxisItem(orientation="bottom")
        self._plot: Any = pg.PlotWidget(axisItems={"bottom": axis})
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setMenuEnabled(False)
        self._plot.setMouseEnabled(x=True, y=True)
        self._legend: Any = self._plot.addLegend(offset=(10, 10))
        layout.addWidget(self._plot, stretch=1)

        self._vline: Any = pg.InfiniteLine(angle=90, movable=False)
        self._hline: Any = pg.InfiniteLine(angle=0, movable=False)
        self._plot.addItem(self._vline, ignoreBounds=True)
        self._plot.addItem(self._hline, ignoreBounds=True)
        self._vline.hide()
        self._hline.hide()

        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.apply_palette(palette)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self._plot.setBackground(palette.bg_surface)
        axis_pen = pg.mkPen(color=palette.border_strong, width=1)
        text_color = palette.text_secondary
        for side in ("bottom", "left"):
            ax = self._plot.getAxis(side)
            ax.setPen(axis_pen)
            ax.setTextPen(text_color)
        cross_pen = pg.mkPen(color=palette.text_muted, style=Qt.PenStyle.DashLine, width=1)
        self._vline.setPen(cross_pen)
        self._hline.setPen(cross_pen)

    def set_series(
        self,
        offers_history: list[dict[str, Any]],
        *,
        time_range: str = "30d",
    ) -> None:
        self._plot.clear()
        self._plot.addItem(self._vline, ignoreBounds=True)
        self._plot.addItem(self._hline, ignoreBounds=True)
        self._vline.hide()
        self._hline.hide()
        if self._legend is not None:
            self._legend.clear()

        self._series_points.clear()
        all_x: list[float] = []
        all_y: list[float] = []

        for idx, offer_data in enumerate(offers_history):
            points = offer_data.get("points") or []
            if not points:
                continue

            xs: list[float] = []
            ys: list[float] = []
            currency = str(offer_data.get("stats", {}).get("currency") or "PLN")
            for pt in points:
                raw_ts = str(pt["recorded_at"]).replace("Z", "+00:00")
                ts = datetime.fromisoformat(raw_ts).timestamp()
                xs.append(ts)
                ys.append(float(Decimal(str(pt["price"]))))

            if not xs:
                continue
            all_x.extend(xs)
            all_y.extend(ys)
            color = CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]
            store_name = str(offer_data.get("store_name") or "Sklep")
            variant = str(offer_data.get("variant_label") or "")
            label = f"{store_name} ({variant})" if variant else store_name
            self._series_points.append((label, xs, ys, currency))

            pen = pg.mkPen(color=color, width=2)
            # Wykres schodkowy dla >=2 punktów (cena obowiązuje do kolejnej zmiany)
            if len(xs) >= 2:
                step_x: list[float] = []
                step_y: list[float] = []
                for i in range(len(xs) - 1):
                    step_x.extend([xs[i], xs[i + 1]])
                    step_y.extend([ys[i], ys[i]])
                step_x.append(xs[-1])
                step_y.append(ys[-1])
                self._plot.plot(step_x, step_y, pen=pen, name=label)
                self._plot.plot(
                    xs,
                    ys,
                    pen=None,
                    symbol="o",
                    symbolSize=6,
                    symbolBrush=color,
                    symbolPen=color,
                )
            else:
                self._plot.plot(
                    xs,
                    ys,
                    pen=pen,
                    symbol="o",
                    symbolSize=8,
                    symbolBrush=color,
                    symbolPen=color,
                    name=label,
                )

        x_bounds, y_bounds = compute_chart_bounds(all_x, all_y, time_range)
        self._plot.enableAutoRange(x=False, y=False)
        self._plot.setXRange(x_bounds[0], x_bounds[1], padding=0.0)
        if y_bounds is not None:
            self._plot.setYRange(y_bounds[0], y_bounds[1], padding=0.0)

        if not all_x:
            self._hover_label.setText(
                "Brak rzeczywistych pomiarów w wybranym zakresie czasowym."
            )
        else:
            self._hover_label.setText(
                "Najedź kursorem na wykres, aby odczytać dokładny pomiar (scroll = zoom)."
            )

    def _on_mouse_moved(self, pos: Any) -> None:
        if not self._series_points:
            return
        vb = self._plot.plotItem.vb
        if not self._plot.sceneBoundingRect().contains(pos):
            self._vline.hide()
            self._hline.hide()
            return

        mouse_point = vb.mapSceneToView(pos)
        x_val = float(mouse_point.x())
        y_val = float(mouse_point.y())
        self._vline.setPos(x_val)
        self._hline.setPos(y_val)
        self._vline.show()
        self._hline.show()

        parts: list[str] = []
        for label, xs, ys, currency in self._series_points:
            closest_i = min(range(len(xs)), key=lambda i: abs(xs[i] - x_val))
            dt_txt = datetime.fromtimestamp(xs[closest_i]).strftime("%Y-%m-%d %H:%M")
            parts.append(f"{label}: {ys[closest_i]:.2f} {currency} ({dt_txt})")
        self._hover_label.setText("  |  ".join(parts))


class AddProductDialog(QDialog):
    """Okno dodawania produktu po URL z podglądem na żywo i możliwością ręcznej korekty."""

    def __init__(
        self,
        api: ApiClient,
        categories: list[dict[str, Any]],
        *,
        initial_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self.created_product: dict[str, Any] | None = None
        self.setWindowTitle("Dodaj produkt do monitorowania")
        self.setMinimumWidth(540)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Wiersz URL + przycisk rozpoznawania
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.returnPressed.connect(self._on_preview)
        self.url_edit.setPlaceholderText("https://www.x-kom.pl/p/...")
        self.preview_btn = QPushButton("Rozpoznaj z URL")
        self.preview_btn.clicked.connect(self._on_preview)
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.preview_btn)
        root.addLayout(url_row)

        self.status_label = QLabel(
            "Wklej adres produktu i kliknij 'Rozpoznaj z URL' lub wpisz dane ręcznie."
        )
        self.status_label.setObjectName("MutedText")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        form = QFormLayout()
        form.setSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Wypełni się automatycznie lub wpisz własną nazwę")
        form.addRow("Nazwa produktu:", self.name_edit)

        self.variant_edit = QLineEdit()
        self.variant_edit.setPlaceholderText("np. 256 GB / Czarny (opcjonalnie)")
        form.addRow("Wariant:", self.variant_edit)

        self.category_combo = QComboBox()
        self.category_combo.addItem("Bez kategorii", None)
        for cat in categories:
            self.category_combo.addItem(str(cat["name"]), int(cat["id"]))
        form.addRow("Kategoria:", self.category_combo)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText(
            "np. elektronika, prezent, promo (oddzielone przecinkiem)"
        )
        form.addRow("Tagi:", self.tags_edit)

        price_row = QHBoxLayout()
        self.price_edit = QLineEdit()
        self.price_edit.setPlaceholderText("opcjonalna korekta ceny, np. 2499.00")
        self.currency_edit = QLineEdit()
        self.currency_edit.setPlaceholderText("PLN")
        self.currency_edit.setMaximumWidth(75)
        price_row.addWidget(self.price_edit, stretch=1)
        price_row.addWidget(self.currency_edit)
        form.addRow("Cena / waluta:", price_row)

        self.interval_combo = QComboBox()
        for label, seconds in INTERVAL_OPTIONS:
            self.interval_combo.addItem(label, seconds)
        form.addRow("Częstotliwość:", self.interval_combo)

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Własna notatka (opcjonalnie)")
        form.addRow("Notatki:", self.notes_edit)

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Dodaj produkt")
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setAutoDefault(False)
            ok_btn.setDefault(False)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setAutoDefault(False)
            cancel_btn.setDefault(False)
        self._ok_btn = ok_btn
        buttons.accepted.connect(self._on_submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._previewed_image: str | None = None
        self._previewed_regular: str | None = None
        self._previewed_availability: str | None = None
        self._preview_ok = False

    def _on_preview(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self.status_label.setText("Wklej najpierw adres URL produktu.")
            return
        self.preview_btn.setEnabled(False)
        self.status_label.setText("Pobieranie i analiza strony sklepu...")

        variant = self.variant_edit.text().strip()

        def _work() -> dict[str, Any]:
            return self._api.preview_url(url, variant_label=variant)

        def _ok(data: dict[str, Any]) -> None:
            self.preview_btn.setEnabled(True)
            if data.get("ok"):
                self._preview_ok = True
                if data.get("title") and not self.name_edit.text().strip():
                    self.name_edit.setText(str(data["title"]))
                if data.get("current_price") is not None:
                    self.price_edit.setText(str(data["current_price"]))
                if data.get("currency"):
                    self.currency_edit.setText(str(data["currency"]))
                self._previewed_image = data.get("image_url")
                self._previewed_regular = (
                    str(data["regular_price"]) if data.get("regular_price") is not None else None
                )
                self._previewed_availability = data.get("availability")
                layer = data.get("extraction_layer") or "unknown"
                store = data.get("store_name") or "sklep"
                self.status_label.setText(
                f"Rozpoznano dane ({store}, warstwa: {layer}). "
                "Możesz poprawić pola przed zapisem."
                )
            else:
                self._preview_ok = False
                code = data.get("error_code") or "error"
                detail = data.get("error_detail") or "Nie udało się pobrać danych automatycznie."
                self.status_label.setText(f"[{code}] {detail}")

        def _err(msg: str) -> None:
            self.preview_btn.setEnabled(True)
            self.status_label.setText(f"Błąd połączenia: {msg}")

        run_in_background(_work, on_success=_ok, on_error=_err)

    def _on_submit(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Brak URL", "Podaj adres URL produktu.")
            return

        manual_price_text = self.price_edit.text().strip().replace(",", ".")
        if manual_price_text:
            try:
                val = Decimal(manual_price_text)
                if val <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                QMessageBox.warning(self, "Błędna cena", "Podaj poprawną cenę większą od zera.")
                return

        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        payload: dict[str, Any] = {
            "url": url,
            "name": self.name_edit.text().strip() or None,
            "category_id": self.category_combo.currentData(),
            "tags": tags,
            "notes": self.notes_edit.text().strip() or None,
            "image_url": self._previewed_image,
            "variant_label": self.variant_edit.text().strip(),
            "check_interval_seconds": self.interval_combo.currentData(),
            "manual_price": manual_price_text or None,
            "manual_regular_price": self._previewed_regular,
            "manual_currency": self.currency_edit.text().strip() or None,
            "manual_availability": self._previewed_availability,
            "auto_fetch": not self._preview_ok and not bool(manual_price_text),
        }

        if self._ok_btn is not None:
            self._ok_btn.setEnabled(False)
        self.status_label.setText("Zapisywanie produktu...")

        def _work() -> dict[str, Any]:
            return self._api.create_product(payload)

        def _ok(prod: dict[str, Any]) -> None:
            self.created_product = prod
            self.accept()

        def _err(msg: str) -> None:
            if self._ok_btn is not None:
                self._ok_btn.setEnabled(True)
            self.status_label.setText(f"Błąd zapisu: {msg}")
            QMessageBox.critical(self, "Błąd zapisu", msg)

        run_in_background(_work, on_success=_ok, on_error=_err)


class AddOfferDialog(QDialog):
    """Dodaje kolejną ofertę (inny sklep lub wariant) do istniejącego produktu."""

    def __init__(
        self,
        api: ApiClient,
        product_id: int,
        product_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._product_id = product_id
        self.setWindowTitle(f"Dodaj sklep / wariant – {product_name}")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        form.addRow("Adres URL oferty:", self.url_edit)

        self.variant_edit = QLineEdit()
        self.variant_edit.setPlaceholderText("np. 100ml / Czarny (opcjonalnie)")
        form.addRow("Wariant:", self.variant_edit)

        self.interval_combo = QComboBox()
        for label, seconds in INTERVAL_OPTIONS:
            self.interval_combo.addItem(label, seconds)
        form.addRow("Częstotliwość:", self.interval_combo)

        self.price_edit = QLineEdit()
        self.price_edit.setPlaceholderText("opcjonalna cena ręczna")
        form.addRow("Cena ręczna:", self.price_edit)

        self.currency_edit = QLineEdit()
        self.currency_edit.setPlaceholderText("np. PLN, EUR, USD")
        form.addRow("Waluta:", self.currency_edit)

        root.addLayout(form)
        self.status_label = QLabel("")
        self.status_label.setObjectName("MutedText")
        root.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setText("Dodaj ofertę")
            ok_btn.setAutoDefault(False)
            ok_btn.setDefault(False)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setAutoDefault(False)
            cancel_btn.setDefault(False)
        self._ok_btn = ok_btn
        buttons.accepted.connect(self._on_submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_submit(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Brak URL", "Podaj adres URL oferty.")
            return
        price_txt = self.price_edit.text().strip().replace(",", ".")
        payload: dict[str, Any] = {
            "url": url,
            "variant_label": self.variant_edit.text().strip(),
            "check_interval_seconds": self.interval_combo.currentData(),
            "manual_price": price_txt or None,
            "manual_currency": self.currency_edit.text().strip() or None,
            "auto_fetch": not bool(price_txt),
        }
        if self._ok_btn is not None:
            self._ok_btn.setEnabled(False)
        self.status_label.setText("Dodawanie oferty...")

        def _ok(_res: Any) -> None:
            self.accept()

        def _err(msg: str) -> None:
            if self._ok_btn is not None:
                self._ok_btn.setEnabled(True)
            self.status_label.setText(msg)
            QMessageBox.critical(self, "Błąd", msg)

        run_in_background(
            lambda: self._api.add_offer(self._product_id, payload),
            on_success=_ok,
            on_error=_err,
        )


class EditOfferDialog(QDialog):
    """Pozwala ręcznie poprawić cenę, walutę, dostępność, interwał i status oferty."""

    def __init__(
        self,
        api: ApiClient,
        offer: dict[str, Any],
        product_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._offer_id = int(offer["id"])
        self.setWindowTitle(f"Edycja oferty – {product_name}")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.variant_edit = QLineEdit(str(offer.get("variant_label") or ""))
        form.addRow("Wariant:", self.variant_edit)

        self.active_check = QCheckBox("Monitorowanie aktywne")
        self.active_check.setChecked(bool(offer.get("is_active", True)))
        form.addRow("Status:", self.active_check)

        self.interval_combo = QComboBox()
        current_interval = offer.get("check_interval_seconds")
        selected_idx = 0
        for idx, (label, seconds) in enumerate(INTERVAL_OPTIONS):
            self.interval_combo.addItem(label, seconds)
            if seconds == current_interval:
                selected_idx = idx
        self.interval_combo.setCurrentIndex(selected_idx)
        form.addRow("Częstotliwość:", self.interval_combo)

        self.price_edit = QLineEdit()
        self.price_edit.setPlaceholderText(
            f"Aktualna: {offer.get('current_price') or 'brak'} (zostaw puste, by nie zmieniać)"
        )
        form.addRow("Ręczna korekta ceny:", self.price_edit)

        self.currency_edit = QLineEdit(str(offer.get("currency") or "PLN"))
        form.addRow("Waluta:", self.currency_edit)

        self.avail_combo = QComboBox()
        cur_avail = str(offer.get("availability_status") or "unknown")
        for key, label in AVAILABILITY_LABELS.items():
            self.avail_combo.addItem(label, key)
        idx_av = self.avail_combo.findData(cur_avail)
        if idx_av >= 0:
            self.avail_combo.setCurrentIndex(idx_av)
        form.addRow("Dostępność:", self.avail_combo)

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setObjectName("PrimaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_save(self) -> None:
        interval_val = self.interval_combo.currentData()
        price_txt = self.price_edit.text().strip().replace(",", ".")
        payload: dict[str, Any] = {
            "variant_label": self.variant_edit.text().strip(),
            "is_active": self.active_check.isChecked(),
            "check_interval_seconds": interval_val,
            "use_default_interval": interval_val is None,
            "manual_currency": self.currency_edit.text().strip() or None,
            "manual_availability": self.avail_combo.currentData(),
        }
        if price_txt:
            payload["manual_price"] = price_txt

        run_in_background(
            lambda: self._api.update_offer(self._offer_id, payload),
            on_success=lambda _: self.accept(),
            on_error=lambda msg: QMessageBox.critical(self, "Błąd zapisu", msg),
        )


class AddAlertDialog(QDialog):
    """Tworzenie indywidualnej reguły powiadomień dla wybranej oferty."""

    def __init__(
        self,
        api: ApiClient,
        offers: list[tuple[int, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self.setWindowTitle("Nowa reguła powiadomienia")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.offer_combo = QComboBox()
        for oid, label in offers:
            self.offer_combo.addItem(label, oid)
        form.addRow("Monitorowana oferta:", self.offer_combo)

        self.type_combo = QComboBox()
        for label, key in ALERT_TYPE_OPTIONS:
            self.type_combo.addItem(label, key)
        self.type_combo.currentIndexChanged.connect(self._update_threshold_hint)
        form.addRow("Warunek alertu:", self.type_combo)

        self.threshold_edit = QLineEdit()
        form.addRow("Próg (kwota / % / liczba błędów):", self.threshold_edit)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 10080)
        self.cooldown_spin.setValue(60)
        self.cooldown_spin.setSuffix(" min")
        form.addRow("Cooldown (anty-spam):", self.cooldown_spin)

        root.addLayout(form)
        self._update_threshold_hint()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setText("Utwórz alert")
        buttons.accepted.connect(self._on_submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_threshold_hint(self) -> None:
        key = str(self.type_combo.currentData())
        hints = {
            "target_price_below": "np. 2200.00 (wymagane)",
            "percent_drop": "np. 10 (spadek o min. 10%, wymagane)",
            "price_drop": "opcjonalnie min. kwota spadku, np. 50.00",
            "price_rise": "opcjonalnie min. kwota wzrostu, np. 50.00",
            "monitoring_error": "opcjonalnie po ilu kolejnych błędach, domyślnie 1",
            "back_in_stock": "nie dotyczy",
            "out_of_stock": "nie dotyczy",
        }
        self.threshold_edit.setPlaceholderText(hints.get(key, ""))
        self.threshold_edit.setEnabled(key not in {"back_in_stock", "out_of_stock"})

    def _on_submit(self) -> None:
        offer_id = self.offer_combo.currentData()
        if offer_id is None:
            QMessageBox.warning(self, "Brak ofert", "Dodaj najpierw co najmniej jeden produkt.")
            return
        rule_type = str(self.type_combo.currentData())
        threshold_txt = self.threshold_edit.text().strip().replace(",", ".")
        threshold_val = (
        threshold_txt if (threshold_txt and self.threshold_edit.isEnabled()) else None
        )
        payload: dict[str, Any] = {
            "offer_id": int(offer_id),
            "rule_type": rule_type,
            "threshold_value": threshold_val,
            "cooldown_minutes": self.cooldown_spin.value(),
            "is_enabled": True,
        }
        run_in_background(
            lambda: self._api.create_alert(payload),
            on_success=lambda _: self.accept(),
            on_error=lambda msg: QMessageBox.critical(self, "Błąd reguły", msg),
        )


class EditStoreDialog(QDialog):
    """Edycja konfiguracji sklepu (adapter, limit zapytań, Playwright JS, selektory CSS)."""

    def __init__(
        self,
        api: ApiClient,
        store: dict[str, Any],
        adapters: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._store_id = int(store["id"])
        self.setWindowTitle(f"Konfiguracja sklepu – {store['domain']}")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(str(store["name"]))
        form.addRow("Nazwa sklepu:", self.name_edit)

        self.adapter_combo = QComboBox()
        for a in adapters:
            self.adapter_combo.addItem(
                f"{a['display_name']} ({a['adapter_key']})",
                str(a["adapter_key"]),
            )
        idx = self.adapter_combo.findData(str(store["adapter_key"]))
        if idx >= 0:
            self.adapter_combo.setCurrentIndex(idx)
        form.addRow("Adapter:", self.adapter_combo)

        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(0, 3600)
        self.rate_spin.setValue(int(store["rate_limit_seconds"]))
        self.rate_spin.setSuffix(" s")
        form.addRow("Odstęp między zapytaniami:", self.rate_spin)

        self.js_check = QCheckBox("Wymuszaj renderowanie JavaScript (Playwright)")
        self.js_check.setChecked(bool(store["requires_js"]))
        form.addRow("Tryb JS:", self.js_check)

        self.enabled_check = QCheckBox("Monitorowanie sklepu włączone")
        self.enabled_check.setChecked(bool(store["is_enabled"]))
        form.addRow("Aktywny:", self.enabled_check)

        self.selectors_edit = QPlainTextEdit()
        self.selectors_edit.setMaximumHeight(110)
        self.selectors_edit.setPlaceholderText(
            'Opcjonalny JSON, np. {"price": ".product-price", "regular_price": ".old-price"}'
        )
        if store.get("custom_selectors_json"):
            self.selectors_edit.setPlainText(
                json.dumps(store["custom_selectors_json"], indent=2, ensure_ascii=False)
            )
        form.addRow("Własne selektory CSS (JSON):", self.selectors_edit)

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setObjectName("PrimaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_save(self) -> None:
        raw_sel = self.selectors_edit.toPlainText().strip()
        selectors_payload: dict[str, str] | None = None
        clear_selectors = False
        if raw_sel:
            try:
                parsed = json.loads(raw_sel)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON musi być obiektem (słownikiem).")
                selectors_payload = {str(k): str(v) for k, v in parsed.items()}
            except ValueError as exc:
                QMessageBox.warning(self, "Błędny JSON", str(exc))
                return
        else:
            clear_selectors = True

        payload: dict[str, Any] = {
            "name": self.name_edit.text().strip(),
            "adapter_key": str(self.adapter_combo.currentData()),
            "rate_limit_seconds": self.rate_spin.value(),
            "requires_js": self.js_check.isChecked(),
            "is_enabled": self.enabled_check.isChecked(),
            "custom_selectors_json": selectors_payload,
            "clear_custom_selectors": clear_selectors,
        }
        run_in_background(
            lambda: self._api.update_store(self._store_id, payload),
            on_success=lambda _: self.accept(),
            on_error=lambda msg: QMessageBox.critical(self, "Błąd zapisu", msg),
        )


def default_palette() -> ThemePalette:
    return get_palette("dark")
