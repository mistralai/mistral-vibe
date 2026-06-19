from __future__ import annotations

from pathlib import Path
import tomllib

from vibe.core.cache_store import FileSystemVibeCodeCacheStore


class TestFileSystemVibeCodeCacheStore:
    def test_reads_valid_toml_section(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text('[update_cache]\nlatest_version = "1.0.0"\n')
        store = FileSystemVibeCodeCacheStore(cache_path)

        result = store.read_section("update_cache")

        assert result["latest_version"] == "1.0.0"

    def test_returns_empty_dict_when_file_is_missing(self, tmp_path: Path) -> None:
        store = FileSystemVibeCodeCacheStore(tmp_path / "missing.toml")

        assert store.read_section("update_cache") == {}

    def test_returns_empty_dict_when_file_is_corrupted(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text("{bad toml")
        store = FileSystemVibeCodeCacheStore(cache_path)

        assert store.read_section("update_cache") == {}

    def test_writes_new_file(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        store = FileSystemVibeCodeCacheStore(cache_path)

        store.write_section("feedback", {"last_shown_at": 100.0})

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["feedback"]["last_shown_at"] == 100.0

    def test_merges_with_existing(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text('[update_cache]\nlatest_version = "1.0.0"\n')
        store = FileSystemVibeCodeCacheStore(cache_path)

        store.write_section("feedback", {"last_shown_at": 200.0})

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["update_cache"]["latest_version"] == "1.0.0"
        assert data["feedback"]["last_shown_at"] == 200.0

    def test_merges_within_section_and_leaves_other_sections_alone(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text(
            "[update_cache]\n"
            'latest_version = "1.0.0"\n'
            "stored_at_timestamp = 1\n"
            'seen_whats_new_version = "1.0.0"\n\n'
            "[feedback]\n"
            "last_shown_at = 100.0\n"
        )
        store = FileSystemVibeCodeCacheStore(cache_path)

        store.write_section(
            "update_cache", {"latest_version": "2.0.0", "stored_at_timestamp": 2}
        )

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["update_cache"]["latest_version"] == "2.0.0"
        assert data["update_cache"]["stored_at_timestamp"] == 2
        assert data["update_cache"]["seen_whats_new_version"] == "1.0.0"
        assert data["feedback"]["last_shown_at"] == 100.0

    def test_reads_section(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text("[feedback]\nlast_shown_at = 100\n")
        store = FileSystemVibeCodeCacheStore(cache_path)

        assert store.read_section("feedback") == {"last_shown_at": 100}

    def test_returns_empty_dict_for_missing_section(self, tmp_path: Path) -> None:
        store = FileSystemVibeCodeCacheStore(tmp_path / "cache.toml")

        assert store.read_section("feedback") == {}

    def test_writes_section(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        store = FileSystemVibeCodeCacheStore(cache_path)

        store.write_section("feedback", {"last_shown_at": 100})

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["feedback"]["last_shown_at"] == 100

    def test_replaces_non_table_section_when_writing(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text('feedback = "not-a-table"\n')
        store = FileSystemVibeCodeCacheStore(cache_path)

        store.write_section("feedback", {"last_shown_at": 100})

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["feedback"]["last_shown_at"] == 100
