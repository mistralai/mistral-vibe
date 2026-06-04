from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from vibe.cli import entrypoint as entrypoint_mod
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.setup.trusted_folders.trust_folder_dialog import TrustDecision


def _make_git_repo(path: Path) -> None:
    git_dir = path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def _git_sub_with_agents(
    tmp_path: Path, *, agents_at_sub: bool = True
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    sub = repo / "src"
    sub.mkdir()
    if agents_at_sub:
        (sub / "AGENTS.md").write_text("# Agents", encoding="utf-8")
    else:
        (repo / "AGENTS.md").write_text("# Agents", encoding="utf-8")
    return repo, sub


def _patch_ask(
    monkeypatch: pytest.MonkeyPatch, *, decision: TrustDecision = TrustDecision.DECLINE
) -> dict[str, object]:
    captured: dict[str, object] = {"called": False}

    def fake_ask(
        cwd: Path,
        repo_root: Path | None,
        detected_files: list[str],
        repo_detected_files: list[str] | None = None,
        offer_repo_trust: bool = False,
        repo_explicitly_untrusted: bool = False,
    ) -> TrustDecision:
        captured["called"] = True
        captured["cwd"] = cwd
        captured["repo_root"] = repo_root
        captured["detected_files"] = detected_files
        captured["repo_detected_files"] = repo_detected_files
        captured["offer_repo_trust"] = offer_repo_trust
        captured["repo_explicitly_untrusted"] = repo_explicitly_untrusted
        return decision

    monkeypatch.setattr(entrypoint_mod, "ask_trust_folder", fake_ask)
    return captured


@pytest.fixture
def away_from_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "elsewhere"))


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param("repo_already_trusted", id="repo_already_trusted"),
        pytest.param("cwd_explicitly_untrusted", id="cwd_explicitly_untrusted"),
    ],
)
def test_skips_trust_dialog(
    setup: Literal["repo_already_trusted", "cwd_explicitly_untrusted"],
    tmp_path: Path,
    away_from_home: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match setup:
        case "repo_already_trusted":
            repo, cwd = _git_sub_with_agents(tmp_path)
            trusted_folders_manager.add_trusted(repo)
        case "cwd_explicitly_untrusted":
            cwd = tmp_path / "plain"
            cwd.mkdir()
            (cwd / "AGENTS.md").write_text("# Agents", encoding="utf-8")
            trusted_folders_manager.add_untrusted(cwd)

    captured = _patch_ask(monkeypatch)
    entrypoint_mod.check_and_resolve_trusted_folder(cwd)
    assert captured["called"] is False


def test_shows_trust_dialog_when_only_repo_context_files_exist(
    tmp_path: Path, away_from_home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, cwd = _git_sub_with_agents(tmp_path, agents_at_sub=False)
    captured = _patch_ask(monkeypatch)
    entrypoint_mod.check_and_resolve_trusted_folder(cwd)

    assert captured["called"] is True
    assert captured["detected_files"] == []
    assert captured["repo_detected_files"] == ["AGENTS.md"]
    assert captured["repo_root"] == repo.resolve()


@pytest.mark.parametrize(
    "untrusted_at,offer_repo_trust,repo_explicitly_untrusted",
    [
        pytest.param(None, True, False, id="repo_undecided"),
        pytest.param("repo", False, True, id="repo_explicitly_untrusted"),
        pytest.param("parent", True, False, id="parent_untrusted_only"),
    ],
)
def test_ask_trust_folder_args_in_git_repo(
    untrusted_at: str | None,
    offer_repo_trust: bool,
    repo_explicitly_untrusted: bool,
    tmp_path: Path,
    away_from_home: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if untrusted_at == "parent":
        parent = tmp_path / "parent"
        parent.mkdir()
        repo = parent / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        cwd = repo / "src"
        cwd.mkdir()
        (cwd / "AGENTS.md").write_text("# Agents", encoding="utf-8")
        trusted_folders_manager.add_untrusted(parent)
    else:
        repo, cwd = _git_sub_with_agents(tmp_path)
        if untrusted_at == "repo":
            trusted_folders_manager.add_untrusted(repo)

    captured = _patch_ask(monkeypatch)
    entrypoint_mod.check_and_resolve_trusted_folder(cwd)

    assert captured["called"] is True
    assert captured["cwd"] == cwd
    assert captured["repo_root"] == repo.resolve()
    assert captured["detected_files"] == ["AGENTS.md"]
    assert captured["repo_detected_files"] == []
    assert captured["offer_repo_trust"] is offer_repo_trust
    assert captured["repo_explicitly_untrusted"] is repo_explicitly_untrusted


@pytest.mark.parametrize(
    ("decision", "sub_trusted", "repo_trusted", "sub_explicitly_untrusted"),
    [
        pytest.param(TrustDecision.TRUST_REPO, True, True, False, id="trust_repo"),
        pytest.param(TrustDecision.TRUST_CWD, True, None, False, id="trust_cwd"),
        pytest.param(TrustDecision.DECLINE, False, None, True, id="decline"),
    ],
)
def test_applies_trust_decision(
    decision: TrustDecision,
    sub_trusted: bool | None,
    repo_trusted: bool | None,
    sub_explicitly_untrusted: bool,
    tmp_path: Path,
    away_from_home: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sub = _git_sub_with_agents(tmp_path)
    _patch_ask(monkeypatch, decision=decision)
    entrypoint_mod.check_and_resolve_trusted_folder(sub)

    assert trusted_folders_manager.is_trusted(sub) is sub_trusted
    assert trusted_folders_manager.is_trusted(repo) is repo_trusted
    assert (
        trusted_folders_manager.is_explicitly_untrusted(sub) is sub_explicitly_untrusted
    )
    if decision == TrustDecision.TRUST_CWD:
        assert trusted_folders_manager.is_trusted(repo / "other") is None


def test_no_git_repo_offers_no_repo_trust_and_decline_untrusts_cwd(
    tmp_path: Path, away_from_home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "plain"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("# Agents", encoding="utf-8")

    captured = _patch_ask(monkeypatch, decision=TrustDecision.DECLINE)
    entrypoint_mod.check_and_resolve_trusted_folder(cwd)

    assert captured["called"] is True
    assert captured["repo_root"] is None
    assert captured["repo_detected_files"] == []
    assert captured["offer_repo_trust"] is False
    assert trusted_folders_manager.is_explicitly_untrusted(cwd) is True
