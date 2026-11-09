from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, cast

import pytest
from sentry_sdk.types import Event, Hint

from vibe.observability.logging import StructuredLogFormatter
from vibe.observability.sentry import _before_send
from vibe.utils.api_keys import (
    ApiKeySource,
    resolve_api_key,
    resolve_api_key_with_origin,
    session_credentials,
)
from vibe.utils.secrets import REDACTED, holding_secrets, redact

_KEY = "CUSTOM_API_KEY"


def test_session_credentials_come_first_and_only_inside_their_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: The environment holds a key.
    *Do*: Resolve it inside and outside a context holding another value.
    *Assert*: Inside, the context's value wins and names the run as its
    origin; outside, the environment's value is back. The environment never
    changes.
    """
    # Prepare
    monkeypatch.setenv(_KEY, "env-key")

    # Do
    with session_credentials({_KEY: "run-key"}):
        inside = resolve_api_key_with_origin(_KEY)
        environment_inside = os.environ[_KEY]
    outside = resolve_api_key_with_origin(_KEY)

    # Assert
    assert inside is not None
    assert inside[0] == "run-key"
    assert inside[1].source is ApiKeySource.SESSION
    assert inside[1].describe() == f"the run's credential {_KEY}"
    assert environment_inside == "env-key"
    assert outside is not None
    assert outside[0] == "env-key"
    assert outside[1].source is ApiKeySource.ENVIRONMENT


def test_a_name_the_session_does_not_hold_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: The environment holds a key the run's credentials do not name.
    *Do*: Resolve it inside the run's context.
    *Assert*: The environment answers.
    """
    # Prepare
    monkeypatch.setenv(_KEY, "env-key")

    # Do
    with session_credentials({"OTHER_KEY": "run-key"}):
        resolved = resolve_api_key(_KEY)

    # Assert
    assert resolved == "env-key"


@pytest.mark.asyncio
async def test_concurrent_tasks_and_their_threads_each_see_their_own_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: No key in the environment.
    *Do*: Resolve it from two concurrent tasks holding different values, on
    the event loop and in a worker thread.
    *Assert*: Each task, and its thread, sees its own value only.
    """
    # Prepare
    monkeypatch.delenv(_KEY, raising=False)

    async def resolve_as(value: str) -> tuple[str | None, str | None]:
        with session_credentials({_KEY: value}):
            await asyncio.sleep(0.01)
            in_loop = resolve_api_key(_KEY)
            in_thread = await asyncio.to_thread(resolve_api_key, _KEY)
            return in_loop, in_thread

    # Do
    first, second = await asyncio.gather(resolve_as("one"), resolve_as("two"))

    # Assert
    assert first == ("one", "one")
    assert second == ("two", "two")
    assert resolve_api_key(_KEY) is None


def test_a_secret_is_redacted_until_every_holder_lets_go() -> None:
    """*Prepare*: Two holders of the same secret.
    *Do*: Redact a text as each lets go.
    *Assert*: It stays hidden until the last one has.
    """
    # Prepare
    text = "Authorization: Bearer s3cret-value"
    outer = holding_secrets(["s3cret-value"])
    inner = holding_secrets(["s3cret-value", ""])

    # Do
    with outer:
        with inner:
            both = redact(text)
        one = redact(text)
    none = redact(text)

    # Assert
    assert both == one == f"Authorization: Bearer {REDACTED}"
    assert none == text


def test_logs_and_error_reports_hide_a_running_sessions_credentials() -> None:
    """*Prepare*: A session holding a credential.
    *Do*: Format a log record and filter an error report that carry it.
    *Assert*: Neither contains it.
    """
    # Prepare
    secret = "tok-0123456789"
    record = logging.LogRecord(
        "vibe", logging.ERROR, __file__, 1, "sent %s", (secret,), None
    )
    event: dict[str, Any] = {
        "message": f"request with {secret} failed",
        "exception": {"values": [{"value": f"401 for key {secret}"}]},
    }

    # Do
    with session_credentials({_KEY: secret}):
        line = StructuredLogFormatter().format(record)
        report = _before_send(cast(Event, event), cast(Hint, {}))

    # Assert
    assert secret not in line
    assert f"sent {REDACTED}" in line
    assert report is not None
    assert secret not in str(report)
