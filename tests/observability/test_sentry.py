from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextvars
import errno
import functools
import gc
import logging
import os
import sys
import types
from typing import Any, cast
from unittest.mock import patch
import uuid

import httpx2
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from vibe.observability.sentry import (
    CLI_CRASH_REPORT_DSN_ENV,
    _before_send,
    _before_send_cli,
    _close_sentry,
    capture_cli_exception,
    capture_sentry_exception,
    claim_cli_crash_report_dsn,
    cli_crash_reporting_available,
    init_cli_sentry,
    init_sentry,
    report_unhandled_asyncio_errors,
)


@pytest.fixture
def sentry_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Event]]:
    events: list[Event] = []

    class _CapturingTransport(Transport):
        def capture_envelope(self, envelope: Envelope) -> None:
            if (event := envelope.get_event()) is not None:
                events.append(event)

    monkeypatch.setattr(
        "vibe.observability.sentry._ACP_SENTRY_DSN", "http://key@127.0.0.1:9/1"
    )
    monkeypatch.setattr(
        "vibe.observability.sentry._cli_dsn", "http://key@127.0.0.1:9/2"
    )
    monkeypatch.setattr(
        sentry_sdk,
        "init",
        functools.partial(sentry_sdk.init, transport=_CapturingTransport),
    )
    yield events
    _close_sentry()


def _send(event: dict[str, Any]) -> dict[str, Any]:
    result = _before_send(cast(Event, event), cast(Hint, {}))
    assert result is not None
    return cast(dict[str, Any], result)


def test_before_send_drops_ip_but_keeps_geo():
    event = {"user": {"geo": {"city": "Ulm"}, "ip_address": "1.2.3.4"}}
    result = _send(event)
    assert result["user"] == {"geo": {"city": "Ulm"}}


def test_cli_events_carry_the_launch_id_instead_of_the_user():
    event = {"user": {"id": "abc", "geo": {"city": "Ulm"}, "ip_address": "1.2.3.4"}}
    result = cast(dict[str, Any], _before_send_cli(cast(Event, event), cast(Hint, {})))
    assert result["user"]["geo"] == {"city": "Ulm"}
    assert "ip_address" not in result["user"]
    assert uuid.UUID(result["user"]["id"]).version == 4


def test_before_send_drops_breadcrumbs():
    event = {
        "breadcrumbs": {"values": [{"message": "cat /home/rk/secrets.txt"}]},
        "exception": {"values": [{"value": "boom"}]},
    }
    result = _send(event)
    assert "breadcrumbs" not in result
    assert result["exception"]["values"][0]["value"] == "boom"


def test_before_send_scrubs_paths_across_event():
    event = {
        "message": "boom at /Users/rk/x.py",
        "exception": {"values": [{"value": "failed in /home/rk/project/x.py"}]},
    }
    result = _send(event)
    assert result["message"] == "boom at [Filtered]/x.py"
    assert result["exception"]["values"][0]["value"] == "failed in [Filtered]/x.py"


def test_before_send_drops_a_full_disk_wrapped_in_another_error():
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as disk_full:
            raise RuntimeError("Failed to persist session metadata") from disk_full
    except RuntimeError as wrapped:
        hint = cast(Hint, {"exc_info": (RuntimeError, wrapped, None)})

    assert _before_send_cli(cast(Event, {}), hint) is None


@pytest.mark.parametrize(
    "network_error",
    [
        httpx2.ConnectError("offline"),
        httpx2.ReadTimeout("slow"),
        ConnectionResetError(errno.ECONNRESET, "reset"),
    ],
)
def test_before_send_drops_network_errors_even_when_wrapped(network_error):
    try:
        try:
            raise network_error
        except Exception as cause:
            raise RuntimeError("Failed to refresh the model catalog") from cause
    except RuntimeError as wrapped:
        hint = cast(Hint, {"exc_info": (RuntimeError, wrapped, None)})

    assert _before_send_cli(cast(Event, {}), hint) is None


def test_before_send_keeps_an_http_status_error():
    request = httpx2.Request("GET", "https://api.mistral.ai")
    error = httpx2.HTTPStatusError(
        "bad", request=request, response=httpx2.Response(500, request=request)
    )
    hint = cast(Hint, {"exc_info": (type(error), error, None)})

    assert _before_send_cli(cast(Event, {}), hint) is not None


@pytest.mark.asyncio
async def test_only_task_errors_nobody_handled_are_reported(sentry_events):
    assert _init(enabled=True)
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    report_unhandled_asyncio_errors(loop)

    async def fail(error: Exception) -> None:
        raise error

    try:
        await asyncio.gather(fail(FileNotFoundError("npx")), return_exceptions=True)
        with pytest.raises(PermissionError):
            await asyncio.create_task(fail(PermissionError("denied")))
        lost = asyncio.ensure_future(fail(RuntimeError("lost")))
        await asyncio.wait({lost})
        del lost
        gc.collect()
    finally:
        loop.set_exception_handler(previous)

    values = [
        cast(dict[str, Any], event)["exception"]["values"][-1]["value"]
        for event in sentry_events
    ]
    assert values == ["lost"]


def test_capture_during_the_sdk_first_import_is_a_no_op(monkeypatch):
    # Another thread is still importing the SDK: the module has no attributes yet.
    monkeypatch.setitem(sys.modules, "sentry_sdk", types.ModuleType("sentry_sdk"))

    capture_sentry_exception(RuntimeError("boom"), fatal=False)


def test_capture_never_imports_the_sdk_when_reporting_is_off(monkeypatch):
    monkeypatch.delitem(sys.modules, "sentry_sdk")

    capture_sentry_exception(RuntimeError("boom"), fatal=False)

    assert "sentry_sdk" not in sys.modules


def test_a_fingerprint_groups_the_event(sentry_events):
    assert _init(enabled=True)

    capture_sentry_exception(
        RuntimeError("boom in session 1"), fatal=False, fingerprint=["a", "b"]
    )

    assert cast(dict[str, Any], sentry_events[0])["fingerprint"] == ["a", "b"]


@pytest.mark.asyncio
async def test_tags_set_by_an_init_in_a_worker_thread_reach_every_event(sentry_events):
    enabled = await asyncio.to_thread(
        init_cli_sentry,
        enabled=True,
        headless=True,
        tags={"entrypoint": "cli", "client_name": "vibe_rs"},
    )

    # A context the init never ran in, like the app server's event loop.
    contextvars.Context().run(
        capture_sentry_exception, RuntimeError("boom"), fatal=False
    )

    assert enabled is True
    event = cast(dict[str, Any], sentry_events[0])
    assert event["server_name"] == "vibe-app-server"
    assert {
        key: event["tags"][key] for key in ("headless", "entrypoint", "client_name")
    } == {"headless": "true", "entrypoint": "cli", "client_name": "vibe_rs"}


def test_error_logs_are_not_reported_but_exceptions_are(sentry_events):
    assert _init(enabled=True)

    logging.getLogger("vibe.test").error("handled failure")
    capture_sentry_exception(RuntimeError("boom"), fatal=True)

    assert len(sentry_events) == 1
    exception = cast(dict[str, Any], sentry_events[0])["exception"]
    assert exception["values"][0]["value"] == "boom"


def test_vibe_acp_still_reports_error_logs_and_its_own_name(sentry_events):
    assert init_sentry(enabled=True, headless=True, tags={})

    logging.getLogger("vibe.test").error("handled failure")
    capture_cli_exception(RuntimeError("cli only"), tags={})

    assert [
        (event["server_name"], event["logentry"]["message"])
        for event in cast(list[dict[str, Any]], sentry_events)
    ] == [("vibe-acp", "handled failure")]


def test_a_handled_failure_is_captured_by_the_cli_only(sentry_events):
    assert _init(enabled=True)

    capture_cli_exception(RuntimeError("survived"), tags={"vibe_boundary": "x"})

    event = cast(dict[str, Any], sentry_events[0])
    assert event["server_name"] == "vibe-app-server"
    assert event["tags"]["vibe_boundary"] == "x"


def test_events_share_one_random_launch_id_as_user(sentry_events):
    assert _init(enabled=True)

    capture_sentry_exception(RuntimeError("first"), fatal=True)
    capture_sentry_exception(RuntimeError("second"), fatal=False)

    ids = {cast(dict[str, Any], event)["user"]["id"] for event in sentry_events}
    assert len(sentry_events) == 2
    assert len(ids) == 1
    assert uuid.UUID(ids.pop()).version == 4


def test_a_second_init_keeps_the_client_and_the_gate_turns_it_off(sentry_events):
    assert _init(enabled=True)
    client = sentry_sdk.get_client()

    assert _init(enabled=True)
    assert sentry_sdk.get_client() is client

    assert not _init(enabled=False)
    assert not sentry_sdk.is_initialized()
    capture_sentry_exception(RuntimeError("after opt-out"), fatal=True)
    assert sentry_events == []


@patch("sentry_sdk.init")
def test_init_sentry_stays_off_without_a_dsn(init_mock, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert not _init(enabled=True)
    init_mock.assert_not_called()


def test_the_app_server_reports_to_the_project_the_terminal_ui_handed(
    sentry_events, monkeypatch
):
    monkeypatch.setenv(CLI_CRASH_REPORT_DSN_ENV, "http://key@127.0.0.1:9/3")

    claim_cli_crash_report_dsn()

    assert CLI_CRASH_REPORT_DSN_ENV not in os.environ
    assert _init(enabled=True)
    assert sentry_sdk.get_client().dsn == "http://key@127.0.0.1:9/3"


@patch("sentry_sdk.init")
def test_the_app_server_stays_off_when_no_terminal_ui_handed_a_dsn(
    init_mock, monkeypatch
):
    monkeypatch.setattr(
        "vibe.observability.sentry._ACP_SENTRY_DSN", "http://key@127.0.0.1:9/1"
    )
    monkeypatch.setattr("vibe.observability.sentry._cli_dsn", "stale")
    monkeypatch.delenv(CLI_CRASH_REPORT_DSN_ENV, raising=False)

    claim_cli_crash_report_dsn()

    assert not cli_crash_reporting_available()
    assert not _init(enabled=True)
    init_mock.assert_not_called()


@patch("sentry_sdk.init")
def test_init_sentry_stays_disabled(init_mock):
    enabled = _init(enabled=False)

    assert enabled is False
    init_mock.assert_not_called()


def _init(*, enabled: bool) -> bool:
    return init_cli_sentry(enabled=enabled, headless=True, tags={})
