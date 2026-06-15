"""Single entry point for all aether gates — one subprocess per bash call."""

from __future__ import annotations

import json
import sys

from .bonsai import evaluate as bonsai
from .cairn import evaluate as cairn
from .temper import evaluate as temper
from .whetstone import evaluate as whetstone

_GATES = (whetstone, bonsai, temper, cairn)


def main() -> None:
    try:
        invocation = json.loads(sys.stdin.buffer.read())
        command = invocation.get("tool_input", {}).get("command", "")
        cwd = invocation.get("cwd", ".")
    except Exception:
        sys.exit(0)

    for gate in _GATES:
        try:
            result = gate(command, cwd)
        except Exception:
            result = None

        if result:
            print(json.dumps(result))
            return


if __name__ == "__main__":
    main()
