"""A scriptable stand-in for Accordion's Node sidecar, spawned as a subprocess.

Not a test module: pytest never collects it because the filename does not start
with ``test_``. ``conftest.py`` writes a JSON scenario file and hands its path
as ``argv[1]``; every knob lives in that file so one script can play every
failure mode the bridge has to survive (no reply, garbage reply, sudden death).

Wire contract: ``accordion/docs/sidecar-protocol.md`` (protocol v1).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# What ``ready`` advertises. The bridge only ever reads the names, so the
# schemas stay minimal.
_TOOLS = [
    {
        "name": "unfold",
        "description": "Reopen folded blocks.",
        "parameters": {
            "type": "object",
            "properties": {"codes": {"type": "array", "items": {"type": "string"}}},
        },
    },
    {
        "name": "recall",
        "description": "Read a folded block.",
        "parameters": {
            "type": "object",
            "properties": {"codes": {"type": "array", "items": {"type": "string"}}},
        },
    },
]
_COMMANDS = [{"name": "accordion", "description": "Open Accordion"}]

# The prefix a ``context: "replace"`` scenario stamps onto every user message,
# which is what proves the sidecar's rewrite actually reached the backend.
FOLD_PREFIX = "FOLDED:"


def _rewrite(messages: Any) -> list[dict[str, Any]]:
    """Prefix every user message's string content, leaving everything else alone."""
    rewritten: list[dict[str, Any]] = []
    for message in messages if isinstance(messages, list) else []:
        copy = dict(message)
        content = copy.get("content")
        if copy.get("role") == "user" and isinstance(content, str):
            copy["content"] = FOLD_PREFIX + content
        rewritten.append(copy)
    return rewritten


class FakeSidecar:
    """One JSON-lines conversation, driven entirely by the scenario dict."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self._record_path = scenario.get("record_path")
        self._out = sys.stdout.buffer

    # -- io ----------------------------------------------------------------

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._out.write(line.encode("utf-8"))
        self._out.flush()

    def record(self, message: dict[str, Any]) -> None:
        """Append one received message so the test can assert what arrived."""
        if not self._record_path:
            return
        with open(self._record_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")
            handle.flush()

    def run(self) -> int:
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            self.record(message)
            if str(message.get("type")) == self.scenario.get("crash_on"):
                os._exit(1)
            if not self.handle(message):
                return 0
        return 0

    # -- dispatch ----------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> bool:
        """Answer one message. Returns False when the sidecar should exit."""
        kind = str(message.get("type"))
        req = message.get("req")
        match kind:
            case "hello":
                self._hello()
            case "shutdown":
                return False
            case "context":
                self._context(message, req)
            case "before_agent_start":
                self.write({
                    "type": "hook_result",
                    "req": req,
                    "systemPrompt": self.scenario.get("system_prompt"),
                })
            case "resources_discover":
                self.write({
                    "type": "hook_result",
                    "req": req,
                    "skillPaths": self.scenario.get("skill_paths", []),
                    "promptPaths": [],
                    "themePaths": [],
                })
            case "session_before_compact" if req is not None:
                self.write({"type": "hook_result", "req": req})
            case "tool":
                self._tool(message, req)
            case "command":
                self._command(req)
            case _:
                pass  # every other hook is fire-and-forget
        return True

    def _hello(self) -> None:
        if not self.scenario.get("ready", True):
            return
        self.write({
            "type": "ready",
            "v": 1,
            "protocolVersion": 22,
            "tools": _TOOLS,
            "commands": _COMMANDS,
            "flags": [],
        })
        # The real sidecar always announces the arm as off right after ready.
        self.write({"type": "folding", "enabled": False})
        for enabled in self.scenario.get("folding_sequence", []):
            self.write({"type": "folding", "enabled": bool(enabled)})

    def _context(self, message: dict[str, Any], req: Any) -> None:
        mode = self.scenario.get("context", "null")
        match mode:
            case "hang":
                return  # never answers; the harness must time out at 250 ms
            case "replace":
                payload: Any = _rewrite(message.get("messages"))
            case "invalid":
                payload = [{"role": "wizard", "content": "not a role"}]
            case "notalist":
                payload = "not a list either"
            case _:
                payload = None
        self.write({"type": "hook_result", "req": req, "messages": payload})

    def _tool(self, message: dict[str, Any], req: Any) -> None:
        # Echo the call back so the test can assert the round trip from the
        # tool's own return value as well as from the recording.
        echo = json.dumps({
            "name": message.get("name"),
            "args": message.get("args"),
            "toolCallId": message.get("toolCallId"),
        })
        reply: dict[str, Any] = {"type": "tool_result", "req": req, "content": echo}
        reply[str(self.scenario.get("tool_error_key", "isError"))] = bool(
            self.scenario.get("tool_error", False)
        )
        self.write(reply)

    def _command(self, req: Any) -> None:
        for text, level in self.scenario.get("command_notices", []):
            self.write({"type": "notify", "message": text, "level": level})
        reply: dict[str, Any] = {
            "type": "command_result",
            "req": req,
            "ok": bool(self.scenario.get("command_ok", True)),
        }
        if self.scenario.get("command_error"):
            reply["error"] = self.scenario["command_error"]
        self.write(reply)


def main() -> int:
    scenario: dict[str, Any] = {}
    if len(sys.argv) > 1 and sys.argv[1]:
        with open(sys.argv[1], encoding="utf-8") as handle:
            scenario = json.load(handle)
    return FakeSidecar(scenario).run()


if __name__ == "__main__":
    raise SystemExit(main())
