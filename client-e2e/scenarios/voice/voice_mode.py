from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Isolate the missing transcription key from the valid main-provider key.
_NO_KEY_PROVIDER = {
    "transcription": {"provider": {"apiKeyEnvVar": "VIBE_VOICE_NO_KEY"}}
}
handshake = {
    "config/read": {"config": _NO_KEY_PROVIDER},
    "runtime/read": {"runtime": {"config": _NO_KEY_PROVIDER}},
}

# Ctrl+T (0x14) toggles recording; with no resolvable key it errors before any audio.
timeline: Timeline = ["\x14"]
