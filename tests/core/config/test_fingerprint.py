from __future__ import annotations

from itertools import cycle
from pathlib import Path
import threading

import pytest

from vibe.core.config.fingerprint import (
    create_dict_fingerprint,
    create_file_fingerprint,
    open_fingerprinted_file,
)
from vibe.core.config.layer import RawConfig
from vibe.core.config.layers._base import _read_toml_snapshot, _write_toml_snapshot


class TestOpenFingerprintedFile:
    def test_pairs_the_file_with_a_fingerprint_of_its_bytes(
        self, tmp_working_directory: Path
    ) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with open_fingerprinted_file(path) as (file, first_fingerprint):
            assert file.read() == b"key = 1"

        with open_fingerprinted_file(path) as (file, second_fingerprint):
            assert file.read() == b"key = 1"

        assert isinstance(first_fingerprint, str)
        assert first_fingerprint
        assert first_fingerprint == second_fingerprint

    def test_keeps_serving_the_opened_version_when_the_path_is_replaced(
        self, tmp_working_directory: Path
    ) -> None:
        """An atomic replace mid-read leaves the open descriptor untouched.

        This used to raise. The bytes were always the ones the fingerprint
        described -- the check compared them against a file the reader had
        never read.
        """
        path = tmp_working_directory / "config.toml"
        replacement = tmp_working_directory / "replacement.toml"
        path.write_text("key = 1")
        replacement.write_text("key = 2")

        with open_fingerprinted_file(path) as (file, fingerprint):
            replacement.replace(path)
            assert file.read() == b"key = 1"

        with open_fingerprinted_file(path) as (file, later_fingerprint):
            assert file.read() == b"key = 2"

        assert fingerprint != later_fingerprint

    def test_keeps_serving_the_opened_version_when_the_file_is_unlinked(
        self, tmp_working_directory: Path
    ) -> None:
        """Also used to raise -- FileNotFoundError, from the re-open."""
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with open_fingerprinted_file(path) as (file, fingerprint):
            path.unlink()
            assert file.read() == b"key = 1"

        assert fingerprint

    def test_raises_when_file_is_missing(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "missing.toml"

        with pytest.raises(FileNotFoundError):
            with open_fingerprinted_file(path):
                pass


class TestCreateFileFingerprint:
    def test_captures_file_state(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")

        with path.open("rb") as file:
            first_fingerprint = create_file_fingerprint(file)
        with path.open("rb") as file:
            second_fingerprint = create_file_fingerprint(file)

        assert isinstance(first_fingerprint, str)
        assert first_fingerprint
        assert first_fingerprint == second_fingerprint

    def test_changes_when_file_changes(self, tmp_working_directory: Path) -> None:
        path = tmp_working_directory / "config.toml"
        path.write_text("key = 1")
        with path.open("rb") as file:
            first_fingerprint = create_file_fingerprint(file)

        path.write_text("key = 2")

        with path.open("rb") as file:
            assert create_file_fingerprint(file) != first_fingerprint


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


class TestSnapshotReadUnderConcurrentWrites:
    """A config read must survive another process saving at the same moment.

    A save lands as an atomic rename, so the descriptor a reader already holds
    keeps serving the version it opened. Re-stating the *path* afterwards and
    calling the difference a conflict made an ordinary read fail, which
    surfaced to clients as an internal error that closed the connection -- and
    on CI as whole test suites losing a turn to it.
    """

    def test_a_fingerprint_always_describes_the_data_returned_with_it(
        self, tmp_working_directory: Path
    ) -> None:
        """*Prepare*: A config file a background thread keeps atomically
        replacing between two distinguishable versions.
        *Do*: Read a snapshot repeatedly while that runs.
        *Assert*: No read fails, both versions are observed, and no fingerprint
        is ever handed out with two different payloads.

        That last property is the one the optimistic-concurrency token rests
        on: a patch is accepted only when its fingerprint still matches, so a
        fingerprint that could describe two payloads would let a patch built on
        one of them be applied to the other.
        """
        # Prepare
        target = tmp_working_directory / "config.toml"
        small = RawConfig.model_validate({})
        big = RawConfig.model_validate({
            "tools": {f"tool-{index}": {"permission": "always"} for index in range(40)}
        })
        _write_toml_snapshot(target, small)

        stop = threading.Event()
        writes = 0
        writer_error: list[BaseException] = []

        def keep_replacing() -> None:
            nonlocal writes
            payloads = cycle((big, small))
            try:
                while not stop.is_set():
                    _write_toml_snapshot(target, next(payloads))
                    writes += 1
            except BaseException as error:  # surfaced below; a dead writer
                writer_error.append(error)  # would leave the read uncontended

        writer = threading.Thread(target=keep_replacing, daemon=True)
        writer.start()

        # Do
        try:
            snapshots = [_read_toml_snapshot(target) for _ in range(200)]
        finally:
            stop.set()
            writer.join(timeout=5)

        # Assert
        assert not writer_error, f"the writer died: {writer_error[0]!r}"
        assert not writer.is_alive()
        assert writes > 1, "the writer never contended with the reads"

        payload_by_fingerprint: dict[str, object] = {}
        for snapshot in snapshots:
            previous = payload_by_fingerprint.setdefault(
                snapshot.fingerprint, snapshot.data
            )
            assert previous == snapshot.data, (
                f"fingerprint {snapshot.fingerprint} described two payloads"
            )

        observed = {len(snapshot.data.get("tools") or {}) for snapshot in snapshots}
        assert observed == {0, 40}, (
            f"expected to read both versions while writing, saw {observed}"
        )

    def test_a_read_survives_the_file_being_replaced_mid_parse(
        self, tmp_working_directory: Path
    ) -> None:
        """The exact interleaving that used to raise: replaced after the open.

        The reader holds a descriptor on the version it opened, so it must
        still return that version's data rather than fail.
        """
        target = tmp_working_directory / "config.toml"
        replacement = tmp_working_directory / "replacement.toml"
        _write_toml_snapshot(target, RawConfig.model_validate({}))
        _write_toml_snapshot(
            replacement,
            RawConfig.model_validate({"tools": {"t": {"permission": "always"}}}),
        )

        original = _read_toml_snapshot(target)
        replacement.replace(target)
        after = _read_toml_snapshot(target)

        assert not original.data.get("tools")
        assert "t" in (after.data.get("tools") or {})
        assert original.fingerprint != after.fingerprint
