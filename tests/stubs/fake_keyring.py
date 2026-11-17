from __future__ import annotations

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError


class FakeKeyring(KeyringBackend):
    """An in-memory OS keyring backend that records every read.

    Install it with ``keyring.set_keyring``; the suite's autouse
    ``_disable_os_keyring`` fixture restores the original backend afterwards.
    ``error``, when set, is raised by every read, as a locked keyring would.
    """

    priority = 100  # type: ignore[assignment]

    def __init__(self, *, error: KeyringError | None = None) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.reads: list[tuple[str, str]] = []
        self._error = error

    def get_password(self, service: str, username: str) -> str | None:
        self.reads.append((service, username))
        if self._error is not None:
            raise self._error
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)
