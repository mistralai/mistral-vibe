"""A stdio MCP server on the standard library alone: one built on `mcp` spends 0.3s importing it per spawn."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

_TOOLS = [
    {
        "name": "echo",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
    {
        "name": "summarize",
        "description": "Summarize a document.\n\nUsage notes the UI must not show.",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
]


class _MethodNotFoundError(Exception):
    pass


def _result(method: str, params: dict[str, Any], log: Path | None, fail: bool) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": params["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        if log is not None:
            with log.open("a") as stream:
                stream.write("tools/list\n")
        if fail:
            raise RuntimeError("down")
        return {"tools": _TOOLS}
    if method == "tools/call":
        value = str((params.get("arguments") or {}).get("value", ""))
        return {"content": [{"type": "text", "text": value}], "isError": False}
    raise _MethodNotFoundError(method)


def main() -> None:
    # argv: [LOG [ok|fail]]; LOG records each tools/list, `fail` answers it with an error.
    log = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    fail = len(sys.argv) > 2 and sys.argv[2] == "fail"
    while line := sys.stdin.readline():
        request = json.loads(line)
        if "id" not in request:
            continue
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            response["result"] = _result(
                request["method"], request.get("params") or {}, log, fail
            )
        except _MethodNotFoundError as exc:
            response["error"] = {"code": -32601, "message": f"Method not found: {exc}"}
        except RuntimeError as exc:
            response["error"] = {"code": -32603, "message": str(exc)}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
