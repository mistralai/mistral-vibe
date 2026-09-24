from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import time
from typing import TYPE_CHECKING, Any, final
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, VibeConfigSchema
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.builtins._computer_use_trace import (
    SENTINEL,
    step_payload as _step_payload,
)
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent

_MISTRAL_API_BASE = "https://api.mistral.ai/v1"
_STEP_CAP = 40
_WALL_TIMEOUT_SECONDS = 120
_HEARTBEAT_SECONDS = 3.0
_BROWSER_USE_PACKAGE = "browser-use==0.13.8"
_LAUNCH_PHASE_SECONDS = 45
_WORKER_MODULE = "vibe.core.tools.builtins._computer_use_worker"
_STDERR_TAIL_LINES = 15


class ComputerUseStep(BaseModel):
    step: int
    action: str
    thought: str = ""
    url: str = ""


class ComputerUseArgs(BaseModel):
    url: str = Field(description="Public URL to open before starting the task.")
    task: str = Field(
        min_length=1, description="What to accomplish on the site, in concrete terms."
    )
    intent: str = Field(
        default="",
        description="Who the user is and what they are trying to achieve, so the agent can behave like them.",
    )
    max_steps: int | None = Field(
        default=None, description="Override the configured step budget for this run."
    )
    show_browser: bool = Field(
        default=True,
        description="Open a visible Chromium window so you can watch the agent work.",
    )


class ComputerUseResult(BaseModel):
    url: str
    task: str
    completed: bool
    final_url: str
    summary: str
    steps: list[ComputerUseStep] = Field(default_factory=list)
    num_steps: int = 0
    budget_exhausted: bool = False


class ComputerUseConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK

    model: str = Field(
        default="mistral-small-latest",
        description="Mistral model driving the browser. Needs vision for screenshots.",
    )
    api_base: str = Field(
        default=_MISTRAL_API_BASE, description="OpenAI-compatible Mistral endpoint."
    )
    max_steps: int = Field(
        default=12, description="Default step budget for a single task."
    )
    headless: bool = Field(
        default=False,
        description="Hide the browser window. Prefer show_browser=false on each call instead.",
    )
    viewport_width: int = Field(default=1280)
    viewport_height: int = Field(default=800)
    wall_timeout_seconds: int = Field(
        default=_WALL_TIMEOUT_SECONDS,
        description="Hard cap on wall time for one browser task.",
    )


def _step_from_history_item(item: Any, step_no: int) -> ComputerUseStep:
    return ComputerUseStep(**_step_payload(item, step_no))


def _format_step_message(step: ComputerUseStep, max_steps: int) -> str:
    lines = [f"Step {step.step}/{max_steps}: {step.action}"]
    if step.url:
        lines.append(f"  @ {step.url}")
    if step.thought:
        lines.append(f"  → {step.thought}")
    return "\n".join(lines)


class ComputerUse(
    BaseTool[ComputerUseArgs, ComputerUseResult, ComputerUseConfig, BaseToolState],
    ToolUIData[ComputerUseArgs, ComputerUseResult],
):
    effect_kind = ToolEffectKind.TOOL

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return bool(resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY))

    @staticmethod
    def _browser_use_installed() -> bool:
        return importlib.util.find_spec("browser_use") is not None

    @staticmethod
    async def _install_browser_use() -> None:
        if shutil.which("uv"):
            cmd = ["uv", "pip", "install", _BROWSER_USE_PACKAGE]
        else:
            cmd = [sys.executable, "-m", "pip", "install", _BROWSER_USE_PACKAGE]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = (stderr or stdout).decode().strip()
            raise ToolError(
                "Could not install browser-use automatically. "
                "Run `uv pip install browser-use` and retry. "
                f"{detail}"
            )
        if not ComputerUse._browser_use_installed():
            raise ToolError(
                "browser-use install finished but the package is still missing. "
                "Run `uv pip install browser-use` and retry."
            )

    @staticmethod
    def _ensure_chrome() -> None:
        chrome_module = importlib.import_module("browser_use.browser.chrome")
        if chrome_module.find_chrome_executable() is None:
            raise ToolError(
                "Google Chrome or Chromium is required but was not found on this machine. "
                "Install Chrome, then retry computer_use."
            )

    @staticmethod
    def _normalize_url(url: str) -> str:
        raw = url.lstrip("/") if url.startswith("//") else url
        return raw if raw.startswith(("http://", "https://")) else "https://" + raw

    def resolve_permission(self, args: ComputerUseArgs) -> PermissionContext | None:
        if self.config.permission in {ToolPermission.ALWAYS, ToolPermission.NEVER}:
            return PermissionContext(permission=self.config.permission)

        parsed = urlparse(self._normalize_url(args.url))
        domain = parsed.netloc or parsed.path.split("/")[0]
        if not domain:
            return None

        return PermissionContext(
            permission=ToolPermission.ASK,
            required_permissions=[
                RequiredPermission(
                    scope=PermissionScope.URL_PATTERN,
                    invocation_pattern=domain,
                    session_pattern=domain,
                    label=f"driving a browser on {domain}",
                )
            ],
        )

    def _stream_event(self, message: str, ctx: InvokeContext | None) -> ToolStreamEvent:
        return ToolStreamEvent(
            tool_name=self.get_name(),
            message=message if message.endswith("\n") else f"{message}\n",
            tool_call_id=ctx.tool_call_id if ctx else "",
        )

    def _resolve_headless(self, args: ComputerUseArgs) -> bool:
        if os.environ.get("COMPUTER_USE_HEADLESS", "").lower() in {"1", "true", "yes"}:
            return True
        if os.environ.get("COMPUTER_USE_HEADED", "").lower() in {"1", "true", "yes"}:
            return False
        return not args.show_browser

    def _worker_request(
        self,
        url: str,
        args: ComputerUseArgs,
        api_key: str,
        headless: bool,
        max_steps: int,
    ) -> dict[str, Any]:
        return {
            "prompt": self._build_prompt(url, args),
            "api_key": api_key,
            "api_base": self.config.api_base,
            "model": self.config.model,
            "headless": headless,
            "max_steps": max_steps,
            "viewport_width": self.config.viewport_width,
            "viewport_height": self.config.viewport_height,
        }

    @final
    async def run(
        self, args: ComputerUseArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ComputerUseResult, None]:
        if not self._browser_use_installed():
            yield self._stream_event(
                "First run: installing browser-use "
                "(uses your system Chrome — no Playwright)…",
                ctx,
            )
            await self._install_browser_use()

        self._ensure_chrome()

        api_key = resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY)
        if not api_key:
            raise ToolError(
                f"{DEFAULT_MISTRAL_API_ENV_KEY} environment variable not set."
            )

        url = self._normalize_url(args.url)
        self._validate_url(url)
        max_steps = min(args.max_steps or self.config.max_steps, _STEP_CAP)
        headless = self._resolve_headless(args)

        yield self._stream_event(
            f"Opening {url} (headless) …"
            if headless
            else f"Opening visible browser → {url} …",
            ctx,
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            _WORKER_MODULE,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )

        result_payload: dict[str, Any] | None = None
        try:
            async for message, payload in self._drive_worker(
                proc,
                self._worker_request(url, args, api_key, headless, max_steps),
                max_steps,
            ):
                if payload is not None:
                    result_payload = payload
                if message:
                    yield self._stream_event(message, ctx)
        finally:
            await self._terminate(proc)

        if result_payload is None:
            raise ToolError("Browser agent exited before reporting a result.")

        result = self._build_result(result_payload, url, args, max_steps)
        yield self._stream_event(self._format_completion_message(result), ctx)
        yield result

    async def _drive_worker(
        self, proc: Any, request: dict[str, Any], max_steps: int
    ) -> AsyncGenerator[tuple[str | None, dict[str, Any] | None], None]:
        """Feed the worker its request, then translate its events into messages."""
        proc.stdin.write(json.dumps(request).encode())
        await proc.stdin.drain()
        proc.stdin.close()

        stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        drain = asyncio.create_task(self._drain_stderr(proc, stderr_tail))

        started = time.monotonic()
        completed_steps = 0
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), _HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    elapsed = time.monotonic() - started
                    if elapsed >= self.config.wall_timeout_seconds:
                        raise ToolError(
                            f"Browser task timed out after "
                            f"{self.config.wall_timeout_seconds}s. "
                            "Try a simpler task or raise max_steps."
                        ) from None
                    yield self._heartbeat(elapsed, completed_steps, max_steps), None
                    continue

                if not line:
                    break

                event = self._parse_event(line)
                if event is None:
                    continue
                if event.get("type") == "error":
                    raise ToolError(
                        f"Browser agent failed: {event.get('message', 'unknown error')}"
                    )
                if event.get("type") == "result":
                    yield None, event
                    continue
                completed_steps = int(event.get("step", completed_steps))
                yield (
                    _format_step_message(
                        ComputerUseStep(**{
                            key: event[key]
                            for key in ("step", "action", "thought", "url")
                            if key in event
                        }),
                        max_steps,
                    ),
                    None,
                )
        finally:
            drain.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain

        if proc.returncode not in {0, None} and stderr_tail:
            raise ToolError("Browser agent failed: " + " | ".join(stderr_tail))

    @staticmethod
    def _parse_event(line: bytes) -> dict[str, Any] | None:
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith(SENTINEL):
            return None
        try:
            parsed = json.loads(text[len(SENTINEL) :])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _heartbeat(elapsed: float, completed_steps: int, max_steps: int) -> str:
        phase = (
            "launching browser"
            if completed_steps == 0 and elapsed < _LAUNCH_PHASE_SECONDS
            else "waiting on browser/model"
        )
        return (
            f"… still working ({int(elapsed)}s, "
            f"step {completed_steps + 1}/{max_steps}, {phase})"
        )

    @staticmethod
    async def _drain_stderr(proc: Any, tail: deque[str]) -> None:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text[:200])

    @staticmethod
    async def _terminate(proc: Any) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ToolError(
                f"Invalid URL scheme: {parsed.scheme}. Must be http or https."
            )
        if not parsed.netloc:
            raise ToolError("URL must include a host.")

    @staticmethod
    def _build_prompt(url: str, args: ComputerUseArgs) -> str:
        lines = [f"Open {url} and complete the task below."]
        if args.intent:
            lines.append(f"You are acting for this user: {args.intent}")
        lines.append(f"Task: {args.task}")
        lines.append(
            "Use the site's own controls — filters, dropdowns, and date pickers — "
            "rather than typing every constraint into a search box."
        )
        lines.append(
            "Stop as soon as every part of the task is satisfied on the page. "
            "Call done immediately when finished."
        )
        return "\n".join(lines)

    @staticmethod
    def _build_result(
        payload: dict[str, Any], url: str, args: ComputerUseArgs, max_steps: int
    ) -> ComputerUseResult:
        steps = [ComputerUseStep(**step) for step in payload.get("steps") or []]

        final_url = next((step.url for step in reversed(steps) if step.url), url)
        completed = bool(payload.get("is_done"))
        summary = str(payload.get("final_result") or "")

        return ComputerUseResult(
            url=url,
            task=args.task,
            completed=completed,
            final_url=final_url,
            summary=summary,
            steps=steps,
            num_steps=len(steps),
            budget_exhausted=len(steps) >= max_steps and not completed,
        )

    @staticmethod
    def _format_completion_message(result: ComputerUseResult) -> str:
        status = "completed" if result.completed else "stopped"
        lines = [
            f"Done ({status}) — {result.num_steps} steps",
            f"Final page: {result.final_url}",
        ]
        if result.summary:
            lines.append(result.summary.strip())
        return "\n".join(lines)

    def get_result_extra(self, result: ComputerUseResult) -> str | None:
        lines = [
            "Browser trace:",
            *[
                f"  {s.step}. {s.action}" + (f" @ {s.url}" if s.url else "")
                for s in result.steps
            ],
        ]
        if result.summary:
            lines.append(f"Summary: {result.summary.strip()}")
        lines.append(f"Final: {result.final_url}")
        return "\n".join(lines)

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, ComputerUseArgs):
            return ToolCallDisplay(
                summary="computer_use",
                verb="Running",
                message="computer_use",
                settled_verb="Ran",
                settled_message="computer_use",
            )

        parsed = urlparse(cls._normalize_url(event.args.url))
        message = f"{parsed.netloc or event.args.url[:50]} — {event.args.task[:80]}"
        return ToolCallDisplay(
            summary=f"Browsing: {message}",
            verb="Browsing",
            message=message,
            settled_verb="Browsed",
            settled_message=message,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ComputerUseResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        result = event.result
        snippet = result.summary.strip().split("\n")[0][:80] if result.summary else ""
        message = result.final_url
        if snippet:
            message = f"{result.final_url} — {snippet}"
        if result.budget_exhausted:
            return ToolResultDisplay(
                success=False,
                verb="Ran out of steps",
                message=message,
                suffix=f"({result.num_steps} steps)",
            )
        return ToolResultDisplay(
            success=result.completed,
            verb="Completed" if result.completed else "Stopped",
            message=message,
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Driving browser…"


def _call_or_default(target: Any, method_name: str, default: Any) -> Any:
    method = getattr(target, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default
