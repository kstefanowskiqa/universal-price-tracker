from __future__ import annotations


class PriceTrackerError(Exception):
    """Bazowy wyjątek aplikacji."""


class ConfigurationError(PriceTrackerError):
    """Błąd konfiguracji środowiska lub ustawień aplikacji."""


class SecretStoreError(PriceTrackerError):
    """Błąd odczytu lub zapisu sekretów."""


class DatabaseError(PriceTrackerError):
    """Błąd warstwy bazy danych."""


class ValidationError(PriceTrackerError):
    """Błąd walidacji danych domenowych."""


class NotFoundError(PriceTrackerError):
    """Żądany zasób nie istnieje."""


class ConflictError(PriceTrackerError):
    """Konflikt unikalności zasobu (np. duplikat URL + wariantu)."""
