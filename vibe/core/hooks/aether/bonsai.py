"""Gate: nudge toward AST tools instead of grep/sed/awk/mv on source files."""

from __future__ import annotations

import re

from .bypass import is_bypassed

# Match text tools preceded by start-of-string, pipe, semicolon, &&, ||, or whitespace
_PATTERN = re.compile(
    r'(?:^|[|;&\s])(grep|sed|awk|mv)\b[^|;&\n]*\.(py|ts|tsx|js|jsx)\b',
    re.IGNORECASE,
)

# Strip quoted strings before matching to avoid flagging patterns like
# grep "\.py" config.json where .py appears only inside a quoted argument.
_STRIP_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')

_SUGGESTIONS: dict[str, str] = {
    "grep": "pygrep (Python) or tsfindrefs (TypeScript) to find all references",
    "sed": "pyrename / tsrename to rename symbols safely across all imports",
    "awk": "pyrename / tsrename to rename symbols safely across all imports",
    "mv": "pymove / tsmove to move files and rewrite all imports automatically",
}


def evaluate(command: str, cwd: str) -> dict | None:
    if is_bypassed(command, ("bonsai:skip",)):
        return None

    match = _PATTERN.search(_STRIP_QUOTED.sub("", command))
    if not match:
        return None

    tool = match.group(1).lower()
    ext = match.group(2).lower()
    suggestion = _SUGGESTIONS.get(tool, "bonsai AST tools")

    return {
        "decision": "deny",
        "reason": (
            f"🟡 bonsai: Detected `{tool}` on a .{ext} file.\n\n"
            f"Prefer {suggestion} — text tools miss re-exports, aliased imports, "
            f"and type references.\n\n"
            f"To bypass: append # bonsai:skip"
        ),
    }
