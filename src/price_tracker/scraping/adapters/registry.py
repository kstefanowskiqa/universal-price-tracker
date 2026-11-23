from __future__ import annotations

from price_tracker.scraping.adapters.base import BaseStoreAdapter
from price_tracker.scraping.adapters.stores import (
    HMAdapter,
    VictoriasSecretAdapter,
    XKomAdapter,
)
from price_tracker.scraping.adapters.universal import UniversalAdapter


class AdapterRegistry:
    """Rejestr adapterów umożliwiający dodawanie nowych sklepów bez zmian w silniku głównym."""

    def __init__(self, fallback: BaseStoreAdapter | None = None) -> None:
        self._fallback: BaseStoreAdapter = fallback or UniversalAdapter()
        self._by_key: dict[str, BaseStoreAdapter] = {self._fallback.adapter_key: self._fallback}
        self._domain_adapters: list[BaseStoreAdapter] = []

    def register(self, adapter: BaseStoreAdapter) -> None:
        self._by_key[adapter.adapter_key] = adapter
        if adapter.supported_domains and adapter not in self._domain_adapters:
            self._domain_adapters.append(adapter)

    def resolve(self, domain: str, preferred_key: str | None = None) -> BaseStoreAdapter:
        if preferred_key and preferred_key != "universal" and preferred_key in self._by_key:
            return self._by_key[preferred_key]
        for adapter in self._domain_adapters:
            if adapter.matches_domain(domain):
                return adapter
        return self._fallback

    def list_adapters(self) -> list[BaseStoreAdapter]:
        return list(self._by_key.values())


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(XKomAdapter())
    registry.register(HMAdapter())
    registry.register(VictoriasSecretAdapter())
    return registry
