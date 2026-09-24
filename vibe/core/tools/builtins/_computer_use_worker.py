"""Run one browser-use task in a dedicated process, streaming JSON-line events.

browser-use owns signal handlers, an event bus and a Chrome subprocess, none of
which survive being embedded in the Textual UI's event loop. Running it out of
process keeps the tool call identical in interactive and programmatic mode.

Protocol: the request arrives as JSON on stdin, events leave on stdout, each one
prefixed with ``SENTINEL`` so unrelated library output is ignored.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from vibe.core.tools.builtins._computer_use_trace import SENTINEL, step_payload


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _build_agent(request: dict[str, Any]) -> Any:
    from browser_use import Agent, ChatOpenAI
    from browser_use.browser.profile import BrowserProfile

    viewport = {
        "width": request["viewport_width"],
        "height": request["viewport_height"],
    }
    profile = BrowserProfile(
        headless=request["headless"],
        # demo_mode makes browser-use sleep 30s before closing the browser.
        demo_mode=False,
        disable_security=True,
        enable_default_extensions=False,
        captcha_solver=False,
        highlight_elements=False,
        keep_alive=False,
        minimum_wait_page_load_time=0.5,
        wait_for_network_idle_page_load_time=0.5,
        wait_between_actions=0.25,
        window_size=viewport,
        viewport=viewport,
    )
    return Agent(
        task=request["prompt"],
        llm=ChatOpenAI(
            model=request["model"],
            api_key=request["api_key"],
            base_url=request["api_base"],
            temperature=0,
            max_retries=4,
            timeout=90.0,
        ),
        browser_profile=profile,
        use_vision=True,
        use_judge=False,
        max_actions_per_step=2,
        calculate_cost=True,
        extend_system_message=(
            "Complete the task in as few steps as possible. "
            "Call done as soon as every constraint is satisfied on the page."
        ),
    )


async def _run(request: dict[str, Any]) -> int:
    os.environ.setdefault("BROWSER_USE_STEP_TIMEOUT", "45")
    os.environ.setdefault("BROWSER_USE_ACTION_TIMEOUT_S", "60")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

    agent = _build_agent(request)
    seen = 0

    async def on_step_end(running_agent: Any) -> None:
        nonlocal seen
        items = list(
            getattr(getattr(running_agent, "history", None), "history", None) or []
        )
        if len(items) <= seen:
            return
        seen = len(items)
        _emit({"type": "step", **step_payload(items[-1], seen)})

    history = await agent.run(max_steps=request["max_steps"], on_step_end=on_step_end)

    items = list(getattr(history, "history", None) or [])
    _emit({
        "type": "result",
        "steps": [step_payload(item, i) for i, item in enumerate(items, 1)],
        "is_done": bool(_call(history, "is_done", False)),
        "final_result": str(_call(history, "final_result", "") or ""),
    })
    return 0


def _call(target: Any, method_name: str, default: Any) -> Any:
    method = getattr(target, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except Exception as exc:
        _emit({"type": "error", "message": f"Invalid request: {exc}"})
        return 2

    try:
        return asyncio.run(_run(request))
    except Exception as exc:
        _emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
