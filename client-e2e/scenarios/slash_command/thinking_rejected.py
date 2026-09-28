from __future__ import annotations

from e2e.app_server.scenario import Timeline

# config/write rejects the thinking mutation in-band (a successful JSON-RPC
# result with rejected=true); the client must surface the failure instead of
# reporting success (Python _run_settings_update mounts an ErrorMessage).
handshake = {"config/write": {"rejected": True, "failures": []}}

timeline: Timeline = ["/thinking\r", "j\r"]
