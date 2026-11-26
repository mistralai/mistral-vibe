"""Tests for the Vibe attribution ridden by Unified Harness provider requests."""

from __future__ import annotations

from tests.conftest import build_test_vibe_config
from vibe.app_server._completion_attribution import (
    CompletionAffinityHolder,
    CompletionAttributionHolder,
    build_completion_attribution,
)
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext


def test_completion_attribution_names_the_local_host_and_the_call() -> None:
    """*Prepare*: A holder, then the same holder bound to a session's telemetry.
    *Do*: Ask for the attribution of a turn's first call, a tool loop, a
    compaction, a classify-gate completion, a background title.
    *Assert*: All name the local Code host and session; only call type differs.
    """
    # Prepare
    holder = CompletionAttributionHolder()
    before_binding = holder.metadata("agent", 0)
    holder.bind(
        build_completion_attribution(
            TelemetryClient(
                config_getter=build_test_vibe_config,
                session_id_getter=lambda: "session-123",
            ),
            LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
            ),
        )
    )

    # Do
    first_call = holder.metadata("agent", 0)
    tool_loop = holder.metadata("agent", 3)
    compaction = holder.metadata("compaction", 0)
    classify = holder.metadata("classify", 0)
    title = holder.metadata("title", 0)

    # Assert
    # An unbound holder has no session to name, so it attributes nothing rather
    # than emitting a row the request marts would misattribute.
    assert before_binding == {}
    assert first_call["call_source"] == "vibe_code"
    assert first_call["host_kind"] == "local"
    assert first_call["session_id"] == "session-123"
    assert first_call["agent_entrypoint"] == "cli"
    assert first_call["client_name"] == "vibe_cli"
    # Legacy's taxonomy: only the call answering the user's prompt is the main
    # one, so a tool-driven iteration is secondary even though it is agent work.
    assert first_call["call_type"] == "main_call"
    assert tool_loop["call_type"] == "secondary_call"
    assert compaction["call_type"] == "secondary_call"
    # The smart-approve classifier and the background title answer no user
    # prompt, so they never ride the main/secondary pair that request-volume
    # metrics count model turns by.
    assert classify["call_type"] == "smart_approve"
    assert title["call_type"] == "title_generation"
    assert tool_loop == compaction
    assert tool_loop | {"call_type": "main_call"} == first_call
    assert tool_loop | {"call_type": "smart_approve"} == classify
    assert tool_loop | {"call_type": "title_generation"} == title


def test_fork_uses_root_cache_affinity_and_child_request_metadata() -> None:
    """Fork requests keep their own session metadata but reuse root affinity."""

    def holder_for(
        session_id: str, affinity_id: str, parent_session_id: str | None = None
    ) -> tuple[CompletionAttributionHolder, CompletionAffinityHolder]:
        attribution = CompletionAttributionHolder()
        affinity = CompletionAffinityHolder()
        attribution.bind(
            build_completion_attribution(
                TelemetryClient(
                    config_getter=build_test_vibe_config,
                    session_id_getter=lambda: session_id,
                    parent_session_id_getter=lambda: parent_session_id,
                ),
                None,
            )
        )
        affinity.bind(lambda: affinity_id)
        return attribution, affinity

    root_attribution, root_affinity = holder_for(
        "session-root", affinity_id="session-root"
    )
    fork_attribution, fork_affinity = holder_for(
        "session-fork", affinity_id="session-root", parent_session_id="session-root"
    )
    _, unrelated_affinity = holder_for(
        "session-unrelated", affinity_id="session-unrelated"
    )

    fork_metadata = fork_attribution.metadata("agent", 0)
    assert fork_metadata["session_id"] == "session-fork"
    assert fork_metadata["parent_session_id"] == "session-root"
    assert root_attribution.metadata("agent", 0)["session_id"] == "session-root"
    assert root_affinity.affinity_id() == "session-root"
    assert fork_affinity.affinity_id() == "session-root"
    assert unrelated_affinity.affinity_id() == "session-unrelated"
    assert unrelated_affinity.affinity_id() != root_affinity.affinity_id()
