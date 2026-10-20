from __future__ import annotations

from vibe.config_values import (
    AUTO_THEME as AUTO_THEME,
    DEFAULT_LOG_LEVEL as DEFAULT_LOG_LEVEL,
    DEFAULT_THEME as DEFAULT_THEME,
    FALLBACK_THEME as FALLBACK_THEME,
)

DEFAULT_MISTRAL_API_ENV_KEY = "MISTRAL_API_KEY"
DEFAULT_CONSOLE_BASE_URL = "https://console.mistral.ai"
DEFAULT_VIBE_BASE_URL = "https://chat.mistral.ai"
DEFAULT_MISTRAL_SERVER_URL = "https://api.mistral.ai"
DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL = DEFAULT_CONSOLE_BASE_URL
DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL = f"{DEFAULT_CONSOLE_BASE_URL}/api"
DEFAULT_API_TIMEOUT = 720.0
DEFAULT_API_RETRY_MAX_ELAPSED_TIME = 300.0
# Off by default: Mistral sends a tool call in one piece once it is fully
# generated, so a large tool call is a silence no fixed limit can tell from a
# stalled stream.
DEFAULT_API_STREAM_IDLE_TIMEOUT = 0.0
DEFAULT_API_CONNECT_TIMEOUT = 10.0
DEFAULT_API_WRITE_TIMEOUT = 30.0
DEFAULT_API_POOL_TIMEOUT = 10.0
DEFAULT_AUTO_COMPACT_THRESHOLD = 200_000
# A summary needs a fraction of a long session's output budget. Left at the
# model's own cap, a compaction request once reasoned for 112k tokens.
DEFAULT_COMPACTION_MAX_OUTPUT_TOKENS = 80_000
# Past this much reasoning, the in-context compaction request is abandoned for
# the standalone one, which runs without reasoning.
DEFAULT_COMPACTION_REASONING_LIMIT_TOKENS = 32_000
# Answers cut off at the output limit that a turn discards and asks again for.
DEFAULT_OUTPUT_LIMIT_RETRIES = 6
# Tokens kept free when a request's output cap is clipped to the room its
# input leaves in the model's context window.
DEFAULT_OUTPUT_TOKEN_RESERVE = 4_096
# Compaction is itself a model call carrying the conversation, so it needs room
# to run: triggering at the true ceiling would fail exactly when it is needed.
AUTO_COMPACT_WINDOW_RATIO = 0.8
# "No layer set a threshold". The default layer materializes field defaults
# into the merge, so unset-ness must survive it as a value; TOML cannot express
# it, so only the default ever produces it. A -1 written by hand is therefore
# indistinguishable from unset and resolves the same way; 0 stays the
# documented way to disable compaction.
UNSET_AUTO_COMPACT_THRESHOLD = -1
