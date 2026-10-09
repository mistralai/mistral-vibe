from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any

import pytest

from vibe.app_server.run_export import (
    RunExport,
    RunLimits,
    RunOutcome,
    RunWarning,
    copy_journal,
    write_run_export,
)
from vibe.core.session.session_index import METADATA_FILENAME


@pytest.mark.parametrize(
    ("outcome", "exit_code"),
    [
        (RunOutcome.FINISHED, 0),
        (RunOutcome.USAGE_ERROR, 1),
        (RunOutcome.CONFIG_ERROR, 1),
        (RunOutcome.INFRASTRUCTURE_FAILURE, 2),
        (RunOutcome.TURN_LIMIT, 3),
        (RunOutcome.TOKEN_LIMIT, 3),
        (RunOutcome.PRICE_LIMIT, 3),
        (RunOutcome.DEADLINE, 3),
        (RunOutcome.TERMINATED, 3),
        (RunOutcome.LENGTH, 3),
        (RunOutcome.REFUSAL, 3),
        (RunOutcome.ABORTED, 4),
    ],
)
def test_each_outcome_has_its_documented_exit_code(
    outcome: RunOutcome, exit_code: int
) -> None:
    assert outcome.exit_code == exit_code


def _export(warnings: list[RunWarning] | None) -> RunExport:
    now = datetime.now(UTC)
    return RunExport(
        outcome=RunOutcome.FINISHED,
        warnings=warnings,
        vibe_version="0",
        started_at=now,
        ended_at=now,
        limits=RunLimits(),
    )


@pytest.mark.parametrize("warnings", [None, []])
def test_an_export_without_warnings_has_no_warnings_field(
    tmp_path: Path, warnings: list[RunWarning] | None
) -> None:
    path = write_run_export(tmp_path, _export(warnings))

    assert "warnings" not in json.loads(path.read_text(encoding="utf-8"))


def test_an_exports_warnings_are_written_and_read_back(tmp_path: Path) -> None:
    warnings = [
        RunWarning(message="Skill left out", source="/skills/huge/SKILL.md"),
        RunWarning(message="Something else"),
    ]

    path = write_run_export(tmp_path, _export(warnings))

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["warnings"] == [
        {"message": "Skill left out", "source": "/skills/huge/SKILL.md"},
        {"message": "Something else", "source": None},
    ]
    assert RunExport.model_validate(written).warnings == warnings


def test_copied_journal_has_its_config_redacted(tmp_path: Path) -> None:
    """*Prepare*: A session directory whose metadata holds a config with
    secrets, beside a message log.
    *Do*: Copy the journal into an output directory.
    *Assert*: The copy's config is redacted, the rest is copied as is, and the
    session directory itself is untouched.
    """
    # Prepare
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    metadata = {
        "session_id": "s",
        "config": {
            "providers": [{"api_base": "https://user:secret@example.invalid/v1"}],
            "mcp_servers": [{"args": ["--token", "secret"], "env": {"K": "secret"}}],
        },
    }
    (session_dir / METADATA_FILENAME).write_text(json.dumps(metadata))
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    output_dir = tmp_path / "out"

    # Do
    journal = copy_journal(session_dir, output_dir)

    # Assert
    copied = output_dir / journal
    copied_metadata = json.loads((copied / METADATA_FILENAME).read_text())
    assert "secret" not in json.dumps(copied_metadata)
    assert copied_metadata["session_id"] == "s"
    assert copied_metadata["config"]["providers"][0]["api_base"] == (
        "https://<redacted>@example.invalid/v1"
    )
    assert copied_metadata["config"]["mcp_servers"][0] == {
        "args": ["<redacted>", "<redacted>"],
        "env": {"K": "<redacted>"},
    }
    assert (copied / "messages.jsonl").read_text() == '{"role": "user"}\n'
    assert json.loads((session_dir / METADATA_FILENAME).read_text()) == metadata


def test_copied_journal_drops_metadata_it_cannot_read(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / METADATA_FILENAME).write_text('{"config": {"env": ')

    journal = copy_journal(session_dir, tmp_path / "out")

    assert not (tmp_path / "out" / journal / METADATA_FILENAME).exists()


def test_copy_journal_stages_then_replaces(tmp_path: Path) -> None:
    """*Prepare*: A session directory and an output directory with an old journal.
    *Do*: Copy the journal.
    *Assert*: The copy is staged in .session.tmp first, then atomically
    replaces the old journal. The staging directory is gone afterwards.
    """
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    old_journal = output_dir / "session"
    old_journal.mkdir()
    (old_journal / "old.txt").write_text("old data")

    journal = copy_journal(session_dir, output_dir)

    assert journal == "session"
    assert (
        output_dir / "session" / "messages.jsonl"
    ).read_text() == '{"role": "user"}\n'
    assert not (output_dir / ".session.tmp").exists()


def test_copy_journal_preserves_old_data_on_failure(tmp_path: Path) -> None:
    """*Prepare*: A session directory that will fail to copy (it does not exist),
    and an output directory with an old journal.
    *Do*: Attempt to copy the journal.
    *Assert*: The old journal data survives the failed copy.
    """
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    old_journal = output_dir / "session"
    old_journal.mkdir()
    (old_journal / "old.txt").write_text("old data")

    with pytest.raises(FileNotFoundError):
        copy_journal(tmp_path / "nonexistent", output_dir)

    assert (output_dir / "session" / "old.txt").read_text() == "old data"


def _output_dir_with_old_journal(tmp_path: Path) -> Path:
    output_dir = tmp_path / "out"
    old_journal = output_dir / "session"
    old_journal.mkdir(parents=True)
    (old_journal / "old.txt").write_text("old data")
    return output_dir


def _new_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    return session_dir


def test_a_failed_journal_swap_keeps_the_old_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An output directory with an old journal, and a rename of the
    staged copy into place that fails.
    *Do*: Copy a new journal.
    *Assert*: The copy raises, the old journal is still in place, and neither
    the staged copy nor the old journal is left aside.
    """
    # Prepare
    output_dir = _output_dir_with_old_journal(tmp_path)
    session_dir = _new_session_dir(tmp_path)
    real_replace = os.replace

    def replace_failing_on_the_staged_copy(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        if Path(source).name == ".session.tmp":
            raise OSError("Access is denied")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace_failing_on_the_staged_copy)

    # Do
    with pytest.raises(OSError, match="Access is denied"):
        copy_journal(session_dir, output_dir)

    # Assert
    assert (output_dir / "session" / "old.txt").read_text() == "old data"
    assert sorted(path.name for path in output_dir.iterdir()) == ["session"]


def test_an_old_journal_left_aside_by_a_crashed_copy_is_removed(tmp_path: Path) -> None:
    """*Prepare*: An output directory with an old journal and, beside it, the
    journal a crashed earlier copy left aside.
    *Do*: Copy a new journal.
    *Assert*: The new journal is in place and nothing is left aside.
    """
    # Prepare
    output_dir = _output_dir_with_old_journal(tmp_path)
    (output_dir / ".session.old").mkdir()
    (output_dir / ".session.old" / "older.txt").write_text("older data")
    session_dir = _new_session_dir(tmp_path)

    # Do
    copy_journal(session_dir, output_dir)

    # Assert
    assert (output_dir / "session" / "messages.jsonl").is_file()
    assert not (output_dir / "session" / "old.txt").exists()
    assert sorted(path.name for path in output_dir.iterdir()) == ["session"]


def test_a_journal_a_crashed_copy_left_aside_alone_is_put_back(tmp_path: Path) -> None:
    """*Prepare*: An output directory holding only the journal a copy crashed
    after setting aside, and a new copy that fails.
    *Do*: Copy a journal from a session directory that does not exist.
    *Assert*: The journal set aside is back in place.
    """
    # Prepare
    output_dir = tmp_path / "out"
    (output_dir / ".session.old").mkdir(parents=True)
    (output_dir / ".session.old" / "old.txt").write_text("old data")

    # Do
    with pytest.raises(FileNotFoundError):
        copy_journal(tmp_path / "nonexistent", output_dir)

    # Assert
    assert (output_dir / "session" / "old.txt").read_text() == "old data"
    assert sorted(path.name for path in output_dir.iterdir()) == ["session"]


def _secret_metadata() -> dict[str, Any]:
    return {
        "session_id": "s",
        "config": {"providers": [{"api_base": "https://user:secret@example.invalid"}]},
    }


def _files_holding_the_secret(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and "secret" in path.read_text(errors="replace")
    ]


def test_a_failed_journal_copy_leaves_no_unredacted_config_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session directory with a config holding a secret, and a
    copy that fails on one file after copying the others, as ``copytree`` does.
    *Do*: Copy the journal.
    *Assert*: The copy raises, and nothing in the output directory holds the
    secret.
    """
    # Prepare
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / METADATA_FILENAME).write_text(json.dumps(_secret_metadata()))
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    real_copytree = shutil.copytree

    def copy_failing_on_messages(source: str, target: str) -> object:
        if Path(source).name == "messages.jsonl":
            raise OSError("No space left on device")
        return shutil.copy2(source, target)

    def failing_copytree(source: Path, target: Path, **options: Any) -> object:
        return real_copytree(
            source, target, copy_function=copy_failing_on_messages, **options
        )

    monkeypatch.setattr(shutil, "copytree", failing_copytree)

    # Do
    with pytest.raises(OSError, match="No space left on device"):
        copy_journal(session_dir, output_dir)

    # Assert
    assert _files_holding_the_secret(output_dir) == []
    assert list(output_dir.iterdir()) == []


def test_a_half_written_metadata_file_is_not_copied(tmp_path: Path) -> None:
    """*Prepare*: A session directory holding, beside its metadata, the temp
    file a killed metadata write left behind with the full config.
    *Do*: Copy the journal.
    *Assert*: The temp file is not copied, so the secret is nowhere in the
    output directory.
    """
    # Prepare
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / METADATA_FILENAME).write_text(json.dumps(_secret_metadata()))
    (session_dir / "tmpk3x9q1.json.tmp").write_text(json.dumps(_secret_metadata()))
    (session_dir / "tmpw81mz0.jsonl.tmp").write_text('{"role": "user"}\n')
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    output_dir = tmp_path / "out"

    # Do
    journal = copy_journal(session_dir, output_dir)

    # Assert
    copied = output_dir / journal
    assert {path.name for path in copied.iterdir()} == {
        METADATA_FILENAME,
        "messages.jsonl",
    }
    assert _files_holding_the_secret(output_dir) == []


def test_dropping_unreadable_metadata_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A session directory whose metadata is not valid JSON.
    *Do*: Copy the journal.
    *Assert*: The copy drops the metadata and says so in a warning.
    """
    # Prepare
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / METADATA_FILENAME).write_text('{"config": {"env": ')

    # Do
    with caplog.at_level(logging.WARNING):
        journal = copy_journal(session_dir, tmp_path / "out")

    # Assert
    assert not (tmp_path / "out" / journal / METADATA_FILENAME).exists()
    assert any(
        record.levelno == logging.WARNING and METADATA_FILENAME in record.getMessage()
        for record in caplog.records
    )
