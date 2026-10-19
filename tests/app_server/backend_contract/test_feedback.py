from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
import time
import tomllib
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._feedback import (
    FEEDBACK_COOLDOWN_SECONDS,
    FEEDBACK_RESPONDED_COOLDOWN_SECONDS,
    FEEDBACK_SNOOZED_COOLDOWN_SECONDS,
    MIN_USER_MESSAGES_FOR_FEEDBACK,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession

# The on-disk cache layout is a contract: users upgrading keep their cooldowns.
_SECTION = "user_feedback"
_LAST_SHOWN = "last_shown_at"
_RESPONDED = "responded_at"
_SNOOZED = "snoozed_at"

# What both clients send. The server ignores it: history already holds that
# prompt when they ask (the Rust TUI on ``turn/started``, ACP after the turn).
_PENDING = 1


def _configure(config_dir: Path, *, telemetry: bool) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["enable_telemetry"] = telemetry
    config["experiments"] = {"enable": False}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _write_cache(config_dir: Path, values: dict[str, Any]) -> None:
    (config_dir / "cache.toml").write_text(
        tomli_w.dumps({_SECTION: values}), encoding="utf-8"
    )


def _read_cache(config_dir: Path) -> dict[str, Any]:
    with (config_dir / "cache.toml").open("rb") as f:
        return tomllib.load(f)[_SECTION]


@pytest.fixture
def sampling_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vibe.app_server._feedback.FEEDBACK_PROBABILITY", 1.0)


@pytest.fixture
def telemetry_enabled() -> bool:
    return True


@pytest_asyncio.fixture
async def feedback_connection(
    config_dir: Path, telemetry_enabled: bool
) -> AsyncIterator[BackendContractConnection]:
    _configure(config_dir, telemetry=telemetry_enabled)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    try:
        yield connection
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def feedback_session(
    feedback_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
) -> AsyncIterator[AppServerSession]:
    backend_contract_mistral_api.mock(
        side_effect=lambda _: backend_contract_mistral_response("ok")
    )
    session = await feedback_connection.host.open_session()
    try:
        yield session
    finally:
        await session.close()


async def _type_messages(session: AppServerSession, count: int) -> None:
    for index in range(count):
        _ = [event async for event in session.act(f"typed {index}")]


async def _inject_messages(session: AppServerSession, count: int) -> None:
    for index in range(count):
        _ = [event async for event in session.act(f"injected {index}", injected=True)]
        await session.inject_user_context(f"context {index}", as_message=True)


@pytest_asyncio.fixture
async def eligible_session(
    feedback_session: AppServerSession, sampling_hits: None
) -> AppServerSession:
    await _type_messages(feedback_session, MIN_USER_MESSAGES_FOR_FEEDBACK)
    return feedback_session


async def _should_show(session: AppServerSession) -> bool:
    return await session.resources.feedback.should_show(pending_user_messages=_PENDING)


@pytest.mark.asyncio
async def test_shows_when_conditions_met(eligible_session: AppServerSession) -> None:
    assert await _should_show(eligible_session)


@pytest.mark.asyncio
async def test_does_not_show_when_random_misses(
    eligible_session: AppServerSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vibe.app_server._feedback.FEEDBACK_PROBABILITY", 0.0)

    assert not await _should_show(eligible_session)


@pytest.mark.asyncio
@pytest.mark.parametrize("telemetry_enabled", [False])
async def test_does_not_show_when_telemetry_inactive(
    eligible_session: AppServerSession,
) -> None:
    assert not await _should_show(eligible_session)


@pytest.mark.asyncio
async def test_does_not_show_when_too_few_user_messages(
    feedback_session: AppServerSession, sampling_hits: None
) -> None:
    await _type_messages(feedback_session, MIN_USER_MESSAGES_FOR_FEEDBACK - 1)

    assert not await _should_show(feedback_session)


@pytest.mark.asyncio
async def test_skips_injected_messages_in_count(
    feedback_session: AppServerSession, sampling_hits: None
) -> None:
    await _type_messages(feedback_session, MIN_USER_MESSAGES_FOR_FEEDBACK - 1)
    await _inject_messages(feedback_session, 2)

    assert not await _should_show(feedback_session)


@pytest.mark.asyncio
async def test_response_includes_server_owned_snooze_duration(
    feedback_connection: BackendContractConnection, feedback_session: AppServerSession
) -> None:
    response = FeedbackShouldShowResponse.model_validate(
        await feedback_connection.client.request(
            "feedback/shouldShow",
            FeedbackShouldShowParams(
                session_id=feedback_session.session_id, pending_user_messages=_PENDING
            ),
        )
    )

    assert response.snooze_duration_seconds == FEEDBACK_SNOOZED_COOLDOWN_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "key"),
    [
        pytest.param("asked", _LAST_SHOWN, id="test_writes_timestamp_to_cache"),
        pytest.param(
            "given", _RESPONDED, id="test_writes_responded_timestamp_to_cache"
        ),
        pytest.param("snoozed", _SNOOZED, id="test_writes_snoozed_timestamp_to_cache"),
    ],
)
async def test_record_writes_timestamp_and_blocks_the_prompt(
    eligible_session: AppServerSession, config_dir: Path, action: Any, key: str
) -> None:
    before = int(time.time())

    await eligible_session.resources.feedback.record(action)

    assert _read_cache(config_dir)[key] >= before
    assert not await _should_show(eligible_session)


@pytest.mark.asyncio
async def test_record_keeps_other_timestamps(
    eligible_session: AppServerSession, config_dir: Path
) -> None:
    await eligible_session.resources.feedback.record("asked")
    await eligible_session.resources.feedback.record("given")

    assert set(_read_cache(config_dir)) == {_LAST_SHOWN, _RESPONDED}


def _ago(seconds: int) -> int:
    return int(time.time()) - seconds


_SHOWN_EXPIRED = FEEDBACK_COOLDOWN_SECONDS + 1
_RESPONDED_EXPIRED = FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 1
_SNOOZED_EXPIRED = FEEDBACK_SNOOZED_COOLDOWN_SECONDS + 1

_COOLDOWN_CASES = [
    pytest.param(
        lambda: {_LAST_SHOWN: _ago(60)}, False, id="test_does_not_show_within_cooldown"
    ),
    pytest.param(
        lambda: {_LAST_SHOWN: _ago(_SHOWN_EXPIRED)},
        True,
        id="test_shows_after_cooldown_expires",
    ),
    pytest.param(
        lambda: {_RESPONDED: _ago(60)},
        False,
        id="test_does_not_show_within_responded_cooldown",
    ),
    pytest.param(
        lambda: {_RESPONDED: _ago(_RESPONDED_EXPIRED)},
        True,
        id="test_shows_after_responded_cooldown_expires",
    ),
    pytest.param(
        lambda: {
            _RESPONDED: _ago(_RESPONDED_EXPIRED),
            _LAST_SHOWN: _ago(_SHOWN_EXPIRED),
        },
        True,
        id="test_responded_cooldown_longer_than_shown_cooldown",
    ),
    pytest.param(
        lambda: {_RESPONDED: _ago(60), _LAST_SHOWN: _ago(_RESPONDED_EXPIRED)},
        False,
        id="test_responded_takes_precedence_over_shown",
    ),
    pytest.param(
        lambda: {_RESPONDED: _ago(_RESPONDED_EXPIRED), _LAST_SHOWN: "not_an_int"},
        True,
        id="test_shows_when_responded_valid_but_last_shown_corrupted",
    ),
    pytest.param(
        lambda: {
            _RESPONDED: _ago(FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 3600),
            _LAST_SHOWN: _ago(1800),
        },
        False,
        id="test_does_not_show_when_last_shown_recent_even_if_responded_cooldown_expired",
    ),
    pytest.param(
        lambda: {
            _RESPONDED: _ago(FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 3600),
            _LAST_SHOWN: _ago(_SHOWN_EXPIRED),
        },
        True,
        id="test_shows_when_both_cooldowns_expired",
    ),
    pytest.param(
        lambda: {
            _RESPONDED: _ago(FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 3600),
            _LAST_SHOWN: 0,
        },
        True,
        id="test_shows_when_last_shown_zero_and_responded_expired",
    ),
    pytest.param(
        lambda: {
            _RESPONDED: _ago(3600),
            _LAST_SHOWN: _ago(FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 3600),
        },
        False,
        id="test_responded_cooldown_blocks_even_with_old_last_shown",
    ),
    pytest.param(
        lambda: {_SNOOZED: _ago(60)},
        False,
        id="test_does_not_show_within_snoozed_cooldown",
    ),
    pytest.param(
        lambda: {_SNOOZED: _ago(_SNOOZED_EXPIRED)},
        True,
        id="test_shows_after_snoozed_cooldown_expires",
    ),
    pytest.param(
        lambda: {_SNOOZED: _ago(60), _RESPONDED: _ago(_RESPONDED_EXPIRED)},
        False,
        id="test_snoozed_takes_precedence_over_responded",
    ),
    pytest.param(
        lambda: {_SNOOZED: _ago(60), _LAST_SHOWN: _ago(_SHOWN_EXPIRED)},
        False,
        id="test_snoozed_takes_precedence_over_shown",
    ),
    pytest.param(
        lambda: {
            _SNOOZED: _ago(FEEDBACK_SNOOZED_COOLDOWN_SECONDS + 3600),
            _RESPONDED: _ago(FEEDBACK_RESPONDED_COOLDOWN_SECONDS + 3600),
            _LAST_SHOWN: _ago(_SHOWN_EXPIRED),
        },
        True,
        id="test_shows_when_all_cooldowns_expired",
    ),
    pytest.param(
        lambda: {_SNOOZED: _ago(_SNOOZED_EXPIRED), _RESPONDED: _ago(60)},
        False,
        id="test_does_not_show_when_snoozed_expired_but_responded_recent",
    ),
    pytest.param(
        lambda: {_LAST_SHOWN: _ago(-FEEDBACK_SNOOZED_COOLDOWN_SECONDS)},
        True,
        id="test_ignores_a_timestamp_in_the_future",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("cache", "expected"), _COOLDOWN_CASES)
async def test_cooldowns_gate_the_prompt(
    eligible_session: AppServerSession,
    config_dir: Path,
    cache: Callable[[], dict[str, Any]],
    expected: bool,
) -> None:
    _write_cache(config_dir, cache())

    assert await _should_show(eligible_session) is expected
