"""Tests for the `/context` breakdown in `vibe.app_server._context_breakdown`."""

from __future__ import annotations

import json
from pathlib import Path

from mistralai_vibe_local_harness.protocol import (
    RustAssistantMessage,
    RustImageContentBlock,
    RustJsonToolArguments,
    RustModelToolCallPart,
    RustSystemMessage,
    RustTextContentBlock,
    RustToolDefinition,
    RustToolMessage,
    RustUserMessage,
)
from mistralai_vibe_local_harness.vibe import ModelRequest
from vibe.app_server._context_breakdown import (
    ModelRequestLog,
    PayloadSizes,
    build_context_breakdown,
    payload_sizes,
)
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicSession,
    PublicSessionState,
    TokenUsage,
)
from vibe.app_server.protocol import ContextCategoryId
from vibe.core.tools.builtins.skill import skill_content_marker

_SKILL_BODY = (
    f"{skill_content_marker('review')}\n# Skill: review\nbody\n</skill_content>"
)


def _text(text: str) -> RustTextContentBlock:
    return RustTextContentBlock(text=text)


def _state(usage: TokenUsage | None) -> PublicSessionState:
    return PublicSessionState(
        event_id=0,
        session=PublicSession(
            id="session-1",
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
            context_usage=usage,
        ),
        history=[],
    )


def test_payload_sizes_split_a_model_input_by_category() -> None:
    tool = RustToolDefinition(
        name="read_file",
        description="Read a file.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    arguments = RustJsonToolArguments(raw='{"name":"review"}', value={"name": "review"})
    sizes = payload_sizes(
        [
            RustSystemMessage(content=[_text("system prompt")]),
            RustUserMessage(content=[_text("/review this"), _text(_SKILL_BODY)]),
            RustAssistantMessage(
                content=[
                    _text("loading"),
                    RustModelToolCallPart(id="c1", name="skill", arguments=arguments),
                ]
            ),
            RustToolMessage(
                tool_call_id="c1",
                name="skill",
                outcome="success",
                content=[_text("loaded skill body")],
            ),
            RustToolMessage(
                tool_call_id="c2",
                name="read_file",
                outcome="success",
                content=[
                    _text("file text"),
                    RustImageContentBlock(data="AAAA", mime_type="image/png"),
                ],
            ),
        ],
        [tool],
    )

    assert sizes == PayloadSizes(
        system=len("system prompt"),
        tools=len("read_file")
        + len("Read a file.")
        + len(json.dumps(tool.parameters, separators=(",", ":"))),
        skills=len(_SKILL_BODY) + len("loaded skill body"),
        # Images are not text: they cost tokens the character share cannot see.
        conversation=len("/review this")
        + len("loading")
        + len("skill")
        + len(arguments.raw)
        + len("file text"),
    )


def test_unmeasured_session_has_no_estimates() -> None:
    breakdown = build_context_breakdown(
        _state(None),
        payload=PayloadSizes(system=10, conversation=10),
        context_window=200_000,
        compaction_threshold=150_000,
    )

    assert breakdown.measured is False
    assert breakdown.used_tokens == 0
    assert all(category.estimated_tokens == 0 for category in breakdown.categories)


def test_input_tokens_split_by_share_and_output_joins_the_conversation() -> None:
    breakdown = build_context_breakdown(
        _state(TokenUsage(input_tokens=1_000, output_tokens=50)),
        payload=PayloadSizes(system=400, tools=300, skills=100, conversation=200),
        context_window=200_000,
        compaction_threshold=150_000,
    )

    tokens = {c.id: c.estimated_tokens for c in breakdown.categories}
    assert breakdown.used_tokens == 1_050
    assert tokens == {
        ContextCategoryId.SYSTEM: 400,
        ContextCategoryId.TOOLS: 300,
        ContextCategoryId.SKILLS: 100,
        ContextCategoryId.CONVERSATION: 250,
    }
    assert breakdown.total_characters == 1_000


def test_estimates_sum_exactly_to_the_measured_total() -> None:
    breakdown = build_context_breakdown(
        _state(TokenUsage(input_tokens=7, output_tokens=2)),
        payload=PayloadSizes(system=3, tools=3, skills=3, conversation=3),
        context_window=100,
        compaction_threshold=90,
    )

    assert sum(c.estimated_tokens for c in breakdown.categories) == 9


def test_an_unseen_model_input_reports_the_total_without_a_split() -> None:
    breakdown = build_context_breakdown(
        _state(TokenUsage(input_tokens=1_000, output_tokens=0)),
        payload=None,
        context_window=200_000,
        compaction_threshold=150_000,
    )

    assert breakdown.measured is True
    assert breakdown.used_tokens == 1_000
    assert breakdown.categories == []


def test_a_reading_zeroed_by_compaction_drops_the_replaced_split() -> None:
    # Compaction zeroes the usage without a new model input: the newest input
    # (in memory or saved for a resume) describes the context it replaced.
    breakdown = build_context_breakdown(
        _state(TokenUsage(input_tokens=0, output_tokens=0)),
        payload=PayloadSizes(system=400, tools=300, skills=100, conversation=200),
        context_window=200_000,
        compaction_threshold=150_000,
    )

    assert breakdown.measured is True
    assert breakdown.used_tokens == 0
    assert breakdown.categories == []
    assert breakdown.total_characters == 0


def test_threshold_is_clamped_to_the_window() -> None:
    breakdown = build_context_breakdown(
        _state(None), payload=None, context_window=100, compaction_threshold=500
    )

    assert breakdown.compaction_threshold == 100


def _system_request(turn_id: str, text: str) -> ModelRequest:
    return ModelRequest(
        turn_id=turn_id, messages=(RustSystemMessage(content=[_text(text)]),), tools=()
    )


def test_the_log_keys_the_root_and_each_child_apart() -> None:
    log = ModelRequestLog()
    log.bind("root-1", None)

    log.record(_system_request("turn-abc", "root"))
    log.record(_system_request("child-1:turn:2", "child prompt"))

    assert log.latest("root-1") == PayloadSizes(system=len("root"))
    assert log.latest("child-1") == PayloadSizes(system=len("child prompt"))
    assert log.latest("child-2") is None


def test_a_saved_split_survives_a_new_process(tmp_path: Path) -> None:
    (tmp_path / "root-1").mkdir()
    (tmp_path / "child-1").mkdir()
    first = ModelRequestLog()
    first.bind("root-1", tmp_path)
    first.record(_system_request("turn-abc", "root"))
    first.record(_system_request("child-1:turn:2", "child prompt"))

    reopened = ModelRequestLog()
    reopened.bind("root-1", tmp_path)

    assert reopened.latest("root-1") is None
    assert reopened.saved("root-1") == PayloadSizes(system=len("root"))
    assert reopened.saved("child-1") == PayloadSizes(system=len("child prompt"))
    assert reopened.saved("child-2") is None


def test_a_session_not_on_disk_keeps_its_split_in_memory(tmp_path: Path) -> None:
    log = ModelRequestLog()
    log.bind("root-1", tmp_path)

    log.record(_system_request("turn-abc", "root"))

    assert log.latest("root-1") == PayloadSizes(system=len("root"))
    assert not (tmp_path / "root-1").exists()


def test_an_unreadable_saved_split_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "root-1").mkdir()
    (tmp_path / "root-1" / "context-breakdown.json").write_text("{", "utf-8")
    log = ModelRequestLog()
    log.bind("root-1", tmp_path)

    assert log.saved("root-1") is None
