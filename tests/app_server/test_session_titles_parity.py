"""The app server's title prompt and policy match the local runtime's own.

While the local runtime still generates titles, both must give the same titles,
and the app server sends no prompt for the default ``title_prompt_id`` because
the runtime's built-in prompt is the same text.
"""

from __future__ import annotations

import dataclasses

from mistralai_vibe_local_harness.vibe import _title as runtime_title
from vibe.app_server._session_titles import DEFAULT_TITLE_POLICY
from vibe.core.prompts import UtilityPrompt


def test_the_session_title_prompt_is_the_runtime_built_in_prompt() -> None:
    # Do
    prompt = UtilityPrompt.SESSION_TITLE.read()

    # Assert
    assert prompt == runtime_title._SESSION_TITLE_SYSTEM_PROMPT.strip()


def test_the_title_policy_has_the_runtime_values() -> None:
    # Do
    app_server = dataclasses.asdict(DEFAULT_TITLE_POLICY)
    runtime = dataclasses.asdict(runtime_title.DEFAULT_TITLE_POLICY)

    # Assert
    assert app_server == runtime
