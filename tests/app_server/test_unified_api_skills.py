"""A skill whose body quotes skill markup shows as one skill effect in a Session API read."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionFinishReason,
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
    RustUserMessage,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionReadParams as ApiSessionReadParams,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from tests.app_server._session_api_harness import (
    ApiAdapter,
    api_adapter,
    api_transport,
    forwarding,
    until,
)
from vibe.app_server._effect_models import SkillEffectDetail
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendEvent
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import (
    CompletedEffectState,
    PublicEffectEntry,
    PublicHistoryEntry,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    PageRequest,
    SessionReadParams,
    SessionReadResponse,
    TurnStartParams,
)
from vibe.core.tools.builtins.skill import skill_content_marker
from vibe.user_content import UserDisplayContent

USAGE = RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)


@dataclass
class _Model:
    """A fake model that records each request's messages and answers "ok".

    With ``tool_call`` set, the first request calls that tool instead. With
    ``hold_from`` set, that request and the later ones wait for ``release``.
    """

    tool_call: tuple[str, dict[str, str]] | None = None
    hold_from: int | None = None
    release: asyncio.Event = field(default_factory=asyncio.Event)
    requests: list[list[Any]] = field(default_factory=list)

    async def __call__(
        self, action: Any, messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        self.requests.append(list(messages))
        if self.hold_from is not None and len(self.requests) >= self.hold_from:
            await self.release.wait()
        parts: list[RustCompletionResultPart] = [RustTextContentBlock(text="ok")]
        finish_reason: RustCompletionFinishReason = "stop"
        if self.tool_call is not None and len(self.requests) == 1:
            name, arguments = self.tool_call
            parts = [
                RustCompletionResultToolCallPart(
                    id="0123456789abcdef",
                    name=name,
                    arguments_json=json.dumps(arguments),
                )
            ]
            finish_reason = "tool_call"
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts, finish_reason=finish_reason, usage=USAGE
            ),
        )

    def sent_user_texts(self) -> list[str]:
        """The text blocks of the user message the last request ended with."""
        last = self.requests[-1][-1]
        assert isinstance(last, RustUserMessage)
        return [
            block.text
            for block in last.content
            if isinstance(block, RustTextContentBlock)
        ]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _Model:
    fake = _Model()
    monkeypatch.setattr(_local_actions, "execute_completion", fake)
    return fake


def _add_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bodies: dict[str, str]
) -> None:
    """Write one user-invocable ``SKILL.md`` per name, in a ``skill_paths`` folder.

    The shared harness loads no project files, so a skill in the workspace's
    ``.vibe/skills`` is found only when ``skill_paths`` lists its folder.
    """
    root = tmp_path / "skills"
    for name, body in bodies.items():
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            f"---\nname: {name}\ndescription: Reviews a diff.\n---\n\n{body}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("VIBE_SKILL_PATHS", json.dumps([str(root)]))


def _count(forwarded: list[SessionBackendEvent], event_type: type) -> int:
    return sum(isinstance(e.event, event_type) for e in forwarded)


async def _start_turn(
    harness: ApiAdapter, *texts: str, display: UserDisplayContent | None = None
) -> None:
    result = await harness.adapter.start_turn(
        TurnStartParams(
            session_id=harness.adapter.session_id,
            message=[TextContentBlock(text=text) for text in texts],
            user_display_content=display,
        )
    )
    # The app server calls this once it has replied. A Turn that carries
    # display content, as every skill invocation does, only runs after it.
    if result.after_response is not None:
        result.after_response()


async def _run_turn(
    harness: ApiAdapter,
    forwarded: list[SessionBackendEvent],
    *texts: str,
    display: UserDisplayContent | None = None,
) -> None:
    completed = _count(forwarded, TurnCompleted)
    await _start_turn(harness, *texts, display=display)
    await until(lambda: _count(forwarded, TurnCompleted) > completed)


async def _read(harness: ApiAdapter, limit: int = 10) -> SessionReadResponse:
    return await harness.adapter.read(
        SessionReadParams(
            session_id=harness.adapter.session_id, history=PageRequest(limit=limit)
        )
    )


def _history(response: SessionReadResponse) -> list[PublicHistoryEntry]:
    assert response.state.history is not None
    return response.state.history


def _user_entries(history: list[PublicHistoryEntry]) -> list[PublicHistoryEntry]:
    """The user message and every entry after it: its skill effects and replies."""
    first = next(
        index
        for index, entry in enumerate(history)
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    )
    return history[first:]


def _loaded_skill(entry: PublicHistoryEntry) -> str:
    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.detail, SkillEffectDetail)
    assert entry.detail.input is not None
    return entry.detail.input.name


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_a_read_shows_a_skill_body_holding_skill_markup_as_one_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: _Model,
    truncated: bool,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A real adapter on the Session API path over a ``code-review``
        skill whose body quotes ``</skill_content>`` and a whole nested
        rendered skill. With ``truncated``, the body is padded so the
        Runtime's 200-character preview ends inside the nested skill, right
        after its marker.
    *Do*: Run a ``/code-review`` Turn, then read.
    *Assert*: The nested markup does not end the outer payload early: the
        message is the bare prompt, the preview is the bare prompt, and the
        skill effect holds the nested skill.
    """
    # Prepare
    nested = (
        f"{skill_content_marker('code-review')}\n"
        "# Skill: code-review\n\nexample\n</skill_content>"
    )
    quoted = f"Document `</skill_content>` and this: {nested}"
    cut_inside = f"{skill_content_marker('code-review')} # Skill: code-rev"
    head = f"/code-review {skill_content_marker('code-review')} # Skill: code-review  "
    padding = 200 - len(head) - len(quoted.split(nested)[0]) - len(cut_inside)
    body = "x" * padding + quoted if truncated else quoted
    _add_skills(tmp_path, monkeypatch, {"code-review": body})
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        # Do
        await _run_turn(harness, forwarded, "/code-review")
        response = await _read(harness)
        runtime, _extensions = await api_transport(
            harness.adapter
        ).read_with_extensions(
            ApiSessionReadParams(session_id=harness.adapter.session_id)
        )

    # Assert
    runtime_preview = runtime.state.session.preview or ""
    assert runtime_preview.endswith(f"{cut_inside}…") is truncated
    assert response.state.session.preview == "/code-review"
    message, effect, _reply = _user_entries(_history(response))
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review"
    assert _loaded_skill(effect) == "code-review"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.state, CompletedEffectState)
    assert nested in effect.state.output_text
