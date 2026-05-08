from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import IntEnum
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.executor import HookExecutor
from vibe.core.hooks.models import (
    HookEndEvent,
    HookInvocation,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookStartEvent,
    HookType,
    HookUserMessage,
)
from vibe.core.types import BaseEvent

if TYPE_CHECKING:
    from vibe.core.session.session_logger import SessionLogger

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


class HookExitCode(IntEnum):
    SUCCESS = 0
    RETRY = 2


class HookRetryState:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def reset(self) -> None:
        self._counts.clear()

    def remaining_retries(self, hook_name: str) -> int:
        return _MAX_RETRIES - self._counts.get(hook_name, 0)

    def track_retry(self, hook_name: str) -> None:
        self._counts[hook_name] = self._counts.get(hook_name, 0) + 1

    def track_success(self, hook_name: str) -> None:
        self._counts.pop(hook_name, None)

    def should_retry(self, hook_name: str) -> bool:
        return self._counts.get(hook_name, 0) < _MAX_RETRIES


class HooksManager:
    def __init__(self, hooks: list[HookConfig]) -> None:
        self._hooks_by_type: dict[HookType, list[HookConfig]] = {}
        for hook in hooks:
            self._hooks_by_type.setdefault(hook.type, []).append(hook)
        self._executor = HookExecutor()
        self._retry_state = HookRetryState()

    def has_hooks(self, hook_type: HookType) -> bool:
        return bool(self._hooks_by_type.get(hook_type))

    def reset_retry_count(self) -> None:
        self._retry_state.reset()

    async def run(
        self, hook_type: HookType, session_id: str, session_logger: SessionLogger
    ) -> AsyncGenerator[BaseEvent | HookUserMessage]:
        hooks = self._hooks_by_type.get(hook_type, [])
        if not hooks:
            return
        invocation = _build_invocation(hook_type, session_id, session_logger)

        yield HookRunStartEvent()
        for hook in hooks:
            yield HookStartEvent(hook_name=hook.name)
            result = await self._executor.run(hook, invocation)

            if result.timed_out or result.exit_code is None:
                yield HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=f"Timed out after {hook.timeout}s",
                )
            elif result.exit_code == HookExitCode.SUCCESS:
                yield HookEndEvent(hook_name=hook.name, status=HookMessageSeverity.OK)
            elif result.exit_code == HookExitCode.RETRY and result.stdout:
                logger.debug("Hook %s retry output: %s", hook.name, result.stdout)

                if not self._retry_state.should_retry(hook.name):
                    yield HookEndEvent(
                        hook_name=hook.name,
                        status=HookMessageSeverity.ERROR,
                        content=f"Failed, retries exhausted ({_MAX_RETRIES}/{_MAX_RETRIES})",
                    )
                    continue

                remaining = self._retry_state.remaining_retries(hook.name)
                self._retry_state.track_retry(hook.name)
                yield HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.ERROR,
                    content=f"Failed, retrying ({remaining} {'retry' if remaining == 1 else 'retries'} remaining)",
                )
                yield HookUserMessage(content=result.stdout)
                break
            else:
                yield HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=(
                        result.stdout
                        or result.stderr
                        or f"Exited with code {result.exit_code}"
                    ),
                )

            if result.exit_code != HookExitCode.RETRY:
                self._retry_state.track_success(hook.name)

        yield HookRunEndEvent()


def _build_invocation(
    hook_type: HookType, session_id: str, session_logger: SessionLogger
) -> HookInvocation:
    transcript_path = ""
    if session_logger.enabled and session_logger.session_dir is not None:
        transcript_path = str(session_logger.messages_filepath.resolve())

    return HookInvocation(
        session_id=session_id,
        transcript_path=transcript_path,
        cwd=str(Path.cwd().resolve()),
        hook_event_name=hook_type.value,
    )
