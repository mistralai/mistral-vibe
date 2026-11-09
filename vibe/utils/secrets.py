"""Secrets some code in this process holds right now, so output can hide them.

Kept free of heavy imports: the log formatter uses it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import threading

REDACTED = "[REDACTED]"

# Counted, because two runs may hold the same value: it stays hidden until
# both have let go of it.
_held: Counter[str] = Counter()
_lock = threading.Lock()


@contextmanager
def holding_secrets(values: Iterable[str]) -> Iterator[None]:
    """Have :func:`redact` hide ``values`` while the context lasts."""
    secrets = [value for value in values if value]
    with _lock:
        _held.update(secrets)
    try:
        yield
    finally:
        with _lock:
            _held.subtract(secrets)
            for secret in secrets:
                if _held[secret] <= 0:
                    del _held[secret]


def redact(text: str) -> str:
    """``text`` with every secret held right now replaced by ``[REDACTED]``."""
    if not _held:
        return text
    with _lock:
        # Longest first, so a secret that contains another is hidden whole.
        secrets = sorted(_held, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text
