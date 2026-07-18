from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum
import json
from string import Template
from typing import TYPE_CHECKING, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from vibe.core.config import AnyVibeConfig, ModelConfig, resolve_api_key
from vibe.core.llm.backend.factory import create_backend
from vibe.core.llm.types import BackendLike
from vibe.core.logger import logger
from vibe.core.prompts import UtilityPrompt
from vibe.core.types import Backend, LLMMessage, Role
from vibe.core.utils.http import get_user_agent

if TYPE_CHECKING:
    from vibe.core.config.models import AutoModeConfig
    from vibe.core.tools.permissions import RequiredPermission

# mistral-small returns inconsistent verdicts for identical commands often enough
# to matter; a permission gate that changes its mind is worse than a slower one.
CLASSIFIER_MODEL = ModelConfig(
    name="mistral-medium-latest",
    provider="mistral",
    alias="mistral-medium",
    input_price=0.4,
    output_price=2.0,
)

CLASSIFIER_MAX_TOKENS = 256
CLASSIFIER_TIMEOUT_SECONDS = 20.0
MAX_SERIALIZED_ARGS_CHARS = 4000


class ClassifierVerdict(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class ClassifierDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect: StrictStr
    deny_rule: StrictStr | None
    deny_tier: Literal["hard_deny", "soft_deny"] | None
    user_authorized: StrictBool
    scope_ok: StrictBool
    verdict: ClassifierVerdict
    reason: StrictStr

    @model_validator(mode="after")
    def validate_deny_evidence(self) -> ClassifierDecision:
        if (self.deny_rule is None) != (self.deny_tier is None):
            raise ValueError("deny_rule and deny_tier must either both be set or null")
        return self


def _render_rules(rules: Sequence[str]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def _serialize_args(args: BaseModel) -> str | None:
    # Truncating would hand the classifier a harmless prefix while a dangerous
    # suffix goes unseen, so oversized arguments get no verdict at all.
    serialized = args.model_dump_json()
    if len(serialized) > MAX_SERIALIZED_ARGS_CHARS:
        return None
    return serialized


def _transcript_is_clean(transcript: Sequence[LLMMessage]) -> bool:
    # Only genuine human messages may establish intent. Callers filter system,
    # injected, and tool content, but the service boundary enforces provenance too.
    return not any(
        m.role not in {Role.user, Role.assistant} or m.injected or m.tool_calls
        for m in transcript
    )


def _describe_permissions(required: Sequence[RequiredPermission]) -> str:
    if not required:
        return "(none reported)"
    return "\n".join(f"- {rp.scope}: {rp.label}" for rp in required)


def _parse_decision(raw: str) -> ClassifierDecision | None:
    text = raw.strip()
    if not text or "\n" in text or "\r" in text:
        return None
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ClassifierDecision.model_validate(payload)
    except ValidationError:
        return None


def _enforce_dangerous_action_approval(
    decision: ClassifierDecision,
) -> ClassifierDecision:
    if decision.deny_rule is None or decision.verdict is ClassifierVerdict.BLOCK:
        return decision
    return decision.model_copy(
        update={
            "verdict": ClassifierVerdict.BLOCK,
            "reason": (
                "A matched dangerous-action rule requires explicit tool approval: "
                f"{decision.deny_rule}"
            ),
        }
    )


class PermissionClassifier:
    def __init__(self, backend: BackendLike, model: ModelConfig) -> None:
        self._backend = backend
        self._model = model
        self._prompt_template = UtilityPrompt.PERMISSION_CLASSIFIER.read()

    async def aclose(self) -> None:
        await self._backend.__aexit__(None, None, None)

    def _system_prompt(self, auto_mode: AutoModeConfig) -> str:
        # Config appends to the built-in defaults; it cannot remove one.
        return Template(self._prompt_template).safe_substitute(
            hard_deny=_render_rules(auto_mode.hard_deny),
            soft_deny=_render_rules(auto_mode.soft_deny),
            allow=_render_rules(auto_mode.allow),
            environment=_render_rules(auto_mode.environment),
        )

    async def classify(
        self,
        *,
        auto_mode: AutoModeConfig,
        tool_name: str,
        args: BaseModel,
        required_permissions: Sequence[RequiredPermission],
        transcript: Sequence[LLMMessage],
        metadata: dict[str, str] | None = None,
    ) -> ClassifierDecision | None:
        if (serialized_args := _serialize_args(args)) is None:
            logger.warning(
                "Permission classifier skipped: arguments too large for tool=%s",
                tool_name,
            )
            return None
        if not _transcript_is_clean(transcript):
            logger.error(
                "Permission classifier refused an untrusted transcript for tool=%s",
                tool_name,
            )
            return None

        pending = (
            "# Pending tool call\n\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {serialized_args}\n\n"
            "Requires approval because:\n"
            f"{_describe_permissions(required_permissions)}\n\n"
            "Respond with the single-line JSON verdict."
        )
        messages = [
            LLMMessage(role=Role.system, content=self._system_prompt(auto_mode)),
            *transcript,
            LLMMessage(role=Role.user, content=pending),
        ]
        try:
            async with asyncio.timeout(CLASSIFIER_TIMEOUT_SECONDS):
                result = await self._backend.complete(
                    model=self._model,
                    messages=messages,
                    temperature=0.0,
                    tools=None,
                    tool_choice=None,
                    max_tokens=CLASSIFIER_MAX_TOKENS,
                    extra_headers={"user-agent": get_user_agent(Backend.MISTRAL)},
                    metadata=metadata,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Permission classifier call failed for tool=%s",
                tool_name,
                exc_info=True,
            )
            return None

        decision = _parse_decision(result.message.content or "")
        if decision is None:
            logger.warning(
                "Permission classifier returned an unparseable verdict for tool=%s",
                tool_name,
            )
            return None
        return _enforce_dangerous_action_approval(decision)


def create_permission_classifier(config: AnyVibeConfig) -> PermissionClassifier | None:
    model = config.auto_mode.classifier_model or CLASSIFIER_MODEL
    try:
        provider = config.get_provider_for_model(model)
    except ValueError:
        logger.warning(
            "Permission classifier unavailable: no provider for model=%s", model.alias
        )
        return None
    if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
        logger.warning(
            "Permission classifier unavailable: missing API key for provider=%s",
            provider.name,
        )
        return None
    backend = create_backend(
        provider=provider,
        timeout=config.api_timeout,
        retry_max_elapsed_time=config.api_retry_max_elapsed_time,
    )
    return PermissionClassifier(backend, model)
