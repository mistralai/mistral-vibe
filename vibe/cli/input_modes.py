from __future__ import annotations

from typing import Literal

# The single source of truth for chat-input modes. `>` is the default prompt
# mode; the rest are entered by starting a line with their prefix character
# (slash-command, bash, teleport).
InputMode = Literal["!", "/", ">", "&"]
DEFAULT_MODE: Literal[">"] = ">"
