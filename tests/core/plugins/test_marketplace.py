from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.core.plugins.marketplace import MarketplaceManager
from vibe.core.plugins.models import (
    MarketplaceConfig,
    MarketplaceIndex,
    MarketplacePluginRef,
)


class TestNormalizeUrl:
    def test_shorthand_to_github(self) -> None:
        assert (
            MarketplaceManager.normalize_url("owner/repo")
            == "https://github.com/owner/repo.git"
        )

    def test_shorthand_with_dot_git_does_not_duplicate_suffix(self) -> None:
        assert (
            MarketplaceManager.normalize_url("owner/repo.git")
            == "https://github.com/owner/repo.git"
        )

    def test_shorthand_with_trailing_slash(self) -> None:
        assert (
            MarketplaceManager.normalize_url("owner/repo/")
            == "https://github.com/owner/repo.git"
        )

    def test_https_passthrough(self) -> None:
        url = "https://example.com/repo.git"
        assert MarketplaceManager.normalize_url(url) == url

    def test_git_ssh_passthrough(self) -> None:
        url = "git@github.com:owner/repo.git"
        assert MarketplaceManager.normalize_url(url) == url


class TestCacheKey:
    def test_deterministic(self) -> None:
        key1 = MarketplaceManager._cache_key("https://example.com/repo")
        key2 = MarketplaceManager._cache_key("https://example.com/repo")
        assert key1 == key2

    def test_different_urls_different_keys(self) -> None:
        key1 = MarketplaceManager._cache_key("https://example.com/repo1")
        key2 = MarketplaceManager._cache_key("https://example.com/repo2")
        assert key1 != key2


class TestSearch:
    def test_search_by_name(self) -> None:
        mgr = MarketplaceManager()
        index = MarketplaceIndex(
            name="test",
            plugins=[
                MarketplacePluginRef(
                    name="search-tool", source="a", description="Search utility"
                ),
                MarketplacePluginRef(
                    name="lint-tool", source="b", description="Linter"
                ),
            ],
        )
        results = mgr.search(index, "search")
        assert len(results) == 1
        assert results[0].name == "search-tool"

    def test_search_by_description(self) -> None:
        mgr = MarketplaceManager()
        index = MarketplaceIndex(
            name="test",
            plugins=[
                MarketplacePluginRef(
                    name="tool-a", source="a", description="Awesome linter"
                )
            ],
        )
        results = mgr.search(index, "linter")
        assert len(results) == 1

    def test_search_case_insensitive(self) -> None:
        mgr = MarketplaceManager()
        index = MarketplaceIndex(
            name="test",
            plugins=[
                MarketplacePluginRef(name="My-Tool", source="a", description="desc")
            ],
        )
        results = mgr.search(index, "MY-TOOL")
        assert len(results) == 1

    def test_search_no_results(self) -> None:
        mgr = MarketplaceManager()
        index = MarketplaceIndex(name="test", plugins=[])
        results = mgr.search(index, "anything")
        assert results == []


class TestGetCacheDirFor:
    def test_returns_path_under_cache_dir(self) -> None:
        mgr = MarketplaceManager()
        config = MarketplaceConfig(url="owner/repo")
        cache_dir = mgr.get_cache_dir_for(config)
        assert str(cache_dir).startswith(str(mgr._cache_dir))


class TestMarketplaceUrlInjection:
    def test_fetch_rejects_url_starting_with_dash(self) -> None:
        with pytest.raises(ValidationError):
            MarketplaceConfig(url="--upload-pack=evil")

    def test_marketplace_config_rejects_invalid_format(self) -> None:
        with pytest.raises(ValidationError):
            MarketplaceConfig(url="no-slash-here")
