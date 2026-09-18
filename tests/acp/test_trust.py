from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vibe.acp.exceptions import InvalidRequestError, SessionNotFoundError
from vibe.app_server.protocol import WorkspaceTrustStatusResponse
from vibe.core.trusted_folders import trusted_folders_manager


async def _new_session_in(agent, cwd: Path) -> str:
    response = await agent.new_session(cwd=str(cwd), mcp_servers=[])
    return response.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["trust_repo", "trust_cwd", "decline"])
async def test_trust_decision_without_session_is_rejected(
    acp_agent_loop, tmp_path: Path, decision: str
) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")

    with pytest.raises(InvalidRequestError):
        await acp_agent_loop.ext_method(
            "trust/decision", {"decision": decision, "cwd": str(tmp_path)}
        )

    assert trusted_folders_manager.is_trusted(tmp_path) is not True
    assert trusted_folders_manager.is_explicitly_untrusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_trust_decision_with_unknown_session_is_rejected(
    acp_agent_loop, tmp_path: Path
) -> None:
    with pytest.raises(SessionNotFoundError):
        await acp_agent_loop.ext_method(
            "trust/decision",
            {"sessionId": "missing", "decision": "trust_cwd", "cwd": str(tmp_path)},
        )


@pytest.mark.asyncio
async def test_trust_decision_with_foreign_cwd_is_rejected(
    acp_agent_loop, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "AGENTS.md").write_text("Hostile instructions", encoding="utf-8")

    session_id = await _new_session_in(acp_agent_loop, tmp_path)

    with pytest.raises(InvalidRequestError):
        await acp_agent_loop.ext_method(
            "trust/decision",
            {"sessionId": session_id, "decision": "trust_cwd", "cwd": str(other)},
        )

    assert trusted_folders_manager.is_trusted(other) is not True
    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_trust_decision_with_a_non_string_cwd_is_rejected(
    acp_agent_loop, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")

    session_id = await _new_session_in(acp_agent_loop, tmp_path)

    with pytest.raises(InvalidRequestError):
        await acp_agent_loop.ext_method(
            "trust/decision",
            {"sessionId": session_id, "decision": "trust_cwd", "cwd": 42},
        )

    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_cwd", [False, True])
async def test_trust_decision_for_session_cwd_is_forwarded(
    acp_agent_loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_cwd: bool
) -> None:
    session_id = await _new_session_in(acp_agent_loop, tmp_path)
    session = acp_agent_loop.sessions[session_id]
    decide_trust = AsyncMock(
        return_value=WorkspaceTrustStatusResponse(status="trusted")
    )
    monkeypatch.setattr(
        session.app_server.resources.workspace, "decide_trust", decide_trust
    )

    params: dict = {"sessionId": session_id, "decision": "trust_cwd"}
    forwarded_cwd = None
    if with_cwd:
        forwarded_cwd = str(session.cwd)
        params["cwd"] = forwarded_cwd

    result = await acp_agent_loop.ext_method("trust/decision", params)

    decide_trust.assert_awaited_once_with("trust_cwd", cwd=forwarded_cwd)
    assert result == {"trust_status": "trusted", "details": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_cwd", [False, True])
async def test_trust_repo_from_a_subdirectory_trusts_the_repository_root(
    acp_agent_loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_cwd: bool
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("Repo instructions", encoding="utf-8")
    subdirectory = repo / "package"
    subdirectory.mkdir()

    session_id = await _new_session_in(acp_agent_loop, subdirectory)

    params: dict = {"sessionId": session_id, "decision": "trust_repo"}
    if with_cwd:
        params["cwd"] = str(subdirectory)

    result = await acp_agent_loop.ext_method("trust/decision", params)

    assert result["trust_status"] == "trusted"
    assert trusted_folders_manager.is_trusted(repo) is True
    assert trusted_folders_manager.find_trust_root(subdirectory) == repo


@pytest.mark.asyncio
async def test_trust_decision_accepts_a_symlink_to_the_session_cwd(
    acp_agent_loop, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    session_id = await _new_session_in(acp_agent_loop, tmp_path)

    result = await acp_agent_loop.ext_method(
        "trust/decision",
        {"sessionId": session_id, "decision": "trust_cwd", "cwd": str(alias)},
    )

    assert result["trust_status"] == "trusted"
    assert trusted_folders_manager.is_trusted(tmp_path) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_session", [False, True])
async def test_trust_status_remains_readable_without_a_session(
    acp_agent_loop, tmp_path: Path, with_session: bool
) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
    params: dict = {"cwd": str(tmp_path)}
    if with_session:
        params["sessionId"] = await _new_session_in(acp_agent_loop, tmp_path)

    result = await acp_agent_loop.ext_method("trust/status", params)

    assert result["trust_status"] == "untrusted"
