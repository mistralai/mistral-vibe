from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum, auto
import json
from string import Template
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

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
    ALLOW = auto()
    BLOCK = auto()


class ClassifierDecision(BaseModel):
    # The prompt asks for extra fields (effect, soft_deny_rule, user_authorized) to
    # steer the model's reasoning. Only the verdict is used.
    model_config = ConfigDict(extra="ignore")

    verdict: ClassifierVerdict
    reason: str


def _render_rules(rules: Sequence[str]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def _serialize_args(args: BaseModel) -> str:
    serialized = args.model_dump_json()
    if len(serialized) <= MAX_SERIALIZED_ARGS_CHARS:
        return serialized
    return f"{serialized[:MAX_SERIALIZED_ARGS_CHARS]}… [truncated]"


def _describe_permissions(required: Sequence[RequiredPermission]) -> str:
    if not required:
        return "(none reported)"
    return "\n".join(f"- {rp.scope}: {rp.label}" for rp in required)


def _parse_decision(raw: str) -> ClassifierDecision | None:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        payload: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    verdict = payload.get("verdict")
    if isinstance(verdict, str):
        payload["verdict"] = verdict.strip().lower()
    try:
        return ClassifierDecision.model_validate(payload)
    except ValidationError:
        return None


class PermissionClassifier:
    def __init__(self, backend: BackendLike, model: ModelConfig) -> None:
        self._backend = backend
        self._model = model

    def _system_prompt(self, auto_mode: AutoModeConfig) -> str:
        # Config appends to the built-in defaults; it cannot remove one.
        return Template(UtilityPrompt.PERMISSION_CLASSIFIER.read()).safe_substitute(
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
        pending = (
            "# Pending tool call\n\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {_serialize_args(args)}\n\n"
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
        return decision


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
