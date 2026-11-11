# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Session API configuration (``AgentConfig``) over the Runtime's own settings.

The Runtime runs a Session from two resolved settings objects: the Core
configuration and the local adapter configuration (model route, provider,
tool approval modes, workspace). The Session API describes the same Session
with one ``AgentConfig``. This module converts between them.

Only the model route converts both ways: the model, its sampling settings, its
limits (context window, output cap, image input) and the compaction threshold.
A caller states the limits it knows, as an Agents SDK caller does on its
completion config, and the Runtime's profile compiler fills in the rest. Every
other ``AgentConfig`` field is reported back as written and refused on write
when it differs, because the Runtime has no way to apply it to a running Session.
"""

from collections.abc import Mapping
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from mistralai_vibe_local_harness.protocol import RustHarnessConfig, RustModelFacts
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    AgentConfig,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._route_profile import (
    compile_route_profile,
    rust_model_facts,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    SessionConfig,
    output_room,
)
from mistralai_vibe_local_harness.vibe.completion_profile import (
    ExplicitFacts,
    InputModality,
)


class AppliedCompletion(BaseModel):
    """The ``AgentConfig.completion`` fields the Runtime applies to a running Session.

    Both sides of ``config/write`` build or read ``completion`` through this
    model, so a renamed field is renamed for both. Other ``completion`` keys
    are ignored here; ``config/write`` refuses a change to them.
    """

    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[InputModality] | None = None

    @classmethod
    def of(
        cls,
        route: LocalModelRoute,
        facts: RustModelFacts,
        *,
        provider: str | None = None,
    ) -> "AppliedCompletion":
        """The fields describing ``route`` and its limits ``facts``.

        ``provider`` is left out of a model switch, which keeps the Session's.
        """
        return cls(
            type=provider,
            model=route.model,
            temperature=route.temperature,
            top_p=route.top_p,
            reasoning_effort=route.thinking,
            max_tokens=route.max_output_tokens,
            context_window=facts.context_window,
            max_output_tokens=route.max_output_tokens,
            input_modalities=[InputModality(item) for item in facts.input_modalities],
        )

    @classmethod
    def from_completion(
        cls, completion: Mapping[str, JsonValue]
    ) -> "AppliedCompletion":
        """Read the applied fields of ``completion``.

        Raises ``SessionConfigRejectedError`` naming each field whose value
        the Runtime cannot apply.
        """
        try:
            return cls.model_validate(completion)
        except ValidationError as invalid:
            raise SessionConfigRejectedError(
                sorted({f"completion.{error['loc'][0]}" for error in invalid.errors()})
            ) from invalid

    def dump(self) -> dict[str, JsonValue]:
        """The fields as ``completion`` keys, leaving out the unset ones."""
        return self.model_dump(mode="json", exclude_none=True)


COMPLETION_FIELDS_APPLIED = frozenset(AppliedCompletion.model_fields)
"""``AgentConfig.completion`` keys the Runtime applies to a running Session."""

_THINKING_LEVELS = frozenset({"off", "low", "medium", "high", "max"})

_DEFAULT_ROUTE = LocalModelRoute()
"""The sampling settings a written ``completion`` falls back to when it omits them."""


class SessionConfigRejectedError(ValueError):
    """A written ``AgentConfig`` changes settings the Runtime cannot apply.

    ``fields`` names each refused field, for example ``completion.base_url`` or
    ``tools``, so the caller can see what to change.
    """

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__(
            "This Runtime cannot apply these AgentConfig changes: " + ", ".join(fields)
        )


def agent_config_from_runtime(
    core: RustHarnessConfig,
    local: LocalRuntimeAdapterConfig,
    *,
    saved: JsonObject | None = None,
) -> AgentConfig:
    """The ``AgentConfig`` describing the Session's current settings.

    A configuration written through the Session API is the starting point, so
    fields this Runtime does not use read back as they were written. The model
    route always reflects what the Session runs now.
    """
    base = AgentConfig.model_validate(saved) if saved is not None else AgentConfig()
    # The Runtime owns the fields it applies: one the route leaves unset, such
    # as ``top_p``, must not read back from an earlier write.
    completion: dict[str, JsonValue] = {
        key: value
        for key, value in base.completion.items()
        if key not in COMPLETION_FIELDS_APPLIED
    }
    completion.update(_completion_from_runtime(core, local))
    return base.model_copy(
        update={
            "completion": completion,
            "instructions": base.instructions or core.system_instructions,
            "workdir": base.workdir or str(local.workspace.cwd),
            "auto_compact_threshold": (
                core.settings.context.model_facts.compaction_threshold
            ),
        }
    )


def session_config_from_agent_config(
    current_core: RustHarnessConfig,
    current_local: LocalRuntimeAdapterConfig,
    current: AgentConfig,
    written: AgentConfig,
) -> SessionConfig:
    """The Runtime settings for ``written``, starting from the current ones.

    The model's limits come from ``written`` where it states them, else from
    the Runtime's profile of the model. Raises ``SessionConfigRejectedError``
    when ``written`` changes a field the Runtime cannot apply, such as the
    provider type, the tools or the hooks.
    """
    refused = _refused_fields(current, written)
    if refused:
        raise SessionConfigRejectedError(refused)
    completion = AppliedCompletion.from_completion(written.completion)
    route = _route_from_completion(current_local.active_model, completion)
    try:
        compiled = compile_route_profile(
            current_local,
            ExplicitFacts(
                context_window=completion.context_window,
                max_output_tokens=completion.max_output_tokens,
                input_modalities=(
                    None
                    if completion.input_modalities is None
                    else tuple(completion.input_modalities)
                ),
                compaction_threshold=written.auto_compact_threshold,
            ),
            route,
        )
    except ValueError as invalid:
        raise SessionConfigRejectedError(["completion"]) from invalid
    facts = compiled.profile.facts
    room = output_room(facts.context_window, facts.compaction_threshold)
    route = replace(
        route, reasoning=compiled.profile.format.reasoning, output_room=room
    )
    # Compaction follows the model unless the Session set its own.
    follows = current_local.compaction_model == current_local.active_model
    local = replace(
        current_local,
        active_model=route,
        compaction_model=route if follows else current_local.compaction_model,
    )
    context = current_core.settings.context.model_copy(
        update={
            "model_facts": rust_model_facts(facts),
            **({"compaction_model_input_modalities": None} if follows else {}),
        }
    )
    core = current_core.model_copy(
        update={
            "settings": current_core.settings.model_copy(update={"context": context})
        }
    )
    return SessionConfig(core=core, local=local)


def _completion_from_runtime(
    core: RustHarnessConfig, local: LocalRuntimeAdapterConfig
) -> dict[str, JsonValue]:
    return AppliedCompletion.of(
        local.active_model, core.settings.context.model_facts, provider=local.provider
    ).dump()


def _route_from_completion(
    route: LocalModelRoute, completion: AppliedCompletion
) -> LocalModelRoute:
    """The route ``completion`` describes, on top of the Session's current one.

    ``config/write`` carries a whole ``AgentConfig``, so a sampling setting or
    output cap absent from ``completion`` returns to the route's default, not
    left as it was. The model is kept: a write names the model it runs.

    ``completion`` carries no price, so the route keeps its current pricing. A
    host that changes the price under a price budget applies the route directly
    instead of writing it here.
    """
    thinking = (
        _DEFAULT_ROUTE.thinking
        if completion.reasoning_effort is None
        else completion.reasoning_effort
    )
    if thinking not in _THINKING_LEVELS:
        raise SessionConfigRejectedError(["completion.reasoning_effort"])
    max_output = (
        completion.max_tokens
        if completion.max_output_tokens is None
        else completion.max_output_tokens
    )
    return replace(
        route,
        model=route.model if completion.model is None else completion.model,
        temperature=(
            _DEFAULT_ROUTE.temperature
            if completion.temperature is None
            else completion.temperature
        ),
        top_p=completion.top_p,
        thinking=thinking,
        max_output_tokens=max_output,
    )


def _refused_fields(current: AgentConfig, written: AgentConfig) -> list[str]:
    refused: list[str] = []
    for key in sorted(set(current.completion) | set(written.completion)):
        if key in COMPLETION_FIELDS_APPLIED and key != "type":
            continue
        if current.completion.get(key) != written.completion.get(key):
            refused.append(f"completion.{key}")
    current_rest = current.model_dump(
        mode="json", exclude={"completion", "auto_compact_threshold"}
    )
    written_rest = written.model_dump(
        mode="json", exclude={"completion", "auto_compact_threshold"}
    )
    refused.extend(
        name
        for name in sorted(written_rest)
        if written_rest[name] != current_rest.get(name)
    )
    return refused


__all__ = [
    "COMPLETION_FIELDS_APPLIED",
    "AppliedCompletion",
    "SessionConfigRejectedError",
    "agent_config_from_runtime",
    "session_config_from_agent_config",
]
