from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _session_api_tests_use_the_session_api(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn the Session API switch on for the tests written for that path."""
    if request.path.name.startswith("test_unified_api_"):
        monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "true")
