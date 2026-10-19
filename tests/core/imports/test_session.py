from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.imports._session import VibeSessionPayload


@pytest.mark.parametrize(
    ("source_harness", "source_session_id", "error"),
    [("", "session-id", "source harness"), ("claude", "", "source session ID")],
)
def test_imported_session_requires_source_identity(
    source_harness: str, source_session_id: str, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        VibeSessionPayload(
            cwd=Path("/workspace"),
            messages=[],
            title=None,
            started_at=None,
            source_harness=source_harness,
            source_session_id=source_session_id,
            source_cwd=None,
        )
