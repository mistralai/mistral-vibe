from __future__ import annotations

from vibe.cli.vscode_extension_promo import (
    VscodeExtensionPromoRepository,
    VscodeExtensionPromoState,
)


class FakeVscodeExtensionPromoRepository(VscodeExtensionPromoRepository):
    def __init__(self, state: VscodeExtensionPromoState | None = None) -> None:
        self.state: VscodeExtensionPromoState | None = state

    async def get(self) -> VscodeExtensionPromoState | None:
        return self.state

    async def set(self, state: VscodeExtensionPromoState) -> None:
        self.state = state
