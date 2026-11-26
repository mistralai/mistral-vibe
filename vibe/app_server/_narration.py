from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe.app_server.protocol import NarrationSummarizeParams
from vibe.core.config import ModelConfig, VibeConfigSchema
from vibe.core.llm.completion import CompletionTransport, complete_once
from vibe.core.prompts import UtilityPrompt
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.types import LaunchContext
from vibe.core.types import LLMMessage, Role
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import ProviderCredentialProvider

_NARRATION_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)


@dataclass(frozen=True, slots=True)
class NarrationContext:
    config: VibeConfigSchema
    launch_context: LaunchContext | None
    parent_session_id: str | None
    user_plan: str | None
    # The session's credential for a provider by name; None reads the API key.
    credentials: Callable[[str], ProviderCredentialProvider] | None = None


class NarrationService:
    def __init__(self, context_getter: Callable[[], NarrationContext]) -> None:
        self._context_getter = context_getter

    async def summarize(self, params: NarrationSummarizeParams) -> str | None:
        context = self._context_getter()
        config = context.config
        provider = next(
            (
                provider
                for provider in config.providers
                if provider.name == _NARRATION_MODEL.provider
            ),
            None,
        )
        if provider is None:
            return None
        credentials = (
            context.credentials(provider.name) if context.credentials else None
        )
        if (
            credentials is None
            and provider.api_key_env_var
            and not resolve_api_key(provider.api_key_env_var)
        ):
            return None

        sections = [f"## User Request\n{params.user_message}"]
        if params.assistant_text:
            sections.append(f"## Assistant Response\n{params.assistant_text}")
        if params.error:
            sections.append(f"## Error\n{params.error}")
        messages = [
            LLMMessage(role=Role.system, content=UtilityPrompt.TURN_SUMMARY.read()),
            LLMMessage(role=Role.user, content="\n\n".join(sections)),
        ]
        metadata = build_request_metadata(
            launch_context=context.launch_context,
            session_id=params.session_id,
            parent_session_id=context.parent_session_id,
            call_type="secondary_call",
            message_id=params.message_id,
            user_plan=context.user_plan,
        ).model_dump(exclude_none=True)
        try:
            result = await complete_once(
                provider=provider,
                model=_NARRATION_MODEL,
                messages=messages,
                temperature=0.0,
                max_tokens=512,
                metadata=metadata,
                transport=CompletionTransport.from_config(config),
                credentials=credentials,
            )
        except Exception as exc:
            logger.warning("Turn summary generation failed", exc_info=exc)
            return None
        return result.text
