"""UTF-8 input selection keeps composer byte offsets on character boundaries."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_SELECT = "\x1b[<0;4;36M\x1b[<32;11;36M\x1b[<0;11;36m"

timeline: Timeline = ["café 😀 naïve", _SELECT]
