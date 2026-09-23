"""Double- and triple-clicking remote-project text selects a word, then its row."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "Vibe Code project"
capture_steps = {1, 2, 3}
_CLICK = "\x1b[<0;8;22M\x1b[<0;8;22m"
_COMMAND_C = "\x1b[99;9u"

timeline: Timeline = ["/remote-project\r", _CLICK * 2, _CLICK, _COMMAND_C]
