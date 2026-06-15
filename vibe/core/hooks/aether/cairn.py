"""Gate: nudge toward better commit messages when message is weak."""

from __future__ import annotations

import re

from .bypass import is_bypassed

_MSG_PATTERN = re.compile(r'-m\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))')
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

    match = _MSG_PATTERN.search(command)
    if not match:
        return None

    message = (match.group(1) or match.group(2) or match.group(3) or "").strip()
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
