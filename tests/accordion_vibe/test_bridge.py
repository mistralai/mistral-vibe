"""``AccordionBridge``: vibe values in, wire payloads out, and back again."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from accordion_vibe._bridge import (
    AccordionBridge,
    active_bridges,
    bridge_for_session,
    primary_bridge,
)
from tests.accordion_vibe.conftest import Sidecar, wait_until
from tests.accordion_vibe.fake_sidecar import FOLD_PREFIX
from vibe.core.types import LLMMessage, Role

_MODEL = {"id": "devstral", "provider": "mistral", "contextWindow": 100_000}


def _messages() -> list[LLMMessage]:
    return [
        LLMMessage(role=Role.system, content="You are vibe."),
        LLMMessage(role=Role.user, content="hello"),
    ]


# -- context ----------------------------------------------------------------


def test_context_replaces_the_messages_the_sidecar_rewrote(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    sidecar = make_sidecar(context="replace")
    bridge = make_bridge(sidecar)

    replacement = bridge.context(_messages(), _MODEL)

    assert replacement is not None
    assert [m.content for m in replacement] == ["You are vibe.", f"{FOLD_PREFIX}hello"]
    assert bridge.context_passthroughs == 0
    sent = sidecar.received_of("context")[0]
    assert sent["model"] == _MODEL
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]


def test_context_null_is_a_passthrough_that_is_not_counted_as_a_failure(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(context="null"))

    assert bridge.context(_messages(), _MODEL) is None
    assert bridge.context_passthroughs == 0


@pytest.mark.parametrize("mode", ["hang", "invalid", "notalist"])
def test_context_falls_back_to_passthrough_and_counts_it(
    mode: str,
    make_sidecar: Callable[..., Sidecar],
    make_bridge: Callable[..., AccordionBridge],
):
    bridge = make_bridge(make_sidecar(context=mode))

    assert bridge.context(_messages(), _MODEL) is None

    assert bridge.context_passthroughs == 1
    assert bridge.attached  # none of these kill the sidecar


def test_context_on_a_never_started_bridge_is_a_passthrough(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(), start=False)

    assert bridge.context(_messages(), _MODEL) is None
    assert bridge.context_passthroughs == 0


# -- folding ----------------------------------------------------------------


def test_folding_toggles_are_idempotent(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    # The sidecar always opens with enabled:false, which the bridge already is;
    # the duplicated true must not bump the generation either.
    bridge = make_bridge(make_sidecar(folding_sequence=[True, True, False]))

    assert wait_until(lambda: bridge.folding_generation == 2)
    assert bridge.folding_enabled is False
    assert bridge.folding_generation == 2


def test_folding_enabled_is_off_until_the_sidecar_says_otherwise(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(folding_sequence=[True]))

    assert wait_until(lambda: bridge.folding_enabled)
    assert bridge.folding_generation == 1


# -- tools and commands -----------------------------------------------------


def test_call_tool_round_trips_the_arguments(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    sidecar = make_sidecar()
    bridge = make_bridge(sidecar)

    content, is_error = bridge.call_tool("unfold", {"codes": ["a1f"]}, "call-7")

    assert is_error is False
    assert '"name": "unfold"' in content
    assert '"codes": ["a1f"]' in content
    sent = sidecar.received_of("tool")[0]
    assert sent["name"] == "unfold"
    assert sent["args"] == {"codes": ["a1f"]}
    assert sent["toolCallId"] == "call-7"


@pytest.mark.parametrize("key", ["isError", "is_error"])
def test_call_tool_maps_either_spelling_of_the_error_flag(
    key: str,
    make_sidecar: Callable[..., Sidecar],
    make_bridge: Callable[..., AccordionBridge],
):
    bridge = make_bridge(make_sidecar(tool_error=True, tool_error_key=key))

    _content, is_error = bridge.call_tool("recall", {"codes": ["9c2"]})

    assert is_error is True


def test_call_tool_on_a_detached_bridge_reports_the_error(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(), start=False)

    content, is_error = bridge.call_tool("unfold", {"codes": []})

    assert is_error is True
    assert "not attached" in content


def test_run_command_returns_ok_and_queues_its_notices(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    sidecar = make_sidecar(
        command_notices=[["Opening Accordion", "info"], ["No app found", "warning"]]
    )
    bridge = make_bridge(sidecar)

    ok, error = bridge.run_command("accordion", "--browser")

    assert (ok, error) == (True, None)
    assert bridge.drain_notices() == [
        ("Opening Accordion", "info"),
        ("No app found", "warning"),
    ]
    assert bridge.drain_notices() == []
    assert sidecar.received_of("command")[0]["args"] == "--browser"


def test_run_command_surfaces_a_handler_error(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(command_ok=False, command_error="boom"))

    assert bridge.run_command("accordion") == (False, "boom")


def test_ready_payload_is_exposed_as_tool_and_command_specs(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar())

    assert [s["name"] for s in bridge.tool_specs()] == ["unfold", "recall"]
    assert [s["name"] for s in bridge.command_specs()] == ["accordion"]


# -- registry ---------------------------------------------------------------


def test_registry_finds_a_live_bridge_and_forgets_a_closed_one(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    bridge = make_bridge(make_sidecar(), session_id="sess-42")

    assert active_bridges() == [bridge]
    assert primary_bridge() is bridge
    assert bridge_for_session("sess-42") is bridge

    bridge.close()

    assert bridge.closed
    assert active_bridges() == []
    assert primary_bridge() is None
    assert bridge_for_session("sess-42") is None


def test_bridge_for_session_prefers_an_exact_session_match(
    make_sidecar: Callable[..., Sidecar], make_bridge: Callable[..., AccordionBridge]
):
    first = make_bridge(make_sidecar(), session_id="sess-1")
    second = make_bridge(make_sidecar(), session_id="sess-2")

    assert bridge_for_session("sess-1") is first
    assert bridge_for_session("sess-2") is second
    assert primary_bridge() is second
    # Two live bridges and no match: no guess is made.
    assert bridge_for_session("sess-3") is None
