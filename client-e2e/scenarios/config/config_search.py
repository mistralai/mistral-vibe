"""Config lists take j/k, `/` searches, and Esc leaves search, clears it, then closes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/config\r", "jjk", "/jk", "\x7f\x7fmax", "\x1b", "\x1b", "\x1b"]

screen_excludes = {"rust": ("Search settings",)}
