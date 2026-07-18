from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibe.core.watchdog.fingerprint import fingerprint_call
from vibe.core.watchdog.models import RecoveryStrategy

_DENIED_COMMAND_FRAGMENTS = (
    "rm -rf",
    "git reset --hard",
    "git clean -f",
    "mkfs",
    "shutdown",
    "sudo ",
    "dd if=",
)


class RecoveryAdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: RecoveryStrategy
    objective: str
    detector: str
    evidence: tuple[str, ...]
    attempted_strategies: tuple[RecoveryStrategy, ...]
    available_tools: tuple[str, ...] = ()
    failed_tool: str | None = None
    failed_action_fingerprint: str | None = None


class RecoveryAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: RecoveryStrategy
    reason: str = Field(min_length=1, max_length=500)
    tool: str | None = Field(default=None, max_length=200)
    command: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_action(self) -> RecoveryAdvice:
        if self.strategy == RecoveryStrategy.REWRITE_COMMAND and not self.command:
            raise ValueError("rewrite_command advice requires command")
        if self.strategy == RecoveryStrategy.ALTERNATE_TOOL and not self.tool:
            raise ValueError("alternate_tool advice requires tool")
        if self.strategy == RecoveryStrategy.LLM_RECOVERY and not (
            self.tool or self.command
        ):
            raise ValueError("llm_recovery advice requires tool or command")
        return self


class RecoveryAdvisor(Protocol):
    async def advise(self, request: RecoveryAdviceRequest) -> RecoveryAdvice: ...


def parse_recovery_advice(content: str) -> RecoveryAdvice:
    normalized = content.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        normalized = normalized[3:-3].strip()
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()
    return RecoveryAdvice.model_validate_json(normalized)


def validate_recovery_advice(
    advice: RecoveryAdvice, request: RecoveryAdviceRequest
) -> None:
    if advice.strategy != request.strategy:
        raise ValueError("recovery advisor returned the wrong strategy")
    if (
        advice.tool
        and request.available_tools
        and advice.tool not in request.available_tools
    ):
        raise ValueError("recovery advisor returned an unavailable tool")
    if (
        advice.strategy == RecoveryStrategy.ALTERNATE_TOOL
        and advice.tool == request.failed_tool
    ):
        raise ValueError("alternate tool must differ from the failed tool")
    if advice.command:
        normalized = " ".join(advice.command.casefold().split())
        if any(fragment in normalized for fragment in _DENIED_COMMAND_FRAGMENTS):
            raise ValueError("recovery advisor returned a destructive command")
        tool = advice.tool or request.failed_tool
        if (
            tool
            and request.failed_action_fingerprint
            and fingerprint_call(tool, {"cmd": advice.command})
            == request.failed_action_fingerprint
        ):
            raise ValueError("rewritten command must differ from the failed action")
