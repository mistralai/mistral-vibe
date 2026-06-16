"""Gate: nudge toward better commit messages when message is weak."""

from __future__ import annotations

import re

from .bypass import is_bypassed

_MSG_PATTERN = re.compile(
    r'(?:--message=|--message\s+|-m\s+)(?:"([^"]+)"|\'([^\']+)\'|(\S+))',
)
_WEAK = re.compile(
    r"^(fix|wip|update|change|edit|minor|small|quick|temp|test|stuff|thing"
    r"|work|done|ok|oops|misc|cleanup|changes?|tweak|patch)\s*[.!]*$",
    re.IGNORECASE,
)


def evaluate(command: str, cwd: str) -> dict | None:
    if "git commit" not in command:
        return None

    if is_bypassed(command, ("cairn:skip",)):
        return None

    parts = [
        (m.group(1) or m.group(2) or m.group(3) or "").strip()
        for m in _MSG_PATTERN.finditer(command)
    ]
    if not parts:
        return None

    message = "\n\n".join(p for p in parts if p)
    if not message:
        return None

    if len(message) >= 10 and not _WEAK.match(message):
        return None

    return {
        "decision": "deny",
        "reason": (
            f"🟡 cairn: Weak commit message '{message}'.\n\n"
            f"Run /cairn-commit to generate a message from the diff.\n\n"
            f"To bypass: append # cairn:skip"
        ),
    }
