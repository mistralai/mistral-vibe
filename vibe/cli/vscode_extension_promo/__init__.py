from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from vibe.cli.vscode_extension_promo._port import (
    VscodeExtensionPromoRepository,
    VscodeExtensionPromoState,
)
from vibe.cli.vscode_extension_promo.adapters.filesystem_repository import (
    FileSystemVscodeExtensionPromoRepository,
)

__all__ = [
    "MAX_SHOWN_COUNT",
    "PROMO_START",
    "FileSystemVscodeExtensionPromoRepository",
    "VscodeExtensionPromo",
    "VscodeExtensionPromoRepository",
    "VscodeExtensionPromoState",
    "should_show_promo",
]


MAX_SHOWN_COUNT = 10
PROMO_START = datetime(2026, 5, 28, 16, 0, tzinfo=UTC)


def should_show_promo(
    state: VscodeExtensionPromoState | None, now: datetime | None = None
) -> bool:
    current = now or datetime.now(UTC)
    if current < PROMO_START:
        return False
    if state is None:
        return True
    return state.shown_count < MAX_SHOWN_COUNT


@dataclass(frozen=True, slots=True)
class VscodeExtensionPromo:
    repository: VscodeExtensionPromoRepository
    initial_state: VscodeExtensionPromoState | None
