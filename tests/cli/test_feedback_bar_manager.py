from __future__ import annotations

from pathlib import Path
import time
import tomllib
from unittest.mock import MagicMock, patch

from vibe.cli.textual_ui.widgets.feedback_bar_manager import FeedbackBarManager
from vibe.core.cache_store import FileSystemVibeCodeCacheStore
from vibe.core.feedback import (
    _CACHE_SECTION,
    _LAST_SHOWN_KEY,
    _RESPONDED_AT_KEY,
    _SNOOZED_AT_KEY,
    FEEDBACK_COOLDOWN_SECONDS,
    FEEDBACK_RESPONDED_COOLDOWN_SECONDS,
    FEEDBACK_SNOOZED_COOLDOWN_SECONDS,
    MIN_USER_MESSAGES_FOR_FEEDBACK,
)
from vibe.core.types import LLMMessage, Role


def _patch_probability(value: float):
    return patch("vibe.core.feedback.FEEDBACK_PROBABILITY", value)


def _make_agent_loop(
    cache_path: Path,
    user_message_count: int = MIN_USER_MESSAGES_FOR_FEEDBACK,
    telemetry_active: bool = True,
) -> MagicMock:
    loop = MagicMock()
    loop.telemetry_client.is_active.return_value = telemetry_active
    loop.cache_store = FileSystemVibeCodeCacheStore(cache_path)
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
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_does_not_show_when_random_misses(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=1.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_does_not_show_within_cooldown(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_LAST_SHOWN_KEY} = {int(time.time()) - 60}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_after_cooldown_expires(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_LAST_SHOWN_KEY} = {int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_does_not_show_when_telemetry_inactive(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with _patch_probability(0.2):
            assert (
                manager.should_show(
                    _make_agent_loop(tmp_path / "cache.toml", telemetry_active=False)
                )
                is False
            )

    def test_does_not_show_when_too_few_user_messages(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(
                    _make_agent_loop(tmp_path / "cache.toml", user_message_count=1)
                )
                is False
            )

    def test_skips_injected_messages_in_count(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(tmp_path / "cache.toml", user_message_count=0)
        loop.messages = [
            LLMMessage(role=Role.user, content="real"),
            LLMMessage(role=Role.user, content="injected", injected=True),
            LLMMessage(role=Role.assistant, content="reply"),
        ]
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            # Only 1 non-injected user message, below MIN_USER_MESSAGES_FOR_FEEDBACK
            assert manager.should_show(loop) is False


class TestRecordFeedbackAsked:
    def test_writes_timestamp_to_cache(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        before = int(time.time())
        manager.record_feedback_asked(_make_agent_loop(tmp_path / "cache.toml"))
        with (tmp_path / "cache.toml").open("rb") as f:
            data = tomllib.load(f)
        assert data[_CACHE_SECTION][_LAST_SHOWN_KEY] >= before


class TestRecordFeedbackGiven:
    def test_writes_responded_timestamp_to_cache(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        before = int(time.time())
        manager.record_feedback_given(_make_agent_loop(tmp_path / "cache.toml"))
        with (tmp_path / "cache.toml").open("rb") as f:
            data = tomllib.load(f)
        assert data[_CACHE_SECTION][_RESPONDED_AT_KEY] >= before


class TestShouldShowWithResponded:
    def test_does_not_show_within_responded_cooldown(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {int(time.time()) - 60}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_after_responded_cooldown_expires(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 1}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_responded_cooldown_longer_than_shown_cooldown(
        self, tmp_path: Path
    ) -> None:
        one_hour_ago = int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1
        twenty_four_hours_ago = (
            int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 1
        )

        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {twenty_four_hours_ago}\n{_LAST_SHOWN_KEY} = {one_hour_ago}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_responded_takes_precedence_over_shown(self, tmp_path: Path) -> None:
        recent_time = int(time.time()) - 60
        old_time = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 1

        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {recent_time}\n{_LAST_SHOWN_KEY} = {old_time}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_when_responded_valid_but_last_shown_corrupted(
        self, tmp_path: Path
    ) -> None:
        old_time = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 1

        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {old_time}\n{_LAST_SHOWN_KEY} = not_an_int\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_does_not_show_when_last_shown_recent_even_if_responded_cooldown_expired(
        self, tmp_path: Path
    ) -> None:
        """Bug: when responded_at cooldown expires but last_shown_at is recent, feedback should NOT show."""
        twenty_five_hours_ago = (
            int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 3600
        )
        thirty_minutes_ago = int(time.time()) - 1800

        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {twenty_five_hours_ago}\n{_LAST_SHOWN_KEY} = {thirty_minutes_ago}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_when_both_cooldowns_expired(self, tmp_path: Path) -> None:
        responded_at = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 3600
        last_shown_at = int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {responded_at}\n{_LAST_SHOWN_KEY} = {last_shown_at}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_shows_when_last_shown_zero_and_responded_expired(
        self, tmp_path: Path
    ) -> None:
        responded_at = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 3600
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {responded_at}\n{_LAST_SHOWN_KEY} = 0\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_responded_cooldown_blocks_even_with_old_last_shown(
        self, tmp_path: Path
    ) -> None:
        responded_at = int(time.time()) - 3600
        last_shown_at = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 3600
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_RESPONDED_AT_KEY} = {responded_at}\n{_LAST_SHOWN_KEY} = {last_shown_at}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )


class TestRecordFeedbackSnoozed:
    def test_writes_snoozed_timestamp_to_cache(self, tmp_path: Path) -> None:
        manager = FeedbackBarManager()
        before = int(time.time())
        manager.record_feedback_snoozed(_make_agent_loop(tmp_path / "cache.toml"))
        with (tmp_path / "cache.toml").open("rb") as f:
            data = tomllib.load(f)
        assert data[_CACHE_SECTION][_SNOOZED_AT_KEY] >= before


class TestShouldShowWithSnoozed:
    def test_does_not_show_within_snoozed_cooldown(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {int(time.time()) - 60}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_after_snoozed_cooldown_expires(self, tmp_path: Path) -> None:
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {int(time.time()) - FEEDBACK_SNOOZED_COOLDOWN_SECONDS - 1}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_snoozed_takes_precedence_over_responded(self, tmp_path: Path) -> None:
        recent_snoozed = int(time.time()) - 60
        old_responded = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 1
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {recent_snoozed}\n{_RESPONDED_AT_KEY} = {old_responded}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_snoozed_takes_precedence_over_shown(self, tmp_path: Path) -> None:
        recent_snoozed = int(time.time()) - 60
        old_last_shown = int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {recent_snoozed}\n{_LAST_SHOWN_KEY} = {old_last_shown}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )

    def test_shows_when_all_cooldowns_expired(self, tmp_path: Path) -> None:
        snoozed_at = int(time.time()) - FEEDBACK_SNOOZED_COOLDOWN_SECONDS - 3600
        responded_at = int(time.time()) - FEEDBACK_RESPONDED_COOLDOWN_SECONDS - 3600
        last_shown_at = int(time.time()) - FEEDBACK_COOLDOWN_SECONDS - 1
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {snoozed_at}\n{_RESPONDED_AT_KEY} = {responded_at}\n{_LAST_SHOWN_KEY} = {last_shown_at}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is True
            )

    def test_shows_when_snoozed_expired_but_responded_recent(
        self, tmp_path: Path
    ) -> None:
        old_snoozed = int(time.time()) - FEEDBACK_SNOOZED_COOLDOWN_SECONDS - 1
        recent_responded = int(time.time()) - 60
        (tmp_path / "cache.toml").write_text(
            f"[{_CACHE_SECTION}]\n{_SNOOZED_AT_KEY} = {old_snoozed}\n{_RESPONDED_AT_KEY} = {recent_responded}\n"
        )
        manager = FeedbackBarManager()
        with (
            _patch_probability(0.2),
            patch("vibe.core.feedback.random.random", return_value=0.0),
        ):
            assert (
                manager.should_show(_make_agent_loop(tmp_path / "cache.toml")) is False
            )
