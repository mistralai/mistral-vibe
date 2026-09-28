"""Drag the config choice scrollbar to reveal models beyond the first viewport."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

models = [
    {
        "name": f"model-{index}",
        "alias": f"model-{index}",
        "thinking": "off",
        "supportsImages": False,
        "displayName": f"Model {index}",
    }
    for index in range(30)
]
handshake = {
    "config/read": {"config": {"models": models}},
    "runtime/read": {"runtime": {"config": {"models": models}}},
}

capture_startup = False
screen_contains = {"rust": ("Model 28",)}
screen_excludes = {"rust": ("Model 29", "default (currently")}
timeline: Timeline = [
    "/config\r",
    "\r",
    "\x1b[<0;59;11M",
    "\x1b[<32;59;39M",
    "\x1b[<0;59;39m",
    "\x1b[<65;40;18M",
    "\x1b[<64;40;18M",
]
