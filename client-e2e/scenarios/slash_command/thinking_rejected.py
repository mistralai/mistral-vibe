from __future__ import annotations

from e2e.app_server.scenario import Timeline

# config/model/write rejects the thinking mutation in-band (a successful JSON-RPC
# result with rejected=true); the client must surface the failure instead of
# reporting success.
handshake = {"config/model/write": {"rejected": True, "failures": []}}

timeline: Timeline = ["/thinking\r", "j\r"]
