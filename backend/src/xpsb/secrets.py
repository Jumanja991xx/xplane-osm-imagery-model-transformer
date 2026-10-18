"""API key storage in the OS credential store (Windows Credential Manager).

The key is never returned through the API, never logged and never written to
job files. Callers only learn whether a key exists.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Protocol

log = logging.getLogger(__name__)

SERVICE = "XPlaneAISceneryBuilder"
ANTHROPIC_ACCOUNT = "anthropic_api_key"


class SecretStore(Protocol):
    def available(self) -> bool: ...
    def get(self, account: str) -> str | None: ...
    def set(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


class KeyringSecretStore:
    def __init__(self) -> None:
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                import keyring
                from keyring.backends import fail

                self._available = not isinstance(keyring.get_keyring(), fail.Keyring)
            except Exception:  # noqa: BLE001 - any backend problem means "unavailable"
                self._available = False
        return self._available

    def get(self, account: str) -> str | None:
        if not self.available():
            return None
        import keyring

        try:
            return keyring.get_password(SERVICE, account)
        except Exception as exc:  # noqa: BLE001
            log.warning("credential store read failed: %s", type(exc).__name__)
            return None

    def set(self, account: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, account, value)

    def delete(self, account: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(SERVICE, account)


class MemorySecretStore:
    """Used in tests and as a fallback-free stand-in; never persists."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def get(self, account: str) -> str | None:
        return self._values.get(account)

    def set(self, account: str, value: str) -> None:
        self._values[account] = value

    def delete(self, account: str) -> None:
        self._values.pop(account, None)


def normalize_api_key(raw: str) -> str:
    key = raw.strip()
    if not key or len(key) > 512 or any(c.isspace() for c in key):
        raise ValueError("invalid_api_key")
    return key
