from __future__ import annotations

from vibe.app_server.protocol import NarrationSummarizeParams
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig
from vibe.core.llm.backend.factory import create_backend
from vibe.core.prompts import UtilityPrompt
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.types import Backend, LLMMessage, Role
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_user_agent

_NARRATION_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)


class NarrationService:
    def __init__(self, agent_loop: AgentLoop) -> None:
        self._agent_loop = agent_loop

    async def summarize(self, params: NarrationSummarizeParams) -> str | None:
        provider = next(
            (
                provider
                for provider in self._agent_loop.config.providers
                if provider.name == _NARRATION_MODEL.provider
            ),
            None,
        )
        if provider is None:
            return None
        if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
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
            launch_context=self._agent_loop.launch_context,
            session_id=self._agent_loop.session_id,
            parent_session_id=self._agent_loop.parent_session_id,
            call_type="secondary_call",
            message_id=params.message_id,
            user_plan=self._agent_loop.user_plan,
        ).model_dump(exclude_none=True)
        backend = create_backend(
            provider=provider,
            timeout=self._agent_loop.config.api_timeout,
            retry_max_elapsed_time=(self._agent_loop.config.api_retry_max_elapsed_time),
        )
        try:
            async with backend:
                result = await backend.complete(
                    model=_NARRATION_MODEL,
                    messages=messages,
                    temperature=0.0,
                    tools=None,
                    tool_choice=None,
                    max_tokens=512,
                    extra_headers={"user-agent": get_user_agent(Backend.MISTRAL)},
                    metadata=metadata,
                )
        except Exception as exc:
            logger.warning("Turn summary generation failed", exc_info=exc)
            return None
        return result.message.content or ""
