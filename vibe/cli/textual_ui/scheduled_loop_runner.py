from __future__ import annotations

from collections.abc import Callable
import time
from typing import Protocol

from textual.widget import Widget

from vibe.app_server.models import ScheduledLoop
from vibe.app_server.protocol import AppServerResponseError
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage

_USAGE_HINT = """\
Usage:
  /loop <interval> <prompt>
  /loop list
  /loop cancel <id|all>
"""


class LoopsClient(Protocol):
    async def list(self) -> list[ScheduledLoop]: ...

    async def create(self, interval: str, prompt: str) -> ScheduledLoop: ...

    async def delete(self, loop_id: str) -> ScheduledLoop: ...

    async def clear(self) -> int: ...


def _format_loop_list(loops: list[ScheduledLoop]) -> str:
    if not loops:
        return "No scheduled loops."
    now = time.time()
    rows = ["| Prompt | Next in | Every | ID |", "|--------|------|-------|----|"]
    for loop in loops:
        remaining = _format_duration(max(0, int(loop.next_fire_at - now)), short=True)
        interval = _format_duration(loop.interval_seconds)
        prompt = loop.prompt.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {prompt} | {remaining} | {interval} | `{loop.id}` |")
    return "\n".join(rows)


class ScheduledLoopCommands:
    def __init__(
        self, loops: LoopsClient, *, tools_collapsed: Callable[[], bool]
    ) -> None:
        self._loops = loops
        self._tools_collapsed = tools_collapsed

    async def handle_command(self, cmd_args: str) -> Widget:
        try:
            return await self._handle_command(cmd_args.strip())
        except AppServerResponseError as exc:
            return self._error(exc.error.message)

    async def _handle_command(self, arguments: str) -> Widget:
        if not arguments or arguments.lower() in {"list", "ls"}:
            return UserCommandMessage(_format_loop_list(await self._loops.list()))
        verb, _, rest = arguments.partition(" ")
        if verb.lower() not in {"cancel", "rm", "stop", "delete"}:
            loop = await self._loops.create(verb, rest)
            return UserCommandMessage(
                f"Scheduled loop `{loop.id}` every "
                f"{_format_duration(loop.interval_seconds)}: {loop.prompt}"
            )
        loop_id = rest.strip()
        if not loop_id:
            return self._error("Missing loop id.")
        if loop_id.lower() == "all":
            count = await self._loops.clear()
            return UserCommandMessage(f"Cancelled {count} scheduled loop(s).")
        loop = await self._loops.delete(loop_id)
        return UserCommandMessage(f"Cancelled loop `{loop.id}`: {loop.prompt}")

    def _error(self, message: str) -> ErrorMessage:
        return ErrorMessage(
            f"{message}\n{_USAGE_HINT}", collapsed=self._tools_collapsed()
        )


def _format_duration(seconds: int, *, short: bool = False) -> str:
    units = ((86400, "d"), (3600, "h"), (60, "m"), (1, "s"))
    parts: list[str] = []
    for unit_seconds, suffix in units:
        value = seconds // unit_seconds
        if value > 0:
            parts.append(f"{value}{suffix}")
            seconds %= unit_seconds
    if not parts:
        parts.append("0s")
    return parts[0] if short else "".join(parts)
