from __future__ import annotations

from pathlib import Path
import time
from typing import cast

import pytest

from vibe.core.tools.builtins import experimental_bash
from vibe.core.tools.builtins.experimental_bash import (
    ManagedTerminal,
    TerminalSession,
    TerminalSessionManager,
)


@pytest.fixture
def small_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    cap = 4096
    # raising=False so the assertions below fail on the unbounded behaviour
    # rather than erroring on a missing symbol.
    monkeypatch.setattr(experimental_bash, "DEFAULT_MAX_LOG_BYTES", cap, raising=False)
    return cap


def _session(tmp_path: Path) -> TerminalSession:
    output_path = tmp_path / "session.log"
    output_path.touch()
    return TerminalSession(
        session_id="s1",
        command="yes",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=cast(ManagedTerminal, object()),
        output_path=output_path,
        manifest_path=tmp_path / "session.json",
        created_at=time.time(),
    )


def test_session_log_stops_growing_once_it_passes_the_cap(
    tmp_path: Path, small_cap: int
) -> None:
    manager = TerminalSessionManager()
    session = _session(tmp_path)

    for _ in range(200):
        manager._append_output(session, b"x" * 1024)

    assert session.output_path.stat().st_size <= small_cap


def test_trimmed_log_keeps_the_most_recent_output(
    tmp_path: Path, small_cap: int
) -> None:
    manager = TerminalSessionManager()
    session = _session(tmp_path)

    manager._append_output(session, b"a" * (small_cap * 2))
    manager._append_output(session, b"newest-output\n")

    contents = session.output_path.read_bytes()
    assert contents.endswith(b"newest-output\n")
    assert b"earlier output dropped" in contents


def test_trimmed_log_stays_decodable_when_a_cut_splits_a_character(
    tmp_path: Path, small_cap: int
) -> None:
    manager = TerminalSessionManager()
    session = _session(tmp_path)

    # Three-byte characters guarantee some cut lands mid-sequence.
    manager._append_output(session, "字".encode() * 2048)

    session.output_path.read_bytes().decode()


def test_log_under_the_cap_is_left_alone(tmp_path: Path, small_cap: int) -> None:
    manager = TerminalSessionManager()
    session = _session(tmp_path)

    manager._append_output(session, b"short output\n")

    assert session.output_path.read_bytes() == b"short output\n"
