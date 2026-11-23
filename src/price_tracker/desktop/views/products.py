from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import (
    AVAILABILITY_LABELS,
    AddOfferDialog,
    AddProductDialog,
    EditOfferDialog,
    configure_table,
    format_iso_local,
    format_money,
    make_item,
)


class ProductsView(QWidget):
    """Tabela wszystkich monitorowanych produktów i ofert z filtrowaniem oraz akcjami."""

    def __init__(
        self,
        api: ApiClient,
        palette: ThemePalette,
        *,
        on_open_history: Callable[[int, int], None],
        on_status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._palette = palette
        self._on_open_history = on_open_history
        self._on_status = on_status
        self._categories: list[dict[str, Any]] = []
        self._rows_meta: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # Pasek tytułowy i akcje główne
        header = QHBoxLayout()
        title = QLabel("Produkty i monitorowane oferty")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        add_cat_btn = QPushButton("Nowa kategoria")
        add_cat_btn.clicked.connect(self._create_category)
        header.addWidget(add_cat_btn)

        add_btn = QPushButton("Dodaj produkt")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(lambda: self.open_add_dialog(""))
        header.addWidget(add_btn)
        root.addLayout(header)

        # Pasek wyszukiwania i filtrów
        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Szukaj po nazwie produktu...")
        self.search_edit.returnPressed.connect(self.refresh)
        filters.addWidget(self.search_edit, stretch=2)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("Wszystkie kategorie", None)
        self.cat_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.cat_combo)

        self.avail_combo = QComboBox()
        self.avail_combo.addItem("Każda dostępność", None)
        for key, lbl in AVAILABILITY_LABELS.items():
            self.avail_combo.addItem(lbl, key)
        self.avail_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.avail_combo)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Filtr tagu...")
        self.tag_edit.setMaximumWidth(150)
        self.tag_edit.returnPressed.connect(self.refresh)
        filters.addWidget(self.tag_edit)

        self.errors_only_check = QCheckBox("Tylko z błędami")
        self.errors_only_check.stateChanged.connect(self.refresh)
        filters.addWidget(self.errors_only_check)

        refresh_btn = QPushButton("Filtruj")
        refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(refresh_btn)
        root.addLayout(filters)

        # Tabela produktów/ofert
        self.table = QTableWidget()
        configure_table(
            self.table,
            [
                "Produkt",
                "Kategoria / Tagi",
                "Sklep",
                "Wariant",
                "Cena",
                "Cena reg.",
                "Dostępność",
                "Status",
                "Ostatnie sprawdzenie",
            ],
        )
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 110)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 110)
        self.table.cellDoubleClicked.connect(self._open_selected_history)
        root.addWidget(self.table, stretch=1)

        # Dolny pasek akcji dla zaznaczonej oferty
        actions = QHBoxLayout()
        self.btn_check_now = QPushButton("Sprawdź teraz")
        self.btn_check_now.clicked.connect(self._check_selected_now)
        actions.addWidget(self.btn_check_now)

        self.btn_history = QPushButton("Historia i wykres")
        self.btn_history.clicked.connect(self._open_selected_history)
        actions.addWidget(self.btn_history)

        self.btn_add_offer = QPushButton("Dodaj sklep / wariant")
        self.btn_add_offer.clicked.connect(self._add_offer_to_selected)
        actions.addWidget(self.btn_add_offer)

        self.btn_edit = QPushButton("Edytuj ofertę")
        self.btn_edit.clicked.connect(self._edit_selected_offer)
        actions.addWidget(self.btn_edit)

        self.btn_toggle = QPushButton("Wstrzymaj / Wznów")
        self.btn_toggle.clicked.connect(self._toggle_selected_active)
        actions.addWidget(self.btn_toggle)

        actions.addStretch(1)

        self.btn_delete_offer = QPushButton("Usuń ofertę")
        self.btn_delete_offer.setObjectName("DangerButton")
        self.btn_delete_offer.clicked.connect(self._delete_selected_offer)
        actions.addWidget(self.btn_delete_offer)

        self.btn_delete_product = QPushButton("Usuń produkt")
        self.btn_delete_product.setObjectName("DangerButton")
        self.btn_delete_product.clicked.connect(self._delete_selected_product)
        actions.addWidget(self.btn_delete_product)
        root.addLayout(actions)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def open_add_dialog(self, initial_url: str = "") -> None:
        dlg = AddProductDialog(
            self._api, self._categories, initial_url=initial_url, parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_status("Dodano produkt do monitorowania.")
            self.refresh()

    def _selected_pair(self) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows_meta):
            return self._rows_meta[row]
        return None

    def refresh(self) -> None:
        search = self.search_edit.text().strip() or None
        cat_id = self.cat_combo.currentData()
        avail = self.avail_combo.currentData()
        tag = self.tag_edit.text().strip() or None
        only_errors = self.errors_only_check.isChecked()

        def _work() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            cats = self._api.list_categories()
            prods = self._api.list_products(
                search=search,
                category_id=int(cat_id) if cat_id is not None else None,
                tag=tag,
                availability=str(avail) if avail is not None else None,
                only_errors=only_errors,
            )
            return cats, prods

        run_in_background(
            _work,
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd ładowania produktów: {err}"),
        )

    def _render(self, payload: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        categories, products = payload
        self._categories = categories

        current_cat = self.cat_combo.currentData()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItem("Wszystkie kategorie", None)
        for cat in categories:
            self.cat_combo.addItem(str(cat["name"]), int(cat["id"]))
        idx = self.cat_combo.findData(current_cat)
        self.cat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_combo.blockSignals(False)

        self._rows_meta.clear()
        for prod in products:
            offers = prod.get("offers") or []
            if not offers:
                self._rows_meta.append((prod, None))
            else:
                for offer in offers:
                    self._rows_meta.append((prod, offer))

        self.table.setRowCount(len(self._rows_meta))
        for row, (prod, offer) in enumerate(self._rows_meta):
            cat_name = prod["category"]["name"] if prod.get("category") else ""
            tags_str = ", ".join(prod.get("tags") or [])
            cat_tag_txt = " · ".join(x for x in (cat_name, tags_str) if x) or "—"

            self.table.setItem(row, 0, make_item(str(prod["name"])))
            self.table.setItem(row, 1, make_item(cat_tag_txt))

            if offer is None:
                self.table.setItem(row, 2, make_item("—"))
                self.table.setItem(row, 3, make_item("—"))
                self.table.setItem(row, 4, make_item("—", align_right=True))
                self.table.setItem(row, 5, make_item("—", align_right=True))
                self.table.setItem(row, 6, make_item("—"))
                self.table.setItem(
                    row, 7, make_item("Brak ofert", color=self._palette.warning)
                )
                self.table.setItem(row, 8, make_item("—"))
                continue

            curr = offer.get("currency")
            avail_key = str(offer.get("availability_status") or "unknown")
            avail_txt = AVAILABILITY_LABELS.get(avail_key, avail_key)
            avail_color = (
                self._palette.positive
                if avail_key == "in_stock"
                else self._palette.negative
                if avail_key == "out_of_stock"
                else self._palette.text_secondary
            )

            failures = int(offer.get("consecutive_failures") or 0)
            if not offer.get("is_active"):
                status_txt = "Wstrzymane"
                status_color = self._palette.text_muted
            elif failures > 0:
                status_txt = f"Błąd ({offer.get('last_error_code')})"
                status_color = self._palette.negative
            else:
                status_txt = "Aktywne"
                status_color = self._palette.positive

            self.table.setItem(row, 0, make_item(str(prod["name"])))
            self.table.setItem(row, 1, make_item(cat_tag_txt))
            self.table.setItem(row, 2, make_item(str(offer["store_name"])))
            self.table.setItem(row, 3, make_item(str(offer.get("variant_label") or "—")))
            self.table.setItem(
                row,
                4,
                make_item(format_money(offer.get("current_price"), curr), align_right=True),
            )
            self.table.setItem(
                row,
                5,
                make_item(format_money(offer.get("regular_price"), curr), align_right=True),
            )
            self.table.setItem(row, 6, make_item(avail_txt, color=avail_color))
            self.table.setItem(row, 7, make_item(status_txt, color=status_color))
            self.table.setItem(
                row, 8, make_item(format_iso_local(offer.get("last_checked_at")))
            )

    def _create_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Nowa kategoria", "Nazwa kategorii:")
        if not ok or not name.strip():
            return
        run_in_background(
            lambda: self._api.create_category(name.strip()),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )

    def _open_selected_history(self, *_args: Any) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        prod, offer = pair
        if prod is None or offer is None:
            return
        self._on_open_history(int(prod["id"]), int(offer["id"]))

    def _check_selected_now(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        _, offer = pair
        if offer is None:
            return
        offer_id = int(offer["id"])
        run_in_background(
            lambda: self._api.check_offer_now(offer_id),
            on_success=lambda res: self._on_status(
                f"Dodano priorytetowe zadanie #{res['job_id']} dla oferty #{offer_id}."
            ),
            on_error=lambda err: QMessageBox.warning(self, "Błąd kolejkowania", err),
        )

    def _add_offer_to_selected(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        prod, _ = pair
        if prod is None:
            return
        dlg = AddOfferDialog(self._api, int(prod["id"]), str(prod["name"]), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_status("Dodano nową ofertę do produktu.")
            self.refresh()

    def _edit_selected_offer(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        prod, offer = pair
        if offer is None:
            return
        dlg = EditOfferDialog(self._api, offer, str(prod["name"]), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_status("Zapisano zmiany w ofercie.")
            self.refresh()

    def _toggle_selected_active(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        _, offer = pair
        if offer is None:
            return
        new_state = not bool(offer.get("is_active", True))
        offer_id = int(offer["id"])
        run_in_background(
            lambda: self._api.update_offer(offer_id, {"is_active": new_state}),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )

    def _delete_selected_offer(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        prod, offer = pair
        if offer is None:
            return
        offers_count = len(prod.get("offers") or [])
        extra = (
            " Ponieważ to ostatnia oferta tego produktu, usunięty zostanie również sam produkt."
            if offers_count <= 1
            else ""
        )
        ans = QMessageBox.question(
            self,
            "Usuń ofertę",
            f"Usunąć ofertę ze sklepu '{offer['store_name']}' dla '{prod['name']}' "
            f"wraz z jej historią cen?{extra}",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        offer_id = int(offer["id"])
        run_in_background(
            lambda: self._api.delete_offer(offer_id),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )

    def _delete_selected_product(self) -> None:
        pair = self._selected_pair()
        if pair is None:
            return
        prod, _ = pair
        if prod is None:
            return
        ans = QMessageBox.question(
            self,
            "Usuń produkt",
            f"Usunąć produkt '{prod['name']}' wraz ze wszystkimi jego ofertami i historią cen?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        product_id = int(prod["id"])
        run_in_background(
            lambda: self._api.delete_product(product_id),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )
