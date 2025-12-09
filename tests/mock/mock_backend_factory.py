from __future__ import annotations

from contextlib import contextmanager

from vibe.core.config import Backend
from vibe.core.llm.backend.factory import BACKEND_FACTORY


@contextmanager
def mock_backend_factory(backend_type: Backend, factory_func):
    original = BACKEND_FACTORY[backend_type]
    try:
        BACKEND_FACTORY[backend_type] = factory_func
        yield
    finally:
        BACKEND_FACTORY[backend_type] = original
