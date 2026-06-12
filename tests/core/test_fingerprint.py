from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.fingerprint import capture_stable_file, create_dict_fingerprint
from vibe.core.config.types import ConcurrencyConflictError


class TestCaptureStableFile:
    def test_captures_unchanged_file(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with capture_stable_file(path) as (file, first_fingerprint):
            assert file.read() == b"key = 1"

        with capture_stable_file(path) as (file, second_fingerprint):
            assert file.read() == b"key = 1"

        assert isinstance(first_fingerprint, str)
        assert first_fingerprint
        assert first_fingerprint == second_fingerprint

    def test_raises_when_file_changes(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with pytest.raises(ConcurrencyConflictError) as exc_info:
            with capture_stable_file(path):
                path.write_text("key = 123")

        assert exc_info.value.actual_fp != exc_info.value.expected_fp

    def test_raises_when_path_is_replaced_after_open(
        self, tmp_working_directory: Path
    ) -> None:
        path = tmp_working_directory / "config.toml"
        replacement = tmp_working_directory / "replacement.toml"
        path.write_text("key = 1")
        replacement.write_text("key = 2")

        with pytest.raises(ConcurrencyConflictError) as exc_info:
            with capture_stable_file(path) as (file, _):
                replacement.replace(path)
                assert file.read() == b"key = 1"

        assert exc_info.value.actual_fp != exc_info.value.expected_fp

    def test_raises_when_file_disappears_after_read(
        self, tmp_working_directory: Path
    ) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with pytest.raises(FileNotFoundError):
            with capture_stable_file(path) as (file, _):
                assert file.read() == b"key = 1"
                path.unlink()

    def test_raises_when_file_is_missing(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "missing.toml"

        with pytest.raises(FileNotFoundError):
            with capture_stable_file(path):
                pass


class TestCreateDictFingerprint:
    def test_empty_dict_returns_stable_non_empty_token(self) -> None:
        first_fingerprint = create_dict_fingerprint({})
        second_fingerprint = create_dict_fingerprint({})

        assert isinstance(first_fingerprint, str)
        assert first_fingerprint
        assert first_fingerprint == second_fingerprint

    def test_stable_for_same_dict(self) -> None:
        data = {
            "VIBE_MODEL": "mistral-large",
            "VIBE_THEME": "dark",
            "VIBE_TOOLS": ["read", "write"],
        }
        fp1 = create_dict_fingerprint(data)
        fp2 = create_dict_fingerprint(data)
        assert fp1 == fp2

    def test_order_independent(self) -> None:
        fp1 = create_dict_fingerprint({"a": "1", "b": "2"})
        fp2 = create_dict_fingerprint({"b": "2", "a": "1"})
        assert fp1 == fp2

    def test_serializes_path_values(self) -> None:
        fp1 = create_dict_fingerprint({
            "tool_paths": [Path("/tmp/custom-tools")],
            "agent_paths": [Path("agents")],
        })
        fp2 = create_dict_fingerprint({
            "tool_paths": ["/tmp/custom-tools"],
            "agent_paths": ["agents"],
        })
        assert fp1 == fp2

    def test_changes_when_list_order_changes(self) -> None:
        fp1 = create_dict_fingerprint({"tools": ["read", "write"]})
        fp2 = create_dict_fingerprint({"tools": ["write", "read"]})
        assert fp1 != fp2

    def test_changes_when_value_changes(self) -> None:
        fp1 = create_dict_fingerprint({"VIBE_MODEL": "mistral-large"})
        fp2 = create_dict_fingerprint({"VIBE_MODEL": "devstral-2"})
        assert fp1 != fp2

    def test_changes_when_key_added(self) -> None:
        fp1 = create_dict_fingerprint({"a": "1"})
        fp2 = create_dict_fingerprint({"a": "1", "b": "2"})
        assert fp1 != fp2
