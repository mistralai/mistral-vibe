from __future__ import annotations

from unittest.mock import MagicMock

from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.core.session.session_id import shorten_session_id


class TestCompactMessage:
    def test_get_content_includes_session_ids_after_compaction(self) -> None:
        message = CompactMessage()
        message.post_message = MagicMock()

        message.set_complete(
            old_tokens=177_017,
            new_tokens=23_263,
            old_session_id="11111111-1111-1111-1111-111111111111",
            new_session_id="22222222-2222-2222-2222-222222222222",
        )

        assert message.get_content() == (
            "Compaction complete: 177,017 → 23,263 tokens (-87.%)\n"
            "session: "
            f"{shorten_session_id('11111111-1111-1111-1111-111111111111')} "
            "(before compaction) → "
            f"{shorten_session_id('22222222-2222-2222-2222-222222222222')} "
            "(after compaction)"
        )
