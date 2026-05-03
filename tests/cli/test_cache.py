from __future__ import annotations

from pathlib import Path
import tomllib

from vibe.cli.cache import read_cache, write_cache


class TestReadCache:
    def test_reads_valid_toml(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text('[update_cache]\nlatest_version = "1.0.0"\n')

        result = read_cache(cache_path)

        assert result["update_cache"]["latest_version"] == "1.0.0"

    def test_returns_empty_dict_when_missing(self, tmp_path: Path) -> None:
        assert read_cache(tmp_path / "missing.toml") == {}

    def test_returns_empty_dict_when_corrupted(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text("{bad toml")

        assert read_cache(cache_path) == {}


class TestWriteCache:
    def test_writes_new_file(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"

        write_cache(cache_path, "feedback", {"last_shown_at": 100.0})

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["feedback"]["last_shown_at"] == 100.0

    def test_merges_with_existing(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.toml"
        cache_path.write_text('[update_cache]\nlatest_version = "1.0.0"\n')

        write_cache(cache_path, "feedback", {"last_shown_at": 200.0})

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

        write_cache(
            cache_path,
            "update_cache",
            {"latest_version": "2.0.0", "stored_at_timestamp": 2},
        )

        with cache_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["update_cache"]["latest_version"] == "2.0.0"
        assert data["update_cache"]["stored_at_timestamp"] == 2
        assert data["update_cache"]["seen_whats_new_version"] == "1.0.0"
        assert data["feedback"]["last_shown_at"] == 100.0
