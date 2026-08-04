from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VscodeExtensionPromoState:
    shown_count: int


class VscodeExtensionPromoRepository(Protocol):
    async def get(self) -> VscodeExtensionPromoState | None: ...
    async def set(self, state: VscodeExtensionPromoState) -> None: ...
