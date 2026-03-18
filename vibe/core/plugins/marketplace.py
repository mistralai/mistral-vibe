from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import time

from vibe.core.logger import logger
from vibe.core.paths import VIBE_HOME
from vibe.core.plugins.models import (
    MarketplaceConfig,
    MarketplaceIndex,
    MarketplacePluginRef,
)

_FETCH_TTL_SECONDS = 300  # Skip re-fetch if pulled within this window


class MarketplaceManager:
    """Manages marketplace repository cloning, searching, and plugin resolution."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or VIBE_HOME.path / "marketplace-cache"
        self._fetch_timestamps: dict[str, float] = {}

    def fetch(
        self, config: MarketplaceConfig, *, force: bool = False
    ) -> MarketplaceIndex:
        url = self.normalize_url(config.url)
        if url.startswith("-"):
            msg = "Invalid marketplace URL"
            raise ValueError(msg)
        clone_dir = self._cache_dir / self._cache_key(url)

        now = time.monotonic()
        last_fetch = self._fetch_timestamps.get(url, 0.0)
        skip_pull = not force and (now - last_fetch) < _FETCH_TTL_SECONDS

        if (clone_dir / ".git").is_dir():
            if not skip_pull:
                logger.info("Updating marketplace cache for %s", url)
                subprocess.run(
                    ["git", "-C", str(clone_dir), "pull", "--ff-only"], check=True
                )
                self._fetch_timestamps[url] = now
        else:
            clone_dir.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Cloning marketplace from %s", url)
            subprocess.run(
                ["git", "clone", "--depth", "1", "--", url, str(clone_dir)], check=True
            )
            self._fetch_timestamps[url] = now

        return MarketplaceIndex.from_dir(clone_dir)

    def search(self, index: MarketplaceIndex, query: str) -> list[MarketplacePluginRef]:
        q = query.lower()
        if not q:
            return list(index.plugins)
        return [
            ref
            for ref in index.plugins
            if q in ref.name.lower() or q in ref.description.lower()
        ]

    def get_cache_dir_for(self, config: MarketplaceConfig) -> Path:
        url = self.normalize_url(config.url)
        return self._cache_dir / self._cache_key(url)

    def is_marketplace(self, config: MarketplaceConfig) -> bool:
        """Check whether the repo behind *config* contains a marketplace index."""
        try:
            self.fetch(config)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def search_all(
        self, configs: list[MarketplaceConfig], query: str
    ) -> list[tuple[MarketplaceConfig, MarketplacePluginRef]]:
        """Search across multiple marketplaces, returning tagged results."""
        results: list[tuple[MarketplaceConfig, MarketplacePluginRef]] = []
        for config in configs:
            try:
                index = self.fetch(config)
                for ref in self.search(index, query):
                    results.append((config, ref))
            except (subprocess.CalledProcessError, FileNotFoundError):
                logger.warning("Failed to fetch marketplace '%s'", config.url)
        return results

    def find_plugin(
        self, configs: list[MarketplaceConfig], plugin_name: str
    ) -> tuple[MarketplaceConfig, MarketplacePluginRef] | None:
        """Find a plugin by exact name across configured marketplaces."""
        for config in configs:
            try:
                index = self.fetch(config)
                if ref := next(
                    (p for p in index.plugins if p.name == plugin_name), None
                ):
                    return config, ref
            except (subprocess.CalledProcessError, FileNotFoundError):
                logger.warning("Failed to fetch marketplace '%s'", config.url)
        return None

    @staticmethod
    def normalize_url(url: str) -> str:
        if url.startswith(("https://", "http://", "git@")):
            return url
        return f"https://github.com/{url}.git"

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:12]
