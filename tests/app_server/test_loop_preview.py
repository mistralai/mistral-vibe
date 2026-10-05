from __future__ import annotations

from pathlib import Path
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    _mistral_sse_payload,
    connect_backend_contract_host,
)
from tests.constants import (
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
)
from vibe.app_server._loop_prompt import LOOP_INSTRUCTIONS, project_loop_preview
from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.protocol import (
    ClientCapabilities,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
)


@pytest.mark.parametrize(
    "preview",
    [
        "",
        "/loop",
        "/loop explain <loop_instructions>",
        "/loop explain <loop_instructions> literally",
        "/loop explain <loop_instructions> literally…",
        "/loop explain <loop_other…",
        "/loop   keep\ttabs and  spaces",
        "/loop " + "x" * 194 + "…",
        "Explain <loop_instructions> to me",
        " ".join(LOOP_INSTRUCTIONS.splitlines()),
    ],
)
def test_preview_projection_preserves_literal_input(preview: str) -> None:
    assert project_loop_preview(preview) == preview


@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize(
    "prompt", ["/loop", "/loop explain <loop_instructions> literally"]
)
def test_preview_projection_removes_only_appended_instructions(
    prompt: str, truncated: bool
) -> None:
    preview = prompt + " " + " ".join(LOOP_INSTRUCTIONS.splitlines())
    if truncated:
        preview = preview[:200].rstrip() + "…"
    projected = project_loop_preview(preview)
    assert projected == prompt
    assert project_loop_preview(projected) == projected


@pytest.mark.parametrize("marker_chars", range(1, len("<loop_instructions>") + 1))
def test_preview_truncated_inside_opening_marker(marker_chars: int) -> None:
    prompt = "/loop " + "x" * (193 - marker_chars)
    preview = (prompt + " " + LOOP_INSTRUCTIONS.replace("\n", " "))[:200] + "…"
    assert project_loop_preview(preview) == prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "/loop",
        "/LOOP every two minutes check CI",
        "/loop   every\tminute\ncheck CI",
        "/loop " + "x" * 180,
        "/loop explain <loop_instructions> literally",
    ],
)
async def test_loop_preview_is_clean_live_stored_listed_and_resumed(
    config_dir: Path, tmp_path: Path, respx_mock: respx.MockRouter, prompt: str
) -> None:
    config_path = config_dir / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config["session_logging"] = {
        "enabled": True,
        "save_dir": str(tmp_path / "sessions"),
    }
    config_path.write_text(tomli_w.dumps(config), encoding="utf-8")
    respx_mock.get(f"{MISTRAL_BASE_URL}/v1/users/me").respond(401)
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").respond(
        200, json={"connectors": []}
    )
    respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
        return_value=httpx.Response(
            200,
            stream=httpx.ByteStream(_mistral_sse_payload("Understood.", None)),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    connection = await connect_backend_contract_host(
        True, session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    session = await connection.host.open_session()
    try:
        _ = [event async for event in session.act(prompt)]
        session_id = session.session_id
        live = session.state
        read = SessionReadResponse.model_validate(
            await connection.client.request(
                "session/read", SessionReadParams(session_id=session_id)
            )
        ).state
    finally:
        await session.close()

    passive = await connect_backend_contract_host(
        True, session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    try:
        listed = await passive.host.list_sessions()
        stored = await passive.host.read_session(session_id)
        resumed = await passive.host.resume_session(session_id)
        try:
            resumed_state = resumed.state
        finally:
            await resumed.close()
    finally:
        await passive.host.close()

    expected_preview = prompt.strip().replace("\n", " ")
    assert (
        next(item for item in listed if item.id == session_id).preview
        == expected_preview
    )
    for state in (live, read, stored, resumed_state):
        assert state.session.preview == expected_preview
        assert state.history is not None
        user = next(
            entry
            for entry in state.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        )
        assert user.text == prompt
