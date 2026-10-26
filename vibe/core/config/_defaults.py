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
# The same limit as every other host of the harness. 0 turns it off, for a
# provider that sends a large tool call in one piece once it is fully generated.
DEFAULT_API_STREAM_IDLE_TIMEOUT = 300.0
DEFAULT_API_CONNECT_TIMEOUT = 10.0
DEFAULT_API_WRITE_TIMEOUT = 30.0
DEFAULT_API_POOL_TIMEOUT = 10.0
# Past this much reasoning, the in-context compaction request is abandoned for
# the standalone one, which runs without reasoning.
DEFAULT_COMPACTION_REASONING_LIMIT_TOKENS = 32_000
# Answers cut off at the output limit that a turn discards and asks again for.
DEFAULT_OUTPUT_LIMIT_RETRIES = 6

SHELL_TOOLS = ("bash", "git_bash", "powershell")
"""The command tools whose `default_timeout` and `max_timeout_seconds` set the
unified harness's bash timeouts."""
