from __future__ import annotations

from typing import Any, cast

from sentry_sdk.types import Event, Hint

from vibe.core.sentry import SentryTarget, _before_send


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
