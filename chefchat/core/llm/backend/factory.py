from __future__ import annotations

from chefchat.core.config import Backend
from chefchat.core.llm.backend.generic import GenericBackend
from chefchat.core.llm.backend.mistral import MistralBackend

BACKEND_FACTORY = {Backend.MISTRAL: MistralBackend, Backend.GENERIC: GenericBackend}
