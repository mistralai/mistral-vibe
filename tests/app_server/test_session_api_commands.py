"""The commands the adapter sends through the Session API."""

from __future__ import annotations

import asyncio
from dataclasses import fields, replace
import json
from pathlib import Path
import time
from typing import Any, cast

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustContextSettings,
    RustEnabledAgentTools,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
    RustHarnessSettings,
    RustProvidedToolDefinition,
    RustRuntimeBuiltinToolCall,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
    RustTokenUsage,
    RustToolGroupDefinition,
)
from mistralai_vibe_local_harness.session_protocol import SessionStartParams
from mistralai_vibe_local_harness.vibe import (
    UnifiedHarnessSessionBackendHost,
    _completion,
    _local_actions,
)
from mistralai_vibe_local_harness.vibe._host import _core_config
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    SessionConfig,
)
from tests.app_server._session_api_harness import use_model_calling_one_tool
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    CallbackResult,
    CallbackResultParams,
    TurnStartParams,
)

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _start(session_id: str, text: str) -> TurnStartParams:
    return TurnStartParams(
        session_id=session_id,
        message=[TextContentBlock(text=text)],
        client_user_message_id="user-entry-1",
    )


@pytest.fixture
def model_using_one_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    use_model_calling_one_tool(monkeypatch, call_id="call-model-1")


@pytest.mark.asyncio
async def test_a_model_switch_goes_through_config_write_with_its_limits(
    tmp_path: Path, model_using_one_tool: None
) -> None:
    """*Prepare*: A session promoted by one Turn, with its Session API commands.
    *Do*: Apply a configuration that switches to a smaller, text-only model and
        carries the app server's template task ID, as a Vibe ``/model`` switch
        does. Then apply one that also changes the request headers.
    *Assert*: The first goes through ``config/write`` with the new limits; the
        template task ID does not count as a change. The second changes more
        than the model, so it goes to the Runtime directly.
    """
    # Prepare
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    commands = SessionApiCommands(session)
    await commands.start_turn(_start(session.session_id, "Read a"))
    await session._wait_for_pending_turns()
    core, local = session.core_config, session.adapter_config
    assert core is not None
    assert local is not None
    context = core.settings.context
    small_facts = context.model_facts.model_copy(
        update={
            "context_window": 65_536,
            "compaction_threshold": 32_768,
            "compaction_threshold_max": 40_000,
            "input_modalities": ["text"],
        }
    )
    switched_core = core.model_copy(
        update={
            "task_id": "app-server-template",
            "settings": core.settings.model_copy(
                update={
                    "context": context.model_copy(update={"model_facts": small_facts})
                }
            ),
        }
    )
    small_route = replace(local.active_model, model="small-model")

    # Do
    await commands.apply_config(
        SessionConfig(
            core=switched_core,
            local=replace(
                local, active_model=small_route, compaction_model=small_route
            ),
        )
    )
    after_write = session.adapter_config
    assert after_write is not None
    await commands.apply_config(
        SessionConfig(
            core=switched_core,
            local=replace(
                after_write,
                active_model=replace(after_write.active_model, model="model-c"),
                extra_headers={"x-team": "qa"},
            ),
        )
    )

    # Assert
    view = commands.transport.view
    assert view.agent_config is not None
    written = cast(dict[str, Any], view.agent_config["completion"])
    assert written["model"] == "small-model"
    assert written["context_window"] == 65_536
    assert written["input_modalities"] == ["text"]
    assert session.adapter_config is not None
    assert session.adapter_config.active_model.model == "model-c"
    assert session.adapter_config.extra_headers == {"x-team": "qa"}
    await commands.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_turn_start_reply_without_a_start_time_gets_the_current_time(
    tmp_path: Path, model_using_one_tool: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Session API commands whose ``turn/start`` reply leaves out the
        Turn's start time, as a runtime may while its pre-turn Hook decides.
    *Do*: Start a Turn.
    *Assert*: The started Turn carries the current time, not 0.
    """
    # Prepare
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    commands = SessionApiCommands(session)
    request = commands.transport.request

    async def without_start_time(method: str, params: Any, result_type: Any) -> Any:
        response = await request(method, params, result_type)
        if method != "app_server/session/turn/start":
            return response
        turn = response.turn.model_copy(update={"started_at": None})
        return response.model_copy(update={"turn": turn})

    monkeypatch.setattr(commands.transport, "request", without_start_time)
    before = int(time.time() * 1000)

    # Do
    result = await commands.start_turn(_start(session.session_id, "Read a"))

    # Assert
    assert result.response.started_at >= before
    await session._wait_for_pending_turns()
    await commands.close()
    await host.shutdown()


class _ProviderRequests:
    """Stand in for the Mistral adapter and record what each request carries."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, float | None, int | None]] = []

    async def __call__(
        self,
        _messages: Any,
        _tools: Any,
        config: LocalRuntimeAdapterConfig,
        _credential: Any,
        route: LocalModelRoute,
        **_kwargs: Any,
    ) -> RustCompletionResult:
        self.sent.append((route.model, route.top_p, config.max_tokens))
        return RustCompletionResult(
            parts=[RustTextContentBlock(text="ok")], finish_reason="stop", usage=USAGE
        )


def _route(model: str, window: int, threshold: int, **knobs: Any) -> LocalModelRoute:
    """A model route as the app server derives it, with its output room."""
    return LocalModelRoute(model=model, output_room=window - threshold, **knobs)


def _switch(
    core: Any, local: LocalRuntimeAdapterConfig, route: LocalModelRoute, window: int
) -> SessionConfig:
    """The configuration a Vibe ``/model`` switch pushes: route and facts move."""
    context = core.settings.context
    facts = context.model_facts.model_copy(
        update={
            "context_window": window,
            "compaction_threshold": window // 2,
            "compaction_threshold_max": window // 2,
        }
    )
    return SessionConfig(
        core=core.model_copy(
            update={
                "settings": core.settings.model_copy(
                    update={
                        "context": context.model_copy(update={"model_facts": facts})
                    }
                )
            }
        ),
        local=replace(local, active_model=route, compaction_model=route),
    )


async def _session_on_a_large_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config: Any
) -> tuple[
    UnifiedHarnessSessionBackendHost, Any, SessionApiCommands, _ProviderRequests
]:
    provider = _ProviderRequests()
    monkeypatch.setattr(_completion, "execute_mistral_completion", provider)
    large = _route("large-model", 262_144, 131_072)
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(
            cwd=tmp_path,
            session_api=True,
            active_model=large,
            compaction_model=large,
            **config,
        ),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    commands = SessionApiCommands(session)
    await commands.start_turn(_start(session.session_id, "first"))
    await session._wait_for_pending_turns()
    return host, session, commands, provider


async def _turn(commands: SessionApiCommands, session: Any, text: str) -> None:
    await commands.start_turn(_start(session.session_id, text))
    await session._wait_for_pending_turns()


@pytest.mark.asyncio
async def test_a_switch_back_drops_what_only_the_other_model_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session on a large model that sets no ``top_p`` and no
        output cap, switched through ``config/write`` to a small model that
        sets both.
    *Do*: Switch back to the large model, as a Vibe ``/model`` switch does,
        and run a Turn.
    *Assert*: The request on the large model carries neither the small
        model's ``top_p`` nor its output cap, and the configuration the
        Session API reads back carries neither key.
    """
    # Prepare
    host, session, commands, provider = await _session_on_a_large_model(
        tmp_path, monkeypatch
    )
    core, local = session.core_config, session.adapter_config
    large = local.active_model
    small = _route("small-model", 65_536, 32_768, top_p=0.5, max_output_tokens=1_234)
    await commands.apply_config(_switch(core, local, small, 65_536))
    await _turn(commands, session, "on small")

    # Do
    await commands.apply_config(_switch(core, local, large, 262_144))
    await _turn(commands, session, "back on large")

    # Assert
    assert provider.sent[1] == ("small-model", 0.5, None)
    assert provider.sent[-1] == ("large-model", None, None)
    view = commands.transport.view
    assert view.agent_config is not None
    written = cast(dict[str, Any], view.agent_config["completion"])
    assert written["model"] == "large-model"
    assert "top_p" not in written
    assert "max_tokens" not in written
    assert "max_output_tokens" not in written
    await commands.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_switch_to_a_smaller_model_clamps_the_output_cap_to_its_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session with a 50,000-token output cap for agent and
        compaction requests, on a large model whose window leaves 131,072
        tokens of room at the compaction threshold.
    *Do*: Switch through ``config/write`` to a model with a 65,536-token
        window and a 32,768-token threshold, then run a Turn.
    *Assert*: The request's ``max_tokens`` is held to the 32,768 tokens the
        small model leaves, and compaction, which follows the model, is held
        the same way.
    """
    # Prepare
    host, session, commands, provider = await _session_on_a_large_model(
        tmp_path, monkeypatch, agent_max_tokens=50_000, compaction_max_tokens=50_000
    )
    core, local = session.core_config, session.adapter_config
    small = _route("small-model", 65_536, 32_768)

    # Do
    await commands.apply_config(_switch(core, local, small, 65_536))
    await _turn(commands, session, "on small")

    # Assert
    assert provider.sent[0] == ("large-model", None, 50_000)
    assert provider.sent[-1] == ("small-model", None, 32_768)
    after = session.adapter_config
    assert after is not None
    assert after.active_model.output_room == 32_768
    assert after.compaction_model.output_room == 32_768
    await commands.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_model_switch_goes_through_config_write_with_connector_tools_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A resident session whose Core advertises a connector tool
        group, which the Session adds from its own connector snapshot and an
        app server derivation never carries.
    *Do*: Apply a model switch derived without that group, as a Vibe ``/model``
        switch is.
    *Assert*: The switch goes through ``config/write``, and the Core keeps the
        connector group.
    """
    # Prepare
    host, session, commands, _provider = await _session_on_a_large_model(
        tmp_path, monkeypatch
    )
    derived_core, local = session.core_config, session.adapter_config
    runtime = session._runtime
    connector = RustToolGroupDefinition(
        name="connector_demo",
        description="A connector's tools",
        tools=[
            RustProvidedToolDefinition(
                name="search",
                description="Search",
                input_schema={"type": "object"},
                exposure="programmatic",
            )
        ],
    )
    await runtime.reconfigure_capability_dimension(
        lambda live: live.model_copy(
            update={"tool_groups": [*live.tool_groups, connector]}, deep=True
        )
    )
    small = _route("small-model", 65_536, 32_768)

    # Do
    await commands.apply_config(_switch(derived_core, local, small, 65_536))

    # Assert
    view = commands.transport.view
    assert view.agent_config is not None
    assert view.agent_config["completion"]["model"] == "small-model"  # type: ignore[index]
    live = session.core_config
    assert live is not None
    assert "connector_demo" in [group.name for group in live.capabilities.tool_groups]
    await commands.close()
    await host.shutdown()


class _RootSpawnsWaitingChild:
    """The root spawns one subagent; the subagent waits until released."""

    def __init__(self) -> None:
        self.child_started = asyncio.Event()
        self.child_release = asyncio.Event()
        self.root_completions = 0

    async def __call__(
        self, action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            self.child_started.set()
            await self.child_release.wait()
            return _answer(action, "child done")
        self.root_completions += 1
        if self.root_completions > 1:
            return _answer(action, "done")
        spawn = (
            "async function main() { await tools.agents.spawn("
            '{name: "researcher", instructions: "Follow the message.",'
            ' message: "Investigate"}); return "spawned"; }'
        )
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[
                    RustCompletionResultToolCallPart(
                        id="program-1",
                        name="run_typescript",
                        arguments_json=json.dumps({"code": spawn}),
                    )
                ],
                finish_reason="tool_call",
                usage=USAGE,
            ),
        )


def _answer(action: Any, text: str) -> RustCompletionSucceededEvent:
    return RustCompletionSucceededEvent(
        action_id=action.action_id,
        result=RustCompletionResult(
            parts=[RustTextContentBlock(text=text)], finish_reason="stop", usage=USAGE
        ),
    )


def _subagents_enabled_config() -> Any:
    config = _core_config("runtime-template")
    tools = config.settings.tools.model_copy(
        update={"subagents": RustEnabledAgentTools()}, deep=True
    )
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"tools": tools})},
        deep=True,
    )


@pytest.mark.asyncio
async def test_a_subagent_approval_is_answered_through_the_session_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A root whose subagent waits on an approval for `bash`.
    *Do*: Answer the approval through the root's commands, addressed to the
        subagent.
    *Assert*: The answer reaches the subagent through the Session API transport:
        its tool call is approved and its callback closes.
    """
    # Prepare
    model = _RootSpawnsWaitingChild()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    host = UnifiedHarnessSessionBackendHost(tmp_path / "sessions")
    host.configure_runtime(
        _subagents_enabled_config(),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, roots=(tmp_path,), session_api=True),
    )
    root = await host.start(SessionStartParams(history_limit=20), cwd=str(tmp_path))
    commands = SessionApiCommands(root, host)
    await commands.start_turn(_start(root.session_id, "Delegate"))
    await asyncio.wait_for(model.child_started.wait(), timeout=5)
    [record] = host.child_session_records(root.session_id)
    child = host.live_child(root.session_id, record.child_session_id)
    assert child is not None
    child_turn = child.active_turn_id
    assert child_turn is not None
    approval = asyncio.create_task(
        child._request_approval(
            RustRuntimeBuiltinToolCallAction(
                action_id="approval-action",
                turn_id=child_turn,
                call_id="approval-call",
                call=RustRuntimeBuiltinToolCall(
                    name="file_system.bash", arguments={"command": "echo hi"}
                ),
            )
        )
    )
    while not child.open_callbacks():
        await asyncio.sleep(0)
    [callback] = child.open_callbacks()

    # Do
    await commands.respond_to_callback(
        CallbackResultParams(
            session_id=record.child_session_id,
            result=CallbackResult(
                callback_id=str(callback["callbackId"]),
                output={"decision": {"type": "approve"}},
            ),
        )
    )

    # Assert
    assert (await asyncio.wait_for(approval, timeout=5)).approved
    assert child.open_callbacks() == ()
    model.child_release.set()
    await commands.close()
    await host.shutdown()


# What a model switch through ``config/write`` was designed against. Model
# switches are recognised by comparing the pushed configuration with the
# running one (``_is_model_switch``), so a new field decides, by default, that
# every model switch goes to the runtime directly. Add a field here only once
# you know whether a model switch changes it, and update ``_is_model_switch``
# to match.
_KNOWN_LOCAL_FIELDS = frozenset({
    "active_agent",
    "active_model",
    "affinity_id",
    "agent_max_tokens",
    "api_style",
    "backend",
    "base_url",
    "bypass_approval",
    "classifier_model",
    "classifier_provider",
    "command_environment",
    "compaction_max_tokens",
    "compaction_model",
    "compaction_reasoning_limit_tokens",
    "completion_metadata",
    "correlation_id_sink",
    "credentials",
    "emits_finish_reason",
    "enable_system_trust_store",
    "env",
    "extra_headers",
    "helper_crashes",
    "malformed_tool_calls_sink",
    "max_tokens",
    "model",
    "model_request_sink",
    "permission_resolver",
    "process_authority",
    "project_id",
    "provided_tool_mode",
    "provider",
    "reasoning_field_name",
    "region",
    "request_sent_sink",
    "retry_max_elapsed_time_s",
    "sandbox",
    "session_api",
    "shell",
    "skills",
    "stream_idle_timeout_s",
    "subagent_max_children",
    "subagent_max_concurrent_turns",
    "temperature",
    "thinking",
    "timeout_s",
    "title_model",
    "title_prompt",
    "title_provider",
    "tool_modes",
    "workspace",
})
_KNOWN_CORE_FIELDS = {
    "config": frozenset({
        "capabilities",
        "plugins",
        "settings",
        "system_instructions",
        "task_id",
    }),
    "capabilities": frozenset({
        "agent_types",
        "hook_bindings",
        "knowledge_folders",
        "skills",
        "tool_groups",
    }),
    "settings": frozenset({"context", "tools", "turn"}),
    "context": frozenset({
        "compaction",
        "compaction_model_input_modalities",
        "model_facts",
        "notifications",
    }),
}


def test_the_model_switch_check_knows_every_runtime_config_field() -> None:
    """*Prepare*: The fields of the runtime configuration a push carries.
    *Do*: Compare them with the fields the model-switch check was written for.
    *Assert*: They match, so a new field cannot silently change whether model
        switches go through ``config/write``.
    """
    # Prepare
    local = {field.name for field in fields(LocalRuntimeAdapterConfig)}
    core = {
        "config": set(RustHarnessConfig.model_fields),
        "capabilities": set(RustHarnessCapabilitySet.model_fields),
        "settings": set(RustHarnessSettings.model_fields),
        "context": set(RustContextSettings.model_fields),
    }

    # Do
    new_local = sorted(local - _KNOWN_LOCAL_FIELDS)
    gone_local = sorted(_KNOWN_LOCAL_FIELDS - local)
    changed_core = {
        name: sorted(core[name] ^ known)
        for name, known in _KNOWN_CORE_FIELDS.items()
        if core[name] != known
    }

    # Assert
    assert (new_local, gone_local, changed_core) == ([], [], {})
