"""A folder pasted or dropped after `/cd ` stays a path, shell escapes and all.

Terminals paste a dropped folder shell-escaped (`My\\ Folder`); it must reach
`session/relocate` as that folder, not as an `@` mention the model reads.
"""

from __future__ import annotations

from pathlib import Path

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

# Outside `ROOT`, so the completion goldens of the other `/cd` scenarios keep
# their folder list.
_TARGET = Path("/tmp/vibe_e2e_fixtures/cd_paste/My Folder")
_TARGET.mkdir(parents=True, exist_ok=True)
_ESCAPED = str(_TARGET).replace(" ", "\\ ")

handshake = {
    "session/relocate": [{"state": handshake()["session/relocate"][0]["state"]}]
}
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_excludes = ("Not a directory", "@/tmp")

timeline: Timeline = ["/cd ", paste(_ESCAPED), "\r"]
