from price_tracker.api.routers.analytics import router as analytics_router
from price_tracker.api.routers.monitoring import router as monitoring_router
from price_tracker.api.routers.notifications import router as notifications_router
from price_tracker.api.routers.products import router as products_router
from price_tracker.api.routers.system import router as system_router

__all__ = [
    "analytics_router",
    "monitoring_router",
    "notifications_router",
    "products_router",
    "system_router",
]
