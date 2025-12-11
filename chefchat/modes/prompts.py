"""ChefChat Mode Prompts
=======================

System prompt injection logic for each mode.
Extracted from mode_manager.py for better organization.

Each mode gets a specific XML block that instructs the LLM
on how to behave in that mode.
"""

from __future__ import annotations

from chefchat.modes.types import VibeMode


def get_system_prompt_modifier(mode: VibeMode) -> str:
    """Get mode-specific system prompt injection.

    This XML block should be prepended to the system prompt
    to inform the LLM about the current mode's rules.

    Args:
        mode: The current operational mode

    Returns:
        XML-formatted mode instruction block
    """
    if mode == VibeMode.PLAN:
        return """\u003cactive_mode\u003e📋 PLAN\u003c/active_mode\u003e
\u003crules\u003eRead-only mode. Use: read_file, grep, bash (ls/cat/grep). NO writes/modifications. Create detailed plans. Wait for approval ("approved"/"go ahead") before execution.\u003c/rules\u003e
\u003cstyle\u003eEmoji: 🔍📋💭. Verbose, pedagogical. Ask questions. Validate assumptions.\u003c/style\u003e"""

    elif mode == VibeMode.NORMAL:
        return """\u003cactive_mode\u003e✋ NORMAL\u003c/active_mode\u003e
\u003crules\u003eReads auto-approved. Writes need confirmation. Explain before acting.\u003c/rules\u003e
\u003cstyle\u003eEmoji: ✋✅⚠️. Concise but complete. Confirm risky ops.\u003c/style\u003e"""

    elif mode == VibeMode.AUTO:
        return """\u003cactive_mode\u003e⚡ AUTO\u003c/active_mode\u003e
\u003crules\u003eAll tools auto-approved. Execute without waiting. Explain actions but don't ask permission.\u003c/rules\u003e
\u003cstyle\u003eEmoji: ⚡✅🔧. Confident, efficient. Maintain momentum.\u003c/style\u003e"""

    elif mode == VibeMode.YOLO:
        return """\u003cactive_mode\u003e🚀 YOLO\u003c/active_mode\u003e
\u003crules\u003eInstant auto-approval. MINIMIZE output. Execute rapidly. Quality maintained.\u003c/rules\u003e
\u003cstyle\u003eULTRA-CONCISE. "✓ [action]" or "✗ [error]". Verbose only on errors. Chain actions silently.\u003c/style\u003e"""

    elif mode == VibeMode.ARCHITECT:
        return """\u003cactive_mode\u003e🏛️ ARCHITECT\u003c/active_mode\u003e
\u003crules\u003eHigh-level design only. Read-only tools. NO modifications. Think systems/patterns. Use mermaid diagrams. Present options with trade-offs.\u003c/rules\u003e
\u003cstyle\u003eEmoji: 🏛️📐💭. Abstract, conceptual. Focus "what"/"why" not "how".\u003c/style\u003e"""

    return ""
