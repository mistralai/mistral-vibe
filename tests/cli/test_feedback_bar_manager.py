from __future__ import annotations

from pathlib import Path
import time
import tomllib
from unittest.mock import MagicMock, patch

from vibe.cli.textual_ui.widgets.feedback_bar_manager import (
    _CACHE_SECTION,
    _LAST_SHOWN_KEY,
    FEEDBACK_COOLDOWN_SECONDS,
    MIN_USER_MESSAGES_FOR_FEEDBACK,
    FeedbackBarManager,
)
from vibe.core.types import LLMMessage, Role


def _patch_cache_file(tmp_path: Path):
    from vibe.core.paths._vibe_home import GlobalPath

    return patch(
        "vibe.cli.textual_ui.widgets.feedback_bar_manager.CACHE_FILE",
        GlobalPath(lambda: tmp_path / "cache.toml"),
    )


def _patch_probability(value: float):
    return patch(
        "vibe.cli.textual_ui.widgets.feedback_bar_manager.FEEDBACK_PROBABILITY", value
    )


def _make_agent_loop(
    user_message_count: int = MIN_USER_MESSAGES_FOR_FEEDBACK,
    telemetry_active: bool = True,
) -> MagicMock:
    loop = MagicMock()
    loop.telemetry_client.is_active.return_value = telemetry_active
    messages = [
        LLMMessage(role=Role.user, content=f"msg {i}")
        for i in range(user_message_count)
    ]
    loop.messages = messages
    return loop


class TestShouldShow:
    def test_shows_when_conditions_met(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=0.0,
            ),
        ):
            assert manager.should_show(_make_agent_loop()) is True

    def test_does_not_show_when_random_misses(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=1.0,
            ),
        ):
            assert manager.should_show(_make_agent_loop()) is False

    def test_does_not_show_within_cooldown(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_LAST_SHOWN_KEY} = {int(time.time()) - 60}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=0.0,
            ),
        ):
            assert manager.should_show(_make_agent_loop()) is False

    def test_shows_after_cooldown_expires(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_LAST_SHOWN_KEY} = {int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=0.0,
            ),
        ):
            assert manager.should_show(_make_agent_loop()) is True

    def test_does_not_show_when_telemetry_inactive(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with _patch_cache_file(tmp_path), _patch_probability(0.2):
            assert (
                manager.should_show(_make_agent_loop(telemetry_active=False)) is False
            )

    def test_does_not_show_when_too_few_user_messages(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=0.0,
            ),
        ):
            assert manager.should_show(_make_agent_loop(user_message_count=1)) is False

    def test_skips_injected_messages_in_count(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(user_message_count=0)
        loop.messages = [
            LLMMessage(role=Role.user, content="real"),
            LLMMessage(role=Role.user, content="injected", injected=True),
            LLMMessage(role=Role.assistant, content="reply"),
        ]
        manager = FeedbackBarManager()
        with (
            _patch_cache_file(tmp_path),
            _patch_probability(0.2),
            patch(
                "vibe.cli.textual_ui.widgets.feedback_bar_manager.random.random",
                return_value=0.0,
            ),
        ):
            # Only 1 non-injected user message, below MIN_USER_MESSAGES_FOR_FEEDBACK
            assert manager.should_show(loop) is False


class TestRecordFeedbackAsked:
    def test_writes_timestamp_to_cache(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        before = int(time.time())
        with _patch_cache_file(tmp_path):
            manager.record_feedback_asked()
            with (tmp_path / "cache.toml").open("rb") as f:
                data = tomllib.load(f)
        assert data[_CACHE_SECTION][_LAST_SHOWN_KEY] >= before
