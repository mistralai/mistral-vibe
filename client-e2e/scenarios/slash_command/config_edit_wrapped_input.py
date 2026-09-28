"""Move the cursor through soft-wrapped config input rows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

value = "connector_github.retrieve_pull_request_information"
expected = "connector_githuXb.retrieve_pull_request_informationY"
handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "disabled_tools",
                "kind": "list",
                "value": [value],
                "description": "Disable one tool per line.",
                "path": "/disabled_tools",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "user-toml", "value": [value]}],
            }
        ],
        "targets": ["user-toml", "overrides"],
    }
}

capture_startup = False
capture_steps = {3}
screen_contains = {"rust": ("connector_githuXb.retrieve_pull_req", "informationY")}
timeline: Timeline = ["/config\r", "\r", "\x1b[AX", "\x1b[BY", "\x13"]
