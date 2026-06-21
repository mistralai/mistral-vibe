from __future__ import annotations

from unittest.mock import MagicMock

from vibe.core.session.resume_sessions import (
    ResumeSessionInfo,
    session_latest_messages,
    short_session_id,
)
from vibe.core.session.session_id import shorten_session_id


class TestShortenSessionId:
    def test_shortens_to_first_8_chars(self) -> None:
        sid = "abcdef1234567890"
        assert shorten_session_id(sid) == "abcdef12"

    def test_from_end_shortens_to_last_8_chars(self) -> None:
        sid = "abcdef1234567890"
        assert shorten_session_id(sid, from_end=True) == "34567890"

    def test_returns_full_id_when_shorter_than_limit(self) -> None:
        sid = "abc"
        assert shorten_session_id(sid) == "abc"
        assert shorten_session_id(sid, from_end=True) == "abc"


class TestShortSessionId:
    def test_delegates_to_shorten(self) -> None:
        sid = "abcdef1234567890"
        assert short_session_id(sid) == shorten_session_id(sid)

    def test_empty_string(self) -> None:
        assert short_session_id("") == ""


class TestSessionLatestMessages:
    def test_uses_session_title_when_present(self) -> None:
        session = ResumeSessionInfo(
            session_id="session-a", cwd="/test", title="My run", end_time=None
        )
        messages = session_latest_messages([session], MagicMock())
        assert messages[session.option_id] == "My run"
