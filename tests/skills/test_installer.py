from __future__ import annotations

import subprocess
from pathlib import Path

from vibe.core.skills.installer import (
    CLONE_TIMEOUT_SECONDS,
    SkillInstallResult,
    install_skill,
)


def test_install_skill_captures_clone_output(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        observed.update(
            {
                "command": command,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
            }
        )
        repo_dir = Path(command[-1])
        skill_dir = repo_dir / "skills" / "carl"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("name: carl\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = install_skill(
        "https://github.com/propel-gtm/propel-code-skills",
        target_dir=tmp_path / "installed-skills",
    )

    assert result == SkillInstallResult(
        success=True,
        message="Skills installed successfully. Restart the application to use them.",
    )
    assert (tmp_path / "installed-skills" / "carl").is_dir()
    assert observed["check"] is True
    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert observed["timeout"] == CLONE_TIMEOUT_SECONDS
    command = observed["command"]
    assert isinstance(command, list)
    assert command[:3] == [
        "git",
        "clone",
        "https://github.com/propel-gtm/propel-code-skills",
    ]


def test_install_skill_returns_error_when_named_skill_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        repo_dir = Path(command[-1])
        skill_dir = repo_dir / "skills" / "other-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("name: other-skill\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = install_skill(
        "https://github.com/propel-gtm/propel-code-skills",
        skill_name="carl",
        target_dir=tmp_path / "installed-skills",
    )

    assert result == SkillInstallResult(
        success=False,
        message="Skill 'carl' was not found in the repository.",
    )


def test_install_skill_returns_clone_error_without_terminal_output(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            128,
            command,
            stderr="fatal: repository not found",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = install_skill(
        "https://github.com/propel-gtm/missing-skills",
        target_dir=tmp_path / "installed-skills",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert result == SkillInstallResult(
        success=False,
        message="Git clone failed: fatal: repository not found",
    )
