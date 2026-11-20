from __future__ import annotations

from e2e.app_server.scenario import Timeline

# A level the model does not offer writes nothing and lists the offered ones.
timeline: Timeline = ["/thinking turbo\r"]

screen_contains = ("Unknown thinking level: turbo. Use one of: off, medium, high",)
request_methods = {"config/model/write"}
