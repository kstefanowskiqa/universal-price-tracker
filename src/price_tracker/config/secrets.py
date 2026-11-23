from __future__ import annotations

import os
from abc import ABC, abstractmethod

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from price_tracker.core.exceptions import SecretStoreError

SERVICE_NAME = "UniversalPriceTracker"
ENV_SECRET_PREFIX = "PT_SECRET_"


class SecretStore(ABC):
    """Abstrakcja bezpiecznego magazynu sekretów (np. token bota Telegrama)."""

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        """Pobiera sekret po nazwie klucza lub zwraca None, jeśli nie istnieje."""

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        """Zapisuje sekret pod podanym kluczem."""

    @abstractmethod
    def delete_secret(self, key: str) -> None:
        """Usuwa sekret spod podanego klucza."""


class EnvSecretStore(SecretStore):
    """Magazyn tylko do odczytu oparty o zmienne środowiskowe (Docker / VPS / CI)."""

    def __init__(self, prefix: str = ENV_SECRET_PREFIX) -> None:
        self._prefix = prefix

    def _env_name(self, key: str) -> str:
        normalized = key.upper().replace("-", "_").replace(".", "_")
        return f"{self._prefix}{normalized}"

    def get_secret(self, key: str) -> str | None:
        val = os.environ.get(self._env_name(key))
        return val.strip() if val and val.strip() else None

    def set_secret(self, key: str, value: str) -> None:
        raise SecretStoreError(
            "EnvSecretStore jest tylko do odczytu. Ustaw zmienną środowiskową: "
            f"{self._env_name(key)}"
        )

    def delete_secret(self, key: str) -> None:
        raise SecretStoreError(
            "EnvSecretStore jest tylko do odczytu. Usuń zmienną środowiskową ręcznie."
        )


class KeyringSecretStore(SecretStore):
    """Magazyn oparty o systemowy pęk kluczy (Windows Credential Manager / macOS Keychain)."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self._service_name = service_name

    def get_secret(self, key: str) -> str | None:
        try:
            return keyring.get_password(self._service_name, key)
        except KeyringError as exc:
            raise SecretStoreError(f"Nie udało się odczytać sekretu '{key}' z keyring.") from exc

    def set_secret(self, key: str, value: str) -> None:
        if not value or not value.strip():
            raise SecretStoreError("Wartość sekretu nie może być pusta.")
        try:
            keyring.set_password(self._service_name, key, value.strip())
        except KeyringError as exc:
            raise SecretStoreError(f"Nie udało się zapisać sekretu '{key}' w keyring.") from exc

    def delete_secret(self, key: str) -> None:
        try:
            keyring.delete_password(self._service_name, key)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise SecretStoreError(f"Nie udało się usunąć sekretu '{key}' z keyring.") from exc


class ChainedSecretStore(SecretStore):
    """Odczytuje najpierw ze zmiennych środowiskowych (priorytet VPS/Docker), potem z keyring."""

    def __init__(
        self,
        env_store: EnvSecretStore | None = None,
        keyring_store: KeyringSecretStore | None = None,
    ) -> None:
        self._env_store = env_store or EnvSecretStore()
        self._keyring_store = keyring_store or KeyringSecretStore()

    def get_secret(self, key: str) -> str | None:
        env_val = self._env_store.get_secret(key)
        if env_val is not None:
            return env_val
        try:
            return self._keyring_store.get_secret(key)
        except SecretStoreError:
            # W środowiskach headless Linux/Docker bez demona keyring nie blokujemy działania
            return None

    def set_secret(self, key: str, value: str) -> None:
        self._keyring_store.set_secret(key, value)

    def delete_secret(self, key: str) -> None:
        self._keyring_store.delete_secret(key)


def get_secret_store() -> SecretStore:
    return ChainedSecretStore()
