from price_tracker.scraping.adapters.base import BaseStoreAdapter
from price_tracker.scraping.adapters.registry import AdapterRegistry, build_default_registry
from price_tracker.scraping.adapters.stores import (
    DeclarativeStoreAdapter,
    HMAdapter,
    VictoriasSecretAdapter,
    XKomAdapter,
)
from price_tracker.scraping.adapters.universal import UniversalAdapter

__all__ = [
    "BaseStoreAdapter",
    "UniversalAdapter",
    "DeclarativeStoreAdapter",
    "XKomAdapter",
    "HMAdapter",
    "VictoriasSecretAdapter",
    "AdapterRegistry",
    "build_default_registry",
]
