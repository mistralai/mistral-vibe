from __future__ import annotations

from e2e.app_server.scenario import Timeline

# `/thinking <level>` saves the level at once, matched case-insensitively, without opening the picker.
timeline: Timeline = ["/thinking HIGH\r"]

screen_contains = ("Applied: thinking high.",)
screen_excludes = ("Select thinking level",)
request_methods = {"config/model/write"}
