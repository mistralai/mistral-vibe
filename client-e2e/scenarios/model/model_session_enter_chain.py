from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /model, submit, navigate down, `s` selects model for this session only, then
# the thinking picker chains. Enter also applies for this session only (the
# model was session-only, so thinking is necessarily session-only too).
timeline: Timeline = ["/model", "\r", "\x1b[B", "s", "\r"]
