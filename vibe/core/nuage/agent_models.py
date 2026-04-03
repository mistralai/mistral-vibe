from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_SUBMIT_INPUT_UPDATE_NAME = "__submit_input"


class AgentCompletionState(BaseModel):
    content: str = ""
    reasoning_content: str = ""


class InterruptSignal(BaseModel):
    prompt: str


class ChatInputModel(BaseModel):
    model_config = ConfigDict(title="ChatInput", extra="forbid")
    message: list[Any]


class SubmitInputModel(BaseModel):
    task_id: str
    input: Any
