"""Wrap Unicode choices safely and scroll the selected visual rows into view."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "compaction_prompt_id",
                "kind": "enum",
                "value": "0",
                "description": "Choose a compaction prompt.",
                "path": "/compaction_prompt_id",
                "popular": True,
                "enumChoices": ["0", "1", "2", "3", "4", "5", "6", "7"],
                "valueLabels": {
                    "0": "éééééééééééééééééééééééééééééééééééééé",
                    "1": "Model 1 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "2": "Model 2 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "3": "Model 3 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "4": "Model 4 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "5": "Model 5 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "6": "Model 6 with a long display "
                    "label that wraps across "
                    "multiple rows",
                    "7": "Model 7 with a long display "
                    "label that wraps across "
                    "multiple rows",
                },
                "layerValues": [{"layer": "default", "value": "0"}],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {"rust": ("Model 7", "multiple rows", "Enter Select")}
timeline: Timeline = ["/config\r", "\r", "\x1b[B" * 7, resize(20, 100)]
