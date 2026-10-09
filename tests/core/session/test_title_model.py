from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.config import UtilityFeature
from vibe.core.prompts import UtilityPrompt
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
        assert captured["feature"] is UtilityFeature.TITLE
        assert "call_type" not in captured
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

    @pytest.mark.asyncio
    async def test_uses_builtin_session_title_prompt_by_default(
        self, monkeypatch
    ) -> None:
        config = build_test_vibe_config()
        captured: dict = {}

        async def fake(**kwargs):
            captured.update(kwargs)
            return "Fix login bug"

        monkeypatch.setattr(title_model, "run_utility_completion", fake)

        await generate_session_title(
            [LLMMessage(role=Role.user, content="please fix the login bug")],
            config=config,
        )

        assert captured["system_prompt"] == UtilityPrompt.SESSION_TITLE.read()

    @pytest.mark.asyncio
    async def test_uses_custom_prompt_from_project_dir(
        self, monkeypatch, mock_prompts_dirs: tuple[Path, Path]
    ) -> None:
        project_prompts, _ = mock_prompts_dirs
        (project_prompts / "terse_titles.md").write_text("Reply with one word.")

        config = build_test_vibe_config(title_prompt_id="terse_titles")
        captured: dict = {}

        async def fake(**kwargs):
            captured.update(kwargs)
            return "Login"

        monkeypatch.setattr(title_model, "run_utility_completion", fake)

        await generate_session_title(
            [LLMMessage(role=Role.user, content="please fix the login bug")],
            config=config,
        )

        assert captured["system_prompt"] == "Reply with one word."


class TestTitlePromptResolution:
    def test_default_falls_back_to_builtin(
        self, mock_prompts_dirs: tuple[Path, Path]
    ) -> None:
        config = build_test_vibe_config()

        assert config.title_prompt == UtilityPrompt.SESSION_TITLE.read()

    def test_custom_prompt_found_in_user_dir_when_missing_from_project(
        self, mock_prompts_dirs: tuple[Path, Path]
    ) -> None:
        _, user_prompts = mock_prompts_dirs
        (user_prompts / "brief.md").write_text("One word titles only")

        config = build_test_vibe_config(title_prompt_id="brief")

        assert config.title_prompt == "One word titles only"

    def test_custom_prompt_overrides_builtin(
        self, mock_prompts_dirs: tuple[Path, Path]
    ) -> None:
        project_prompts, _ = mock_prompts_dirs
        (project_prompts / "session_title.md").write_text("My custom title prompt")

        config = build_test_vibe_config()

        assert config.title_prompt == "My custom title prompt"

    def test_invalid_prompt_id_reports_setting_name(
        self, mock_prompts_dirs: tuple[Path, Path]
    ) -> None:
        project_prompts, user_prompts = mock_prompts_dirs
        (project_prompts / "alpha.md").write_text("a")
        (user_prompts / "beta.md").write_text("b")

        with pytest.raises(ValueError) as exc_info:
            build_test_vibe_config(title_prompt_id="unknown")

        error_text = str(exc_info.value)
        assert "Invalid title_prompt_id value: 'unknown'" in error_text
        assert 'available prompts ("session_title")' in error_text
        assert '(available: "alpha", "beta")' in error_text

    @pytest.mark.parametrize(
        "malicious_id",
        ["../../../etc/passwd", "..", ".", "subdir/session_title", "back\\slash", ""],
    )
    def test_prompt_id_rejects_path_traversal(
        self, mock_prompts_dirs: tuple[Path, Path], malicious_id: str
    ) -> None:
        with pytest.raises(ValueError, match="must be a bare filename"):
            build_test_vibe_config(title_prompt_id=malicious_id)
