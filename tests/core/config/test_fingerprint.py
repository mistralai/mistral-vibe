from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest

from vibe.core.config import fingerprint as fingerprint_module
from vibe.core.config.fingerprint import (
    create_dict_fingerprint,
    create_file_fingerprint,
    open_fingerprinted_file,
)
from vibe.core.config.layer import RawConfig
from vibe.core.config.layers._base import _read_toml_snapshot, _write_toml_snapshot
from vibe.core.config.types import LayerConfigSnapshot

# Every point of a read at which a concurrent save can land, relative to the
# descriptor the fingerprint is taken from. Together they cover the interleaving
# space: the save is invisible to the read, or it is the version the read gets.
SAVE_POINTS = ("before the open", "after the open", "after the fingerprint")


def _save_during_read(
    patch: pytest.MonkeyPatch, target: Path, payload: RawConfig, when: str
) -> None:
    """Arrange for one save of ``target`` to land at ``when`` during a read.

    Placed rather than raced: a writer thread contends only when the machine
    lets it, and this one cannot be starved. See the test for what that cost.
    """
    if when not in SAVE_POINTS:
        raise ValueError(f"unknown save point {when!r}")

    saved = False

    def save_once() -> None:
        nonlocal saved
        if saved:  # exactly one save per read, wherever the read is hooked
            return
        saved = True
        _write_toml_snapshot(target, payload)

    if when == "after the fingerprint":
        take_fingerprint = fingerprint_module.create_file_fingerprint

        def fingerprinting(file: IO[Any]) -> str:
            token = take_fingerprint(file)
            save_once()
            return token

        # Rebinding it here reaches `open_fingerprinted_file` only: `_base`
        # imported the name directly, so its own writes stay unpatched.
        patch.setattr(fingerprint_module, "create_file_fingerprint", fingerprinting)
        return

    open_path = Path.open

    def opening(self: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if self != target:
            return open_path(self, *args, **kwargs)
        if when == "before the open":
            save_once()
        file = open_path(self, *args, **kwargs)
        if when == "after the open":
            save_once()
        return file

    patch.setattr(Path, "open", opening)


def _reads_under_a_save_at_every_point(
    monkeypatch: pytest.MonkeyPatch, target: Path, versions: tuple[RawConfig, RawConfig]
) -> Iterator[LayerConfigSnapshot]:
    """Yield the snapshot of a read meeting a save at each point, both ways round."""
    for when in SAVE_POINTS:
        for before, after in (versions, versions[::-1]):
            _write_toml_snapshot(target, before)
            with monkeypatch.context() as patch:
                _save_during_read(patch, target, after, when)
                yield _read_toml_snapshot(target)


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
        self, tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Prepare*: A config file and two distinguishable versions of it.
        *Do*: Read a snapshot with a save of the other version landing at each
        point of the read where a concurrent save can land, both ways round.
        *Assert*: No read fails, every payload read is one whole version, both
        versions are observed, and no fingerprint is ever handed out with two
        different payloads.

        That last property is the one the optimistic-concurrency token rests
        on: a patch is accepted only when its fingerprint still matches, so a
        fingerprint that could describe two payloads would let a patch built on
        one of them be applied to the other.

        The saves are placed, not raced. This used to run a free-running writer
        thread and assert afterwards that it had managed to contend; whether it
        did was decided by the agent's disk, since a writer iteration costs an
        ``fsync`` while a read is page-cache-hot. Past roughly 10ms of fsync
        latency the reads finished before the writer's second iteration and the
        test failed reporting that its own writer never ran.
        """
        # Prepare
        target = tmp_working_directory / "config.toml"
        small = RawConfig.model_validate({})
        big = RawConfig.model_validate({
            "tools": {f"tool-{index}": {"permission": "always"} for index in range(40)}
        })

        # Do
        snapshots = list(
            _reads_under_a_save_at_every_point(monkeypatch, target, (small, big))
        )

        # Assert
        assert len(snapshots) == len(SAVE_POINTS) * 2

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
            f"every read must return one whole version, saw {observed}"
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
