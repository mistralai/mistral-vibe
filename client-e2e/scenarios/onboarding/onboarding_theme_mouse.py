"""`--setup` theme screen: click-to-select and draggable preview scrollbar."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}

# SGR coordinates are 1-based. The theme list centers in columns 46-75; the
# row just above the selected box is `selected - 1`. The preview pane starts at
# column 26 and its scrollbar gutter sits in columns 93-94, thumb at the top
# while the preview is unscrolled.
_SELECT_PREVIOUS = "\x1b[<0;60;7M\x1b[<0;60;7m"
_DRAG_PREVIEW = "\x1b[<0;93;17M\x1b[<32;93;35M\x1b[<0;93;35m"

timeline: Timeline = ["\r", _SELECT_PREVIOUS, _DRAG_PREVIEW]
