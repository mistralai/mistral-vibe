from __future__ import annotations

import asyncio
from pathlib import Path

from vibe.cli.cache import read_cache, write_cache
from vibe.cli.vscode_extension_promo._port import (
    VscodeExtensionPromoRepository,
    VscodeExtensionPromoState,
)
from vibe.core.paths import VIBE_HOME

_CACHE_SECTION = "vscode_extension_promo"


class FileSystemVscodeExtensionPromoRepository(VscodeExtensionPromoRepository):
    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base_path = Path(base_path) if base_path is not None else VIBE_HOME.path
        self._cache_file = self._base_path / "cache.toml"

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
            write_cache,
            self._cache_file,
            _CACHE_SECTION,
            {"shown_count": state.shown_count},
        )

    def _read_section(self) -> dict | None:
        cache = read_cache(self._cache_file)
        section = cache.get(_CACHE_SECTION)
        if isinstance(section, dict):
            return section
        return None
