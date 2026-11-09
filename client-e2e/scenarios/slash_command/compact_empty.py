from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /compact on a fresh session: the empty-history guard fires before any RPC,
# so no session/compact request is ever sent (Python checks app_server.history).
timeline: Timeline = ["/compact\r"]
