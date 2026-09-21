from __future__ import annotations

from vibe.core.config.patch import escape_json_pointer_token

ACTIVE_MODEL_PATH = "/active_model"


def model_thinking_path(alias: str) -> str:
    return f"/models/{escape_json_pointer_token(alias)}/thinking"
