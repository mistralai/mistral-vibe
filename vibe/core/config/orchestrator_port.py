from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig


class ConfigOrchestratorPort[S: AnyVibeConfig](Protocol):
    """Write/lifecycle surface shared by ConfigOrchestrator and the legacy adapter.

    Kept intentionally minimal: only operations both backends can honor
    truthfully. Layer-aware methods (apply_patch, subscribe, get_layer) are
    excluded because the legacy config has no layers.
    """

    @property
    def config(self) -> S: ...

    async def set_field(
        self,
        path: str,
        value: Any,
        reason: str = "No reason",
        *,
        target_layer: str | None = None,
    ) -> list[BaseException]: ...

    async def reload(self) -> None: ...
