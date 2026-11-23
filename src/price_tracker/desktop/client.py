from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


class ApiClientError(Exception):
    """Błąd komunikacji lub odpowiedź błędna z backendu REST API."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """Klient REST API wykorzystywany przez aplikację desktopową (w wątkach roboczych)."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 35.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def reconfigure(self, base_url: str, api_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        if authenticated:
            headers["X-API-Token"] = self._api_token

        clean_params = (
            {k: v for k, v in params.items() if v is not None and v != ""}
            if params
            else None
        )
        url = f"{self._base_url}{path}"

        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout, connect=8.0),
                transport=self._transport,
            ) as client:
                response = client.request(
                    method,
                    url,
                    params=clean_params,
                    json=json_body,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise ApiClientError("Przekroczono czas oczekiwania na odpowiedź backendu.") from exc
        except httpx.HTTPError as exc:
            raise ApiClientError(f"Brak połączenia z backendem ({self._base_url}): {exc}") from exc

        if response.status_code == 204:
            return None

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code >= 400:
            detail = (
                str(data.get("detail"))
                if isinstance(data, dict) and "detail" in data
                else f"Błąd HTTP {response.status_code}"
            )
            raise ApiClientError(detail, status_code=response.status_code)

        return data

    # ---- System & Health ----

    def health(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("GET", "/health", authenticated=False)
        return res

    def system_info(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("GET", "/api/v1/system/info")
        return res

    def get_settings(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("GET", "/api/v1/system/settings")
        return res

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request("PUT", "/api/v1/system/settings", json_body=payload)
        return res

    def get_logs(
        self,
        *,
        level: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request(
            "GET",
            "/api/v1/system/logs",
            params={"level": level, "search": search, "limit": limit},
        )
        return res

    # ---- Dashboard & Analityka ----

    def get_dashboard(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("GET", "/api/v1/dashboard")
        return res

    def get_offer_history(self, offer_id: int, time_range: str = "30d") -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "GET",
            f"/api/v1/offers/{offer_id}/history",
            params={"range": time_range},
        )
        return res

    def get_product_comparison(self, product_id: int, time_range: str = "30d") -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "GET",
            f"/api/v1/products/{product_id}/comparison",
            params={"range": time_range},
        )
        return res

    # ---- Katalog produktów i ofert ----

    def preview_url(self, url: str, variant_label: str = "") -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "POST",
            "/api/v1/urls/preview",
            json_body={"url": url, "variant_label": variant_label},
        )
        return res

    def list_categories(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request("GET", "/api/v1/categories")
        return res

    def create_category(self, name: str) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/categories", json_body={"name": name})
        return res

    def list_tags(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request("GET", "/api/v1/tags")
        return res

    def list_products(
        self,
        *,
        search: str | None = None,
        category_id: int | None = None,
        tag: str | None = None,
        store_id: int | None = None,
        availability: str | None = None,
        only_errors: bool = False,
    ) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request(
            "GET",
            "/api/v1/products",
            params={
                "search": search,
                "category_id": category_id,
                "tag": tag,
                "store_id": store_id,
                "availability": availability,
                "only_errors": "true" if only_errors else None,
            },
        )
        return res

    def create_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/products", json_body=payload)
        return res

    def update_product(self, product_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "PATCH", f"/api/v1/products/{product_id}", json_body=payload
        )
        return res

    def delete_product(self, product_id: int) -> None:
        self._request("DELETE", f"/api/v1/products/{product_id}")

    def add_offer(self, product_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "POST", f"/api/v1/products/{product_id}/offers", json_body=payload
        )
        return res

    def update_offer(self, offer_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "PATCH", f"/api/v1/offers/{offer_id}", json_body=payload
        )
        return res

    def delete_offer(self, offer_id: int) -> None:
        self._request("DELETE", f"/api/v1/offers/{offer_id}")

    def check_offer_now(self, offer_id: int) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", f"/api/v1/offers/{offer_id}/check-now")
        return res

    # ---- Sklepy, Adaptery, Zadania, Harmonogram ----

    def list_stores(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request("GET", "/api/v1/stores")
        return res

    def update_store(self, store_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "PATCH", f"/api/v1/stores/{store_id}", json_body=payload
        )
        return res

    def list_adapters(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request("GET", "/api/v1/adapters")
        return res

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request(
            "GET",
            "/api/v1/jobs",
            params={"status": status, "limit": limit},
        )
        return res

    def get_scheduler_status(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("GET", "/api/v1/scheduler/status")
        return res

    def pause_scheduler(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/scheduler/pause")
        return res

    def resume_scheduler(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/scheduler/resume")
        return res

    # ---- Powiadomienia i Telegram ----

    def list_alerts(self, *, offer_id: int | None = None) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request(
            "GET", "/api/v1/alerts", params={"offer_id": offer_id}
        )
        return res

    def create_alert(self, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/alerts", json_body=payload)
        return res

    def update_alert(self, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request("PATCH", f"/api/v1/alerts/{rule_id}", json_body=payload)
        return res

    def delete_alert(self, rule_id: int) -> None:
        self._request("DELETE", f"/api/v1/alerts/{rule_id}")

    def list_channels(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request("GET", "/api/v1/notifications/channels")
        return res

    def configure_telegram(self, payload: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = self._request(
            "PUT", "/api/v1/notifications/channels/telegram", json_body=payload
        )
        return res

    def test_telegram(self) -> dict[str, Any]:
        res: dict[str, Any] = self._request("POST", "/api/v1/notifications/channels/telegram/test")
        return res

    def list_notification_logs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = self._request(
            "GET", "/api/v1/notifications/logs", params={"limit": limit}
        )
        return res


_ACTIVE_TASKS: set[Any] = set()
_DISPATCHER: Any = None
_TASK_CLASS: Any = None


def _get_main_dispatcher() -> Any:
    global _DISPATCHER
    if _DISPATCHER is not None:
        return _DISPATCHER

    from PySide6.QtCore import QCoreApplication, QObject, Slot

    class _MainThreadDispatcher(QObject):
        @Slot(object)
        def dispatch(self, payload: Any) -> None:
            task, ok, value, on_success, on_error, on_finished = payload
            try:
                if ok:
                    if on_success is not None:
                        on_success(value)
                elif on_error is not None:
                    on_error(str(value))
            except RuntimeError:
                # Okno lub dialog zostało zamknięte przed zakończeniem żądania HTTP
                pass
            finally:
                _ACTIVE_TASKS.discard(task)
                if on_finished is not None:
                    on_finished()

    dispatcher = _MainThreadDispatcher()
    app = QCoreApplication.instance()
    if app is not None:
        dispatcher.moveToThread(app.thread())
    _DISPATCHER = dispatcher
    return _DISPATCHER


def _get_task_class() -> Any:
    global _TASK_CLASS
    if _TASK_CLASS is not None:
        return _TASK_CLASS

    from PySide6.QtCore import QObject, QRunnable, Signal

    class _WorkerSignals(QObject):
        completed = Signal(object)

    class _FunctionTask(QRunnable):
        def __init__(
            self,
            work_fn: Callable[[], Any],
            on_success: Callable[[Any], object] | None,
            on_error: Callable[[str], object] | None,
            on_finished: Callable[[], object] | None,
        ) -> None:
            super().__init__()
            self.setAutoDelete(False)
            self._work_fn = work_fn
            self._on_success = on_success
            self._on_error = on_error
            self._on_finished = on_finished
            self.signals = _WorkerSignals()

        def run(self) -> None:
            try:
                result = self._work_fn()
            except Exception as exc:
                payload = (
                    self,
                    False,
                    str(exc),
                    self._on_success,
                    self._on_error,
                    self._on_finished,
                )
            else:
                payload = (
                    self,
                    True,
                    result,
                    self._on_success,
                    self._on_error,
                    self._on_finished,
                )
            self.signals.completed.emit(payload)

    _TASK_CLASS = _FunctionTask
    return _TASK_CLASS


def run_in_background(
    fn: Callable[[], Any],
    *,
    on_success: Callable[[Any], object] | None = None,
    on_error: Callable[[str], object] | None = None,
    on_finished: Callable[[], object] | None = None,
) -> None:
    """Uruchamia funkcję blokującą w QThreadPool; callbacki zawsze wracają do wątku GUI."""
    from PySide6.QtCore import Qt, QThreadPool

    dispatcher = _get_main_dispatcher()
    task_cls = _get_task_class()
    task = task_cls(fn, on_success, on_error, on_finished)
    _ACTIVE_TASKS.add(task)

    task.signals.completed.connect(
        dispatcher.dispatch,
        Qt.ConnectionType.QueuedConnection,
    )

    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.start(task)
