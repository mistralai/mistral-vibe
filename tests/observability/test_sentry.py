from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

from sentry_sdk.types import Event, Hint

from vibe.observability.sentry import SentryTarget, _before_send, init_sentry


def _send(event: dict[str, Any]) -> dict[str, Any]:
    result = _before_send(cast(Event, event), cast(Hint, {}))
    assert result is not None
    return cast(dict[str, Any], result)


def test_before_send_drops_ip_but_keeps_geo():
    event = {"user": {"id": "abc", "geo": {"city": "Ulm"}, "ip_address": "1.2.3.4"}}
    result = _send(event)
    assert result["user"] == {"id": "abc", "geo": {"city": "Ulm"}}


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


def test_sentry_target_maps_to_distinct_project_server_names():
    assert SentryTarget.CLI.server_name == "vibe-cli"
    assert SentryTarget.ACP.server_name == "vibe-acp"


@patch("sentry_sdk.set_tag")
@patch("sentry_sdk.is_initialized", return_value=True)
@patch("sentry_sdk.init")
def test_init_sentry_uses_primitive_settings_and_tags(
    init_mock, _is_initialized_mock, set_tag_mock
):
    enabled = init_sentry(
        enabled=True,
        headless=True,
        tags={"entrypoint": "acp", "client_name": "vibe_acp"},
        target=SentryTarget.ACP,
    )

    assert enabled is True
    assert init_mock.call_args.kwargs["server_name"] == "vibe-acp"
    set_tag_mock.assert_any_call("headless", "true")
    set_tag_mock.assert_any_call("entrypoint", "acp")
    set_tag_mock.assert_any_call("client_name", "vibe_acp")


@patch("sentry_sdk.init")
def test_init_sentry_stays_disabled(init_mock):
    enabled = init_sentry(enabled=False, headless=False, tags={})

    assert enabled is False
    init_mock.assert_not_called()
