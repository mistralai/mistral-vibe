from __future__ import annotations

import asyncio
from pathlib import Path

from vibe.cli.vscode_extension_promo._port import (
    VscodeExtensionPromoRepository,
    VscodeExtensionPromoState,
)
from vibe.core.cache_store import FileSystemVibeCodeCacheStore, VibeCodeCacheStore
from vibe.core.paths import VIBE_HOME

_CACHE_SECTION = "vscode_extension_promo"


class FileSystemVscodeExtensionPromoRepository(VscodeExtensionPromoRepository):
    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base_path = Path(base_path) if base_path is not None else VIBE_HOME.path
        self._cache_file = self._base_path / "cache.toml"
        self._cache_store: VibeCodeCacheStore = FileSystemVibeCodeCacheStore(
            self._cache_file
        )

    async def get(self) -> VscodeExtensionPromoState | None:
        data = await asyncio.to_thread(self._read_section)
        if data is None:
            return None
        shown_count = data.get("shown_count")
        if not isinstance(shown_count, int):
            return None
        return VscodeExtensionPromoState(shown_count=shown_count)

    async def set(self, state: VscodeExtensionPromoState) -> None:
        await asyncio.to_thread(
            self._cache_store.write_section,
            _CACHE_SECTION,
            {"shown_count": state.shown_count},
        )

    def _read_section(self) -> dict | None:
        section = self._cache_store.read_section(_CACHE_SECTION)
        if section:
            return section
        return None
