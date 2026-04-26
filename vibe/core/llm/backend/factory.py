from __future__ import annotations

from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import Backend

BACKEND_FACTORY = {Backend.MISTRAL: MistralBackend, Backend.GENERIC: GenericBackend}
