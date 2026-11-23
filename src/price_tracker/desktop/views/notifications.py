from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import (
    ALERT_TYPE_OPTIONS,
    AddAlertDialog,
    configure_table,
    format_iso_local,
    make_item,
)

_RULE_LABELS = {key: lbl for lbl, key in ALERT_TYPE_OPTIONS}


class NotificationsView(QWidget):
    """Konfiguracja reguł alertów oraz historia wysłanych powiadomień."""

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
        self._rules: list[dict[str, Any]] = []
        self._offers_choices: list[tuple[int, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Powiadomienia i alerty")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        add_btn = QPushButton("Nowa reguła alertu")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self._add_rule)
        header.addWidget(add_btn)

        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        tabs = QTabWidget()

        # Karta 1: Reguły alertów
        rules_tab = QWidget()
        rules_layout = QVBoxLayout(rules_tab)
        rules_layout.setContentsMargins(10, 10, 10, 10)
        self.rules_table = QTableWidget()
        configure_table(
            self.rules_table,
            [
                "Produkt",
                "Sklep",
                "Warunek",
                "Próg",
                "Cooldown",
                "Aktywny",
                "Ostatnie wywołanie",
            ],
        )
        self.rules_table.setColumnWidth(0, 230)
        self.rules_table.setColumnWidth(1, 130)
        self.rules_table.setColumnWidth(2, 220)
        self.rules_table.setColumnWidth(3, 100)
        self.rules_table.setColumnWidth(4, 90)
        self.rules_table.setColumnWidth(5, 80)
        rules_layout.addWidget(self.rules_table)

        rules_actions = QHBoxLayout()
        toggle_btn = QPushButton("Włącz / Wyłącz")
        toggle_btn.clicked.connect(self._toggle_rule)
        rules_actions.addWidget(toggle_btn)

        reset_btn = QPushButton("Resetuj stan histerezy")
        reset_btn.clicked.connect(self._reset_rule_state)
        rules_actions.addWidget(reset_btn)

        rules_actions.addStretch(1)
        del_btn = QPushButton("Usuń regułę")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self._delete_rule)
        rules_actions.addWidget(del_btn)
        rules_layout.addLayout(rules_actions)
        tabs.addTab(rules_tab, "Reguły alertów")

        # Karta 2: Historia powiadomień
        logs_tab = QWidget()
        logs_layout = QVBoxLayout(logs_tab)
        logs_layout.setContentsMargins(10, 10, 10, 10)
        self.logs_table = QTableWidget()
        configure_table(
            self.logs_table,
            ["Data", "Status", "Podsumowanie", "Błąd dostarczenia"],
        )
        self.logs_table.setColumnWidth(0, 150)
        self.logs_table.setColumnWidth(1, 100)
        self.logs_table.setColumnWidth(2, 420)
        logs_layout.addWidget(self.logs_table)
        tabs.addTab(logs_tab, "Historia wysłanych")

        root.addWidget(tabs, stretch=1)

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def refresh(self) -> None:
        def _work() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
            return (
                self._api.list_alerts(),
                self._api.list_notification_logs(limit=100),
                self._api.list_products(),
            )

        run_in_background(
            _work,
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd pobierania alertów: {err}"),
        )

    def _render(
        self,
        data: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    ) -> None:
        rules, logs, products = data
        self._rules = rules
        self._offers_choices = []
        for prod in products:
            for o in prod.get("offers") or []:
                label = f"{prod['name']} — {o['store_name']}"
                if o.get("variant_label"):
                    label = f"{label} ({o['variant_label']})"
                self._offers_choices.append((int(o["id"]), label))

        self.rules_table.setRowCount(len(rules))
        for row, r in enumerate(rules):
            rt = str(r["rule_type"])
            enabled = bool(r["is_enabled"])
            self.rules_table.setItem(row, 0, make_item(str(r.get("product_name") or "—")))
            self.rules_table.setItem(row, 1, make_item(str(r.get("store_name") or "Wszystkie")))
            self.rules_table.setItem(row, 2, make_item(_RULE_LABELS.get(rt, rt)))
            self.rules_table.setItem(
                row,
                3,
                make_item(
                    str(r["threshold_value"]) if r.get("threshold_value") is not None else "—",
                    align_right=True,
                ),
            )
            self.rules_table.setItem(
                row, 4, make_item(f"{r['cooldown_minutes']} min", align_right=True)
            )
            self.rules_table.setItem(
                row,
                5,
                make_item(
                    "Tak" if enabled else "Nie",
                    color=self._palette.positive if enabled else self._palette.text_muted,
                ),
            )
            self.rules_table.setItem(
                row, 6, make_item(format_iso_local(r.get("last_triggered_at")))
            )

        self.logs_table.setRowCount(len(logs))
        for row, item in enumerate(logs):
            st = str(item["status"])
            color = (
                self._palette.positive
                if st == "sent"
                else self._palette.negative
                if st == "failed"
                else self._palette.warning
            )
            self.logs_table.setItem(row, 0, make_item(format_iso_local(item.get("created_at"))))
            self.logs_table.setItem(row, 1, make_item(st.upper(), color=color))
            self.logs_table.setItem(row, 2, make_item(str(item.get("payload_summary") or "")))
            self.logs_table.setItem(row, 3, make_item(str(item.get("error_message") or "—")))

    def _selected_rule(self) -> dict[str, Any] | None:
        row = self.rules_table.currentRow()
        if 0 <= row < len(self._rules):
            return self._rules[row]
        return None

    def _add_rule(self) -> None:
        dlg = AddAlertDialog(self._api, self._offers_choices, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_status("Dodano nową regułę powiadomienia.")
            self.refresh()

    def _toggle_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        rule_id = int(rule["id"])
        new_enabled = not bool(rule["is_enabled"])
        run_in_background(
            lambda: self._api.update_alert(rule_id, {"is_enabled": new_enabled}),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )

    def _reset_rule_state(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        rule_id = int(rule["id"])

        def _done(_res: Any) -> None:
            self._on_status("Zresetowano stan histerezy reguły.")
            self.refresh()

        run_in_background(
            lambda: self._api.update_alert(rule_id, {"reset_state": True}),
            on_success=_done,
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )

    def _delete_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        rule_id = int(rule["id"])
        run_in_background(
            lambda: self._api.delete_alert(rule_id),
            on_success=lambda _: self.refresh(),
            on_error=lambda err: QMessageBox.warning(self, "Błąd", err),
        )
