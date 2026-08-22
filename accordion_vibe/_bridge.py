"""The harness-side half of the Accordion sidecar protocol.

``AccordionBridge`` turns vibe-native values into the wire payloads the sidecar
expects and back again. It owns the sidecar process, the folding flag, and the
notification sink the TUI drains; it knows nothing about ``AgentLoop``.

Every method is safe to call on a dead or never-started bridge: hooks become
no-ops and ``context`` returns None (passthrough).
"""

from __future__ import annotations

from collections.abc import Sequence
import threading
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from accordion_vibe._config import sidecar_command, sidecar_log_path
from accordion_vibe._sidecar import SidecarClient
from vibe.core.types import LLMMessage
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AccordionBridge", "active_bridges", "bridge_for_session", "primary_bridge"]

# The only blocking hook. 250 ms is the whole budget the model call may lose.
CONTEXT_TIMEOUT_S = 0.25
TOOL_TIMEOUT_S = 120.0
COMMAND_TIMEOUT_S = 30.0
HOOK_RESULT_TIMEOUT_S = 5.0
MESSAGE_UPDATE_INTERVAL_S = 0.1  # <= 10 updates/second

_REGISTRY: list[AccordionBridge] = []
_REGISTRY_LOCK = threading.Lock()


def active_bridges() -> list[AccordionBridge]:
    """Every bridge alive in this process, oldest first."""
    with _REGISTRY_LOCK:
        return [b for b in _REGISTRY if not b.closed]


def bridge_for_session(session_id: str | None) -> AccordionBridge | None:
    """The bridge owning ``session_id``, falling back to the only live one."""
    bridges = active_bridges()
    for bridge in bridges:
        if bridge.session_id == session_id:
            return bridge
    return bridges[0] if len(bridges) == 1 else None


def primary_bridge() -> AccordionBridge | None:
    """The bridge a UI-level command should talk to (the newest live one)."""
    bridges = active_bridges()
    return bridges[-1] if bridges else None


def _dump(messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in messages]


class AccordionBridge:
    """Owns one sidecar process for one ``AgentLoop``."""

    def __init__(
        self,
        repo: Path,
        *,
        session_id: str,
        cwd: Path,
        harness_version: str,
        session_file: str | None = None,
        model: dict[str, Any] | None = None,
        flags: dict[str, Any] | None = None,
    ) -> None:
        self.repo = repo
        self.session_id = session_id
        self.cwd = cwd
        self.harness_version = harness_version
        self.session_file = session_file
        self.model = model
        self.flags = flags

        self.folding_enabled = False
        self.folding_generation = 0
        self.context_passthroughs = 0
        self.closed = False
        self.skill_paths: list[str] = []

        self._client: SidecarClient | None = None
        self._start_lock = threading.Lock()
        self._start_attempted = False
        self._notices: list[tuple[str, str]] = []
        self._notices_lock = threading.Lock()
        self._status: str = ""
        self._last_update_sent = 0.0
        self._started_session = False
        self._handshake_done = threading.Event()

        with _REGISTRY_LOCK:
            _REGISTRY.append(self)

    # -- lifecycle ---------------------------------------------------------

    @property
    def attached(self) -> bool:
        client = self._client
        return client is not None and client.alive

    @property
    def status_text(self) -> str:
        return self._status

    def start_in_background(self) -> None:
        """Kick off the spawn without blocking the caller."""
        thread = threading.Thread(
            target=self.ensure_started, name="accordion-bridge-start", daemon=True
        )
        thread.start()

    def ensure_started(self, timeout: float = 5.0) -> bool:
        """Spawn + handshake once. Idempotent; safe from any thread."""
        with self._start_lock:
            if self._start_attempted:
                return self.attached
            self._start_attempted = True
            try:
                return self._handshake(timeout)
            finally:
                self._handshake_done.set()

    def _handshake(self, timeout: float) -> bool:
        command = sidecar_command(self.repo)
        if command is None:
            logger.info(
                "accordion: no sidecar bundle under %s; bridge inert", self.repo
            )
            return False
        client = SidecarClient(
            command,
            cwd=self.cwd,
            log_path=sidecar_log_path(self.session_id),
            on_notify=self._record_notice,
            on_status=self._record_status,
            on_folding=self._record_folding,
        )
        hello: dict[str, Any] = {
            "harness": "vibe",
            "harnessVersion": self.harness_version,
            "sessionId": self.session_id,
            "cwd": str(self.cwd),
        }
        if self.session_file:
            hello["sessionFile"] = self.session_file
        if self.model:
            hello["model"] = self.model
        if self.flags:
            hello["flags"] = self.flags
        if not client.start(hello, timeout=timeout):
            self._record_notice(
                "Accordion sidecar unavailable; folding is off.", "warning"
            )
            return False
        self._client = client
        return True

    def wait_ready(self, timeout: float) -> bool:
        """Block briefly for the in-flight handshake. Never starts one."""
        if self.attached:
            return True
        self._handshake_done.wait(timeout)
        return self.attached

    def close(self) -> None:
        """Send ``session_shutdown``, then reap the process."""
        if self.closed:
            return
        self.closed = True
        client = self._client
        self._client = None
        with _REGISTRY_LOCK:
            if self in _REGISTRY:
                _REGISTRY.remove(self)
        if client is None:
            return
        try:
            client.send({"type": "session_shutdown"})
            client.close()
        except Exception as exc:  # teardown must never propagate
            logger.debug("accordion: sidecar close failed: %s", exc)

    # -- sidecar-initiated state ------------------------------------------

    def _record_notice(self, message: str, level: str = "info") -> None:
        with self._notices_lock:
            self._notices.append((message, level))

    def _record_status(self, text: str) -> None:
        self._status = text

    def _record_folding(self, enabled: bool) -> None:
        if enabled == self.folding_enabled:
            return
        self.folding_enabled = enabled
        self.folding_generation += 1

    def drain_notices(self) -> list[tuple[str, str]]:
        """Take every pending ``notify`` message (text, level)."""
        with self._notices_lock:
            notices = self._notices
            self._notices = []
        return notices

    # -- outbound ----------------------------------------------------------

    def emit(self, hook: str, **fields: Any) -> None:
        """Fire-and-forget one hook."""
        client = self._client
        if client is None or not client.alive:
            return
        client.send({"type": hook, **fields})

    def emit_message_update(self, message: LLMMessage) -> None:
        """Throttled ``message_update`` (<= 10/s)."""
        now = time.monotonic()
        if now - self._last_update_sent < MESSAGE_UPDATE_INTERVAL_S:
            return
        self._last_update_sent = now
        self.emit("message_update", message=message.model_dump(mode="json"))

    def session_start(self, reason: str, messages: Sequence[LLMMessage]) -> None:
        if self._started_session:
            return
        self._started_session = True
        self.emit("session_start", reason=reason, messages=_dump(messages))
        self._discover_resources()

    def _discover_resources(self) -> None:
        client = self._client
        if client is None:
            return
        reply = client.request(
            {"type": "resources_discover"}, timeout=HOOK_RESULT_TIMEOUT_S
        )
        if reply is None:
            return
        paths = reply.get("skillPaths")
        self.skill_paths = [str(p) for p in paths] if isinstance(paths, list) else []

    def context(
        self, messages: Sequence[LLMMessage], model: dict[str, Any]
    ) -> list[LLMMessage] | None:
        """The one blocking hook. None means "use the harness's own messages"."""
        client = self._client
        if client is None or not client.alive or not messages:
            # An empty array is malformed on the wire and would be answered
            # `null` anyway; skip the round trip.
            return None
        reply = client.request(
            {"type": "context", "messages": _dump(messages), "model": model},
            timeout=CONTEXT_TIMEOUT_S,
        )
        if reply is None:
            self.context_passthroughs += 1
            return None
        raw = reply.get("messages")
        if raw is None:
            return None
        # Accordion may collapse a group, so a SHORTER reply is legal; a longer
        # one means the reply is not a rewrite of what was sent.
        if not isinstance(raw, list) or len(raw) > len(messages):
            self.context_passthroughs += 1
            logger.warning(
                "accordion: sidecar returned %s messages for a %d-message wire",
                len(raw) if isinstance(raw, list) else type(raw).__name__,
                len(messages),
            )
            return None
        try:
            return [LLMMessage.model_validate(m) for m in raw]
        except ValidationError as exc:
            self.context_passthroughs += 1
            logger.warning("accordion: sidecar returned invalid messages: %s", exc)
            return None

    def before_agent_start(self, prompt: str) -> str | None:
        """Return a system prompt to use for this run only, if the sidecar sets one."""
        client = self._client
        if client is None or not client.alive:
            return None
        reply = client.request(
            {"type": "before_agent_start", "prompt": prompt},
            timeout=HOOK_RESULT_TIMEOUT_S,
        )
        if reply is None:
            return None
        prompt_override = reply.get("systemPrompt")
        return prompt_override if isinstance(prompt_override, str) else None

    def call_tool(
        self, name: str, args: dict[str, Any], tool_call_id: str | None = None
    ) -> tuple[str, bool]:
        """Invoke a sidecar-registered tool. Returns (content, is_error)."""
        client = self._client
        if client is None or not client.alive:
            return ("Accordion is not attached to this session.", True)
        payload: dict[str, Any] = {"type": "tool", "name": name, "args": args}
        if tool_call_id:
            payload["toolCallId"] = tool_call_id
        reply = client.request(payload, timeout=TOOL_TIMEOUT_S)
        if reply is None:
            return (f"Accordion tool {name!r} did not answer.", True)
        # The sidecar spells the flag isError; accept is_error for symmetry
        # with vibe's own tool-result shape.
        raw_error = reply.get("is_error", reply.get("isError"))
        return (str(reply.get("content", "")), bool(raw_error))

    def run_command(self, name: str, args: str = "") -> tuple[bool, str | None]:
        """Invoke a sidecar-registered slash command. Returns (ok, error)."""
        client = self._client
        if client is None or not client.alive:
            return (False, "Accordion is not attached to this session.")
        reply = client.request(
            {"type": "command", "name": name, "args": args}, timeout=COMMAND_TIMEOUT_S
        )
        if reply is None:
            return (False, f"/{name} did not answer.")
        error = reply.get("error")
        return (bool(reply.get("ok")), str(error) if error else None)

    # -- ready payload -----------------------------------------------------

    def _ready(self) -> dict[str, Any]:
        client = self._client
        return client.ready_payload if client is not None else {}

    def _specs(self, key: str) -> list[dict[str, Any]]:
        specs = self._ready().get(key)
        if not isinstance(specs, list):
            return []
        return [s for s in specs if isinstance(s, dict)]

    def tool_specs(self) -> list[dict[str, Any]]:
        return self._specs("tools")

    def command_specs(self) -> list[dict[str, Any]]:
        return self._specs("commands")
