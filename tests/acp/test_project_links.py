"""ACP-layer projectLinks tests: dispatch, param validation, error mapping.

The projectLinks logic lives in the app-server ProjectLinksController
(tested in tests/app_server/test_project_links.py). Here we only verify the
thin `ext_method` delegation and how controller errors map to ACP errors.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.acp.conftest import _create_acp_agent
import vibe.acp.agent as agent_module
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import InvalidRequestError, NotImplementedMethodError
from vibe.app_server._project_links import ProjectLinksInvalidRequest

ROOT = "/tmp/widgets"


@pytest.fixture
def acp_agent() -> VibeAcpAgent:
    return _create_acp_agent()


class _FakeController:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self._result = {} if result is None else result
        self._error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _out(self, name: str, *args: Any) -> Any:
        self.calls.append((name, args))
        if self._error is not None:
            raise self._error
        return self._result

    async def list_links(self) -> Any:
        return self._out("list_links")

    async def resolve_root(self, root_path: str) -> Any:
        return self._out("resolve_root", root_path)

    async def unlink(self, root_path: str) -> Any:
        return self._out("unlink", root_path)


def _install(
    agent: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch, fake: _FakeController
):
    monkeypatch.setattr(agent_module, "ProjectLinksController", lambda: fake)


@pytest.mark.asyncio
async def test_dispatches_to_controller_and_returns_its_result(
    acp_agent: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeController(result={"projects": []})
    _install(acp_agent, monkeypatch, fake)
    result = await acp_agent.ext_method("projectLinks/list", {})
    assert result == {"projects": []}
    assert fake.calls == [("list_links", ())]


@pytest.mark.asyncio
async def test_forwards_validated_params(
    acp_agent: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeController(result={"unlinked": True})
    _install(acp_agent, monkeypatch, fake)
    await acp_agent.ext_method("projectLinks/unlink", {"rootPath": ROOT})
    assert fake.calls == [("unlink", (ROOT,))]


@pytest.mark.asyncio
async def test_unlink_requires_root_path(acp_agent: VibeAcpAgent) -> None:
    with pytest.raises(InvalidRequestError):
        await acp_agent.ext_method("projectLinks/unlink", {})


@pytest.mark.asyncio
async def test_maps_invalid_request(
    acp_agent: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(
        acp_agent,
        monkeypatch,
        _FakeController(error=ProjectLinksInvalidRequest("nope")),
    )
    with pytest.raises(InvalidRequestError):
        await acp_agent.ext_method("projectLinks/resolveRoot", {"rootPath": ROOT})


@pytest.mark.asyncio
async def test_unknown_project_links_method(acp_agent: VibeAcpAgent) -> None:
    with pytest.raises(NotImplementedMethodError):
        await acp_agent.ext_method("projectLinks/nope", {})


@pytest.mark.parametrize(
    "method",
    [
        "projectLinks/create",
        "projectLinks/link",
        "projectLinks/picker/load",
        "projectLinks/picker/loadMore",
    ],
)
@pytest.mark.asyncio
async def test_cloud_project_methods_are_gone(
    acp_agent: VibeAcpAgent, method: str
) -> None:
    with pytest.raises(NotImplementedMethodError):
        await acp_agent.ext_method(method, {"rootPath": ROOT})
