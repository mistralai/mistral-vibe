from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig


class ModelAvailabilitySource(Protocol):
    """Asks a deployment which of ``models`` (in preference order) it serves.

    Returns a verdict per model it decided and leaves out the rest;
    ``timeout_seconds`` bounds the whole check. Caching is not its concern.
    """

    async def check(
        self,
        *,
        provider: ProviderConfig,
        models: Sequence[ModelConfig],
        timeout_seconds: float,
    ) -> Mapping[str, bool]: ...
