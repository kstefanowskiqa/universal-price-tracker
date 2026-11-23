from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.theme import ThemePalette
from price_tracker.desktop.widgets import configure_table, format_iso_local, make_item


class JobsView(QWidget):
    """Podgląd kolejki monitorowania, statusu harmonogramu i szczegółowej diagnostyki błędów."""

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

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Kolejka zadań i diagnostyka")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Wszystkie statusy", None)
        for st in ("pending", "running", "success", "failed", "retry_scheduled"):
            self.status_filter.addItem(st, st)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.status_filter)

        self.pause_btn = QPushButton("Wstrzymaj harmonogram")
        self.pause_btn.clicked.connect(self._toggle_scheduler)
        header.addWidget(self.pause_btn)

        refresh_btn = QPushButton("Odśwież")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        self.scheduler_strip = QLabel("Status harmonogramu: ...")
        self.scheduler_strip.setObjectName("MetricsStrip")
        root.addWidget(self.scheduler_strip)

        self.table = QTableWidget()
        configure_table(
            self.table,
            [
                "ID",
                "Produkt",
                "Sklep",
                "Status",
                "Próba",
                "HTTP",
                "Czas (ms)",
                "Warstwa",
                "Kod błędu",
                "Zaplanowano",
                "Szczegóły diagnostyczne",
            ],
        )
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 105)
        self.table.setColumnWidth(4, 55)
        self.table.setColumnWidth(5, 55)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 100)
        self.table.setColumnWidth(8, 130)
        self.table.setColumnWidth(9, 135)
        root.addWidget(self.table, stretch=1)

        self._is_paused = False

    def apply_palette(self, palette: ThemePalette) -> None:
        self._palette = palette

    def refresh(self) -> None:
        st_filter = self.status_filter.currentData()

        def _work() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            return (
                self._api.get_scheduler_status(),
                self._api.list_jobs(status=str(st_filter) if st_filter else None, limit=120),
            )

        run_in_background(
            _work,
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd pobierania kolejki: {err}"),
        )

    def _render(self, data: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
        sched, jobs = data
        self._is_paused = bool(sched["is_paused"])
        state_txt = (
            "WSTRZYMANY"
            if self._is_paused
            else ("AKTYWNY" if sched["is_running"] else "ZATRZYMANY")
        )
        self.scheduler_strip.setText(
            f"Harmonogram: {state_txt}   ·   "
            f"Aktywne workery: {sched['active_workers']}   ·   "
            f"Zadania w kolejce: {sched['pending_jobs']}"
        )
        self.pause_btn.setText(
            "Wznów harmonogram" if self._is_paused else "Wstrzymaj harmonogram"
        )

        self.table.setRowCount(len(jobs))
        for row, j in enumerate(jobs):
            st = str(j["status"])
            color = (
                self._palette.positive
                if st == "success"
                else self._palette.negative
                if st == "failed"
                else self._palette.warning
                if st == "retry_scheduled"
                else self._palette.text_secondary
            )
            self.table.setItem(row, 0, make_item(str(j["id"]), align_right=True))
            self.table.setItem(row, 1, make_item(str(j["product_name"])))
            self.table.setItem(row, 2, make_item(str(j["store_name"])))
            self.table.setItem(row, 3, make_item(st.upper(), color=color))
            self.table.setItem(row, 4, make_item(str(j["attempt_number"]), align_right=True))
            self.table.setItem(
                row,
                5,
                make_item(str(j["http_status"]) if j.get("http_status") else "—", align_right=True),
            )
            dur_txt = str(j["duration_ms"]) if j.get("duration_ms") is not None else "—"
            self.table.setItem(row, 6, make_item(dur_txt, align_right=True))
            self.table.setItem(row, 7, make_item(str(j.get("extraction_layer") or "—")))
            self.table.setItem(row, 8, make_item(str(j.get("error_code") or "none")))
            self.table.setItem(row, 9, make_item(format_iso_local(j.get("scheduled_at"))))
            self.table.setItem(row, 10, make_item(str(j.get("error_detail") or "—")))

    def _toggle_scheduler(self) -> None:
        fn = self._api.resume_scheduler if self._is_paused else self._api.pause_scheduler
        run_in_background(
            fn,
            on_success=lambda _: self.refresh(),
            on_error=lambda err: self._on_status(f"Błąd przełączania harmonogramu: {err}"),
        )
