from __future__ import annotations

import keyring
from keyring.errors import KeyringError
import pytest


@pytest.fixture(autouse=True)
def disable_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("keyring disabled in tests")

    monkeypatch.setattr(keyring, "set_password", unavailable)
    monkeypatch.setattr(keyring, "delete_password", lambda service, username: None)
