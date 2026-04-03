from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from tests.e2e.common import SpawnedVibeProcessFixture, ansi_tolerant_pattern


@pytest.mark.timeout(15)
def test_spawn_cli_shows_onboarding_when_api_key_missing(
    tmp_path: Path,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vibe_home = tmp_path / "vibe-home-onboarding"
    vibe_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("VIBE_HOME", str(vibe_home))
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        child.expect(ansi_tolerant_pattern("Welcome to Mistral Vibe"), timeout=15)
        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=10)

    output = captured.getvalue()
    assert "Setup cancelled" in output
