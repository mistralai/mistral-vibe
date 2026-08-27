from __future__ import annotations

import asyncio

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.session import title_model
from vibe.core.session.title_model import (
    _clean_title,
    _user_prompt,
    build_title_transcript,
    generate_session_title,
)
from vibe.core.session.title_policy import DEFAULT_TITLE_POLICY, TitlePolicy
from vibe.core.types import LLMMessage, Role

_MAX_MESSAGE_CHARS = DEFAULT_TITLE_POLICY.max_message_chars
MAX_GENERATED_TITLE_CHARS = DEFAULT_TITLE_POLICY.max_title_chars


class TestBuildTitleTranscript:
    def test_skips_system_and_empty_messages(self) -> None:
        messages = [
            LLMMessage(role=Role.system, content="system prompt"),
            LLMMessage(role=Role.user, content="  hello  "),
            LLMMessage(role=Role.assistant, content=""),
            LLMMessage(role=Role.assistant, content="world"),
        ]

        assert build_title_transcript(messages) == "user: hello\n\nassistant: world"

    def test_empty_when_only_system(self) -> None:
        messages = [LLMMessage(role=Role.system, content="system")]

        assert build_title_transcript(messages) == ""

    def test_keeps_head_and_tail_when_over_cap(self) -> None:
        # Over the cap, the opening intent and the latest message both survive,
        # separated by an elision marker, so a refresh sees evolving context.
        messages = [
            LLMMessage(role=Role.user, content="INTENT " + "x" * _MAX_MESSAGE_CHARS),
            LLMMessage(role=Role.assistant, content="a" * _MAX_MESSAGE_CHARS),
            LLMMessage(role=Role.assistant, content="b" * _MAX_MESSAGE_CHARS),
            LLMMessage(role=Role.user, content="LATEST focus"),
        ]

        transcript = build_title_transcript(messages)

        assert "INTENT" in transcript
        assert "LATEST focus" in transcript
        assert "[…]" in transcript
        assert len(transcript) <= 6000 + len("\n\n[…]\n\n")

    def test_truncates_each_message(self) -> None:
        messages = [LLMMessage(role=Role.user, content="a" * 10_000)]

        transcript = build_title_transcript(messages)

        assert transcript == f"user: {'a' * _MAX_MESSAGE_CHARS}"


class TestCleanTitle:
    def test_none_and_empty_return_none(self) -> None:
        assert _clean_title(None) is None
        assert _clean_title("") is None
        assert _clean_title("   ") is None

    def test_strips_wrapping_quotes_and_collapses_whitespace(self) -> None:
        assert _clean_title('  "Fix   login   bug"  ') == "Fix login bug"
        assert _clean_title("`Add retry logic`") == "Add retry logic"

    def test_keeps_only_first_line(self) -> None:
        assert _clean_title("Real title\nextra chatter") == "Real title"

    def test_strips_terminal_control_characters(self) -> None:
        cleaned = _clean_title("Fix\x1b]0;pwned\x07 login")

        assert cleaned is not None
        assert "\x1b" not in cleaned and "\x07" not in cleaned
        assert _clean_title("Bad\x07title") == "Badtitle"
        assert _clean_title("csi\x9bhere") == "csihere"

    def test_generic_titles_return_none(self) -> None:
        assert _clean_title("New session") is None
        assert _clean_title("untitled") is None
        assert _clean_title("Untitled session") is None

    def test_caps_length_with_ellipsis(self) -> None:
        title = _clean_title("word " * 40)

        assert title is not None
        assert title.endswith("…")
        assert len(title) <= MAX_GENERATED_TITLE_CHARS + 1


class TestUserPrompt:
    def test_without_previous_title_returns_transcript(self) -> None:
        assert _user_prompt("user: hi", None) == "user: hi"

    def test_includes_previous_title_for_refinement(self) -> None:
        prompt = _user_prompt("user: hi", "Old title")

        assert prompt.startswith("Current title: Old title")
        assert "user: hi" in prompt


class TestGenerateSessionTitle:
    @staticmethod
    def _patch_completion(monkeypatch, content: str | None) -> None:
        async def fake(**_):
            return content

        monkeypatch.setattr(title_model, "run_utility_completion", fake)

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_transcript(self) -> None:
        config = build_test_vibe_config()

        assert await generate_session_title([], config=config) is None

    @pytest.mark.asyncio
    async def test_returns_cleaned_title(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        self._patch_completion(monkeypatch, '"Fix login bug"')

        title = await generate_session_title(
            [LLMMessage(role=Role.user, content="please fix the login bug")],
            config=config,
        )

        assert title == "Fix login bug"

    @pytest.mark.asyncio
    async def test_forwards_prompt_transcript_and_budgets(self, monkeypatch) -> None:
        config = build_test_vibe_config()
        captured: dict = {}

        async def fake(**kwargs):
            captured.update(kwargs)
            return "Fix login bug"

        monkeypatch.setattr(title_model, "run_utility_completion", fake)

        await generate_session_title(
            [LLMMessage(role=Role.user, content="do a thing")],
            config=config,
            previous_title="Old title",
        )

        assert captured["config"] is config
        assert captured["retry_budget_seconds"] > 0
        assert captured["max_tokens"] > 0
        assert "do a thing" in captured["user_content"]
        assert "Old title" in captured["user_content"]

    @pytest.mark.asyncio
    async def test_propagates_backend_errors(self, monkeypatch) -> None:
        config = build_test_vibe_config()

        async def boom(**_):
            raise RuntimeError("boom")

        monkeypatch.setattr(title_model, "run_utility_completion", boom)

        with pytest.raises(RuntimeError, match="boom"):
            await generate_session_title(
                [LLMMessage(role=Role.user, content="something")], config=config
            )

    @pytest.mark.asyncio
    async def test_propagates_timeouts(self, monkeypatch) -> None:
        config = build_test_vibe_config()

        async def hang(**_):
            await asyncio.sleep(3600)

        monkeypatch.setattr(title_model, "run_utility_completion", hang)

        with pytest.raises(TimeoutError):
            await generate_session_title(
                [LLMMessage(role=Role.user, content="something")],
                config=config,
                policy=TitlePolicy(total_timeout_seconds=0.05),
            )
