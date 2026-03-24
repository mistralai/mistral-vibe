"""Lifecycle hook manager for Vibe.

Fires shell commands at key lifecycle events, passing structured JSON on stdin.
Hook commands run asynchronously and never block the agent loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from vibe.core.config._settings import HooksConfig

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_SECONDS = 30


class HookManager:
    """Manages lifecycle hook execution.

    Created in AgentLoop.__init__() with session config, session_id, and cwd.
    Public emit_*() methods are called at lifecycle points in AgentLoop.
    They enqueue hook subprocesses without awaiting. ``drain()`` is called at
    turn end to wait for all pending hook tasks to complete.
    """

    def __init__(self, config: HooksConfig, session_id: str, cwd: str) -> None:
        self._config = config
        self._session_id = session_id
        self._cwd = cwd
        self._tasks: set[asyncio.Task[None]] = set()

    def _base_payload(self, event_name: str) -> dict[str, Any]:
        return {
            "hook_event_name": event_name,
            "cwd": self._cwd,
            "session_id": self._session_id,
        }

    def _emit(self, event_name: str, extra: dict[str, Any] | None = None) -> None:
        """Enqueue all hooks registered for the given event."""
        hooks: list[Any] = getattr(self._config, event_name, [])
        if not hooks:
            return
        payload = self._base_payload(event_name)
        if extra:
            payload.update(extra)
        try:
            payload_bytes = json.dumps(payload, default=str).encode()
        except Exception:
            logger.exception("Failed to serialize hook payload for %r", event_name)
            return

        for hook in hooks:
            task = asyncio.create_task(self._run_hook(hook.command, payload_bytes))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_hook(self, command: str, payload: bytes) -> None:
        """Execute a single hook command as an async subprocess."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(input=payload), timeout=HOOK_TIMEOUT_SECONDS
                )
                if stderr:
                    logger.debug(
                        "Hook %r stderr: %s", command, stderr.decode(errors="replace")
                    )
                if proc.returncode != 0:
                    logger.warning(
                        "Hook %r exited with code %d", command, proc.returncode
                    )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning(
                    "Hook %r timed out after %ds, killed", command, HOOK_TIMEOUT_SECONDS
                )
        except Exception:
            logger.exception("Failed to run hook %r", command)

    # ── Public API (called from AgentLoop) ──

    def emit_session_start(self) -> None:
        self._emit("session_start")

    def emit_user_prompt_submit(self, prompt: str) -> None:
        self._emit("user_prompt_submit", {"prompt": prompt})

    def emit_pre_tool_use(self, tool_name: str, tool_input: Any) -> None:
        self._emit("pre_tool_use", {"tool_name": tool_name, "tool_input": tool_input})

    def emit_post_tool_use(
        self,
        tool_name: str,
        tool_input: Any,
        tool_outcome: str,
        tool_response: Any | None = None,
        tool_error: str | None = None,
    ) -> None:
        self._emit(
            "post_tool_use",
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_outcome": tool_outcome,
                "tool_response": tool_response,
                "tool_error": tool_error,
            },
        )

    def emit_turn_end(self) -> None:
        self._emit("turn_end")

    async def drain(self) -> None:
        """Wait for all pending hook tasks to complete."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
