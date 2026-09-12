"""Trace formatting shared by the computer_use tool and its worker process.

Kept free of vibe imports so the worker process starts fast.
"""

from __future__ import annotations

from typing import Any

SENTINEL = "@@COMPUTER_USE@@"


def is_meaningful(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str | list | dict | tuple) and not value:
        return False
    return True


def action_label(action: Any) -> str:
    if action is None:
        return "—"
    if isinstance(action, list):
        return "; ".join(action_label(item) for item in action)

    payload = getattr(action, "root", None) or action
    dumped = (
        payload.model_dump(exclude_none=True)
        if hasattr(payload, "model_dump")
        else None
    )
    if not isinstance(dumped, dict) or not dumped:
        return str(action)[:200]

    name, arguments = next(iter(dumped.items()))
    if not isinstance(arguments, dict):
        return f"{name}: {arguments}"[:200]

    detail = ", ".join(
        f"{key}={value}" for key, value in arguments.items() if is_meaningful(value)
    )
    return (f"{name} — {detail}" if detail else str(name))[:200]


def thought(model_output: Any) -> str:
    for field_name in ("next_goal", "evaluation_previous_goal", "thinking"):
        value = getattr(model_output, field_name, None)
        if value:
            return str(value).strip()[:300]
    return ""


def step_payload(item: Any, step_no: int) -> dict[str, Any]:
    model_output = getattr(item, "model_output", None)
    state = getattr(item, "state", None)
    return {
        "step": step_no,
        "action": action_label(getattr(model_output, "action", None)),
        "thought": thought(model_output),
        "url": str(getattr(state, "url", "") or ""),
    }
