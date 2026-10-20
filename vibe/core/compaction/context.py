from __future__ import annotations

from html import escape
import re

_SUMMARY_OPEN = "<summary>"
_SUMMARY_CLOSE = "</summary>"
_SUMMARY_RE = re.compile(
    rf"{re.escape(_SUMMARY_OPEN)}(.*?){re.escape(_SUMMARY_CLOSE)}", re.DOTALL
)


def extract_summary(text: str) -> str | None:
    match = _SUMMARY_RE.search(text)
    if match is None:
        return None
    return match.group(1).strip() or None


def render_teleport_summary_request(
    base_summary_prompt: str, teleported_prompt: str, *, max_summary_chars: int
) -> str:
    return "\n".join([
        base_summary_prompt,
        "",
        "## Teleport Message Context Instructions",
        "Create concise message context for Vibe Code Web. Focus on prior "
        "task state that the next agent needs in order to continue naturally.",
        "Do not repeat the prompt that will be sent separately to start the "
        "web session.",
        f"Keep the summary under {max_summary_chars:,} characters.",
        "",
        "<teleported_prompt>",
        escape(teleported_prompt, quote=False),
        "</teleported_prompt>",
    ])
