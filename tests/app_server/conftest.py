from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _direct_path_tests_turn_the_session_api_off(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn the Session API switch off for the tests written for the direct path."""
    if request.path.name == "test_unified_harness_backend_direct.py":
        monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "false")
