from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.app_server.protocol import SessionSettingsUpdateParams


def test_session_settings_require_a_strict_non_negative_update() -> None:
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1")
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1", max_turns=True)
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1", max_tokens=-1)

    assert SessionSettingsUpdateParams(session_id="session-1", max_turns=0)
