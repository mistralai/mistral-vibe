"""The ``start_session`` tool: the attached client creates the session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server.protocol import (
    SessionSpawnCheckout,
    SessionSpawnNewWorktree,
    SessionSpawnParams,
    SessionSpawnResponse,
)
from vibe.config_values import THINKING_LEVELS, ThinkingLevel

if TYPE_CHECKING:
    from vibe.core.agents.manager import AgentManager
    from vibe.core.config.vibe_schema import VibeConfigSchema

START_SESSION_TOOL_NAME = "start_session"

START_SESSION_TOOL_DESCRIPTION = """\
Start a separate coding session that works on a task on its own, next to this \
one. Use it when the user asks for something to be done in another session, or \
for a side task that should not interrupt the current work. Never use it to \
hand off the task you were asked to do: do that task yourself. The new session \
starts with `prompt` as its first message and knows nothing else, so the \
prompt must carry everything it needs: the goal, the \
relevant files, and what done looks like. Write it as the task itself, addressed \
to the session that will do it: it must not ask that session to start or use \
another session. It runs in the same project; choose `placement` 'new_worktree' \
when it will edit files this session is also working on, and 'current_checkout' \
when it should see this session's uncommitted changes. Set `title` only when \
the user names the session: left out, the session names itself from its work. \
It runs like this one, on the same model, thinking level and approval policy; \
set `model` or `reasoning_effort` only when the user asks for one. When you \
tell the user, link the new session as in the result's `message`; do not \
quote its id."""

SPAWN_TIMEOUT_SECONDS = 120.0

_IDEMPOTENCY_NAMESPACE: Final = uuid.UUID("5b0f3c7e-6a1d-4f4e-9c55-2d8f0e7a4b61")


class StartSessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=1,
        description=(
            "The new session's first message: the task itself, self-contained, "
            "written to the session that will do it."
        ),
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The session's name, only when the user gives one; otherwise the "
            "session names itself."
        ),
    )
    placement: Literal["current_checkout", "new_worktree"] = Field(
        description=(
            "'new_worktree' to work in a fresh worktree of the project, or "
            "'current_checkout' to work in this session's checkout."
        )
    )
    # Their descriptions list the host's models: see `start_session_input_schema`.
    model: str | None = Field(default=None, min_length=1)
    reasoning_effort: ThinkingLevel | None = None


@dataclass(frozen=True, slots=True)
class SpawnModel:
    alias: str
    display_name: str | None
    levels: tuple[ThinkingLevel, ...]


def spawn_models(config: VibeConfigSchema) -> tuple[SpawnModel, ...]:
    from vibe.app_server._projection import offered_levels

    return tuple(
        SpawnModel(
            alias=model.alias,
            display_name=model.display_name,
            levels=tuple(offered_levels(config, model)),
        )
        for model in config.available_models().values()
    )


def start_session_input_schema(models: Sequence[SpawnModel]) -> dict[str, Any]:
    """Models are named in descriptions rather than an enum, so a user's
    "opus" can be matched to its alias, and the app-server checks the pick.
    """
    schema = StartSessionArgs.model_json_schema()
    properties: dict[str, Any] = schema["properties"]
    offered = "; ".join(
        f"{model.alias} ({model.display_name})"
        if model.display_name and model.display_name != model.alias
        else model.alias
        for model in models
    )
    properties["model"]["description"] = (
        "The alias of the model to run on, only when the user asks for one: "
        f"{offered}. Left out, it runs on this session's model."
    )
    levels = "; ".join(
        f"{model.alias}: {', '.join(model.levels)}" for model in models if model.levels
    )
    # The standard levels, then any level of a model's own its config lists.
    offered_levels = dict.fromkeys((
        *THINKING_LEVELS,
        *(level for model in models for level in model.levels),
    ))
    properties["reasoning_effort"] = {
        "anyOf": [{"type": "string", "enum": list(offered_levels)}, {"type": "null"}],
        "default": None,
        "description": (
            "How hard the model thinks, only when the user asks, among the levels "
            f"its model offers ({levels}). Left out, it thinks like this session, "
            "or at its model's default level on another model."
        ),
    }
    schema.pop("$defs", None)
    return schema


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """What a session runs on, which a session it starts inherits."""

    model: str
    # None when the config leaves it unset: the model's lowest setting applies.
    reasoning_effort: ThinkingLevel | None
    approval_policy: str


def session_settings(config: VibeConfigSchema, agents: AgentManager) -> SessionSettings:
    model = config.get_active_model()
    return SessionSettings(
        model=model.alias,
        reasoning_effort=model.thinking if model.thinking_is_set else None,
        approval_policy=agents.active_profile.name,
    )


@dataclass(frozen=True, slots=True)
class SessionSpawnOrigin:
    checkout: SessionSpawnCheckout
    settings: SessionSettings
    models: tuple[SpawnModel, ...] = ()
    # Another session started this one and the user has not written to it yet.
    awaits_user: bool = False


class StartSessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str | None
    url: str | None
    message: str


class SessionSpawnError(Exception):
    pass


type SessionOriginResolver = Callable[[str], SessionSpawnOrigin]


class SessionSpawner(Protocol):
    async def spawn_session(
        self, params: SessionSpawnParams
    ) -> SessionSpawnResponse: ...


async def run_start_session(
    args: StartSessionArgs,
    *,
    parent_session_id: str,
    invocation_id: str,
    origin_of: SessionOriginResolver,
    spawner: SessionSpawner,
) -> StartSessionResult:
    """The idempotency key only absorbs a repeated delivery of this one call.
    Recovery never re-runs an interrupted provided tool call (it fails it), and
    a model retry is a new call with a new key.
    """
    origin = origin_of(parent_session_id)
    if origin.awaits_user:
        raise SessionSpawnError(
            "Another session started or messaged this one, so it cannot start "
            "sessions until the user writes to it. Do the task here."
        )
    inherited = origin.settings
    model, reasoning_effort = _model_choice(args, origin)
    params = SessionSpawnParams(
        parent_session_id=parent_session_id,
        prompt=args.prompt,
        title=args.title,
        checkout=(
            SessionSpawnNewWorktree()
            if args.placement == "new_worktree"
            else origin.checkout
        ),
        idempotency_key=str(
            uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"{parent_session_id}/{invocation_id}")
        ),
        model=model,
        reasoning_effort=reasoning_effort,
        approval_policy=inherited.approval_policy,
    )
    try:
        async with asyncio.timeout(SPAWN_TIMEOUT_SECONDS):
            spawned = await spawner.spawn_session(params)
    except TimeoutError as exc:
        # The client keeps going after this, so the session may well exist.
        raise SessionSpawnError(
            f"The client did not confirm the session within "
            f"{SPAWN_TIMEOUT_SECONDS:g} seconds, so it may or may not have "
            "started. Do not start it again before the user has checked their "
            "sessions."
        ) from exc
    title = spawned.title or args.title
    return StartSessionResult(
        # The client's id is the one its peer tools take.
        session_id=spawned.client_session_id or spawned.session_id,
        title=title,
        url=spawned.url,
        message=_started_message(title, spawned.url),
    )


def _model_choice(
    args: StartSessionArgs, origin: SessionSpawnOrigin
) -> tuple[str, ThinkingLevel | None]:
    """A level belongs to its model: on another model, the parent's may not be
    offered, so that model's default applies.
    """
    inherited = origin.settings
    model = args.model or inherited.model
    offered = {candidate.alias: candidate for candidate in origin.models}
    if args.model is not None and args.model not in offered:
        raise SessionSpawnError(
            f"There is no model {args.model!r}. Pick one of: {', '.join(offered)}."
        )
    level = args.reasoning_effort
    if level is None:
        return model, inherited.reasoning_effort if model == inherited.model else None
    levels = offered[model].levels if model in offered else ()
    if levels and level not in levels:
        raise SessionSpawnError(
            f"{model} does not offer {level!r} thinking. Pick one of: "
            f"{', '.join(levels)}."
        )
    return model, level


def _started_message(title: str | None, url: str | None) -> str:
    if url is None:
        return f'Started "{title}"' if title else "Started a new session"
    label = _escape_link_label(title) if title else "the new session"
    return f"Started [{label}]({url})"


def _escape_link_label(label: str) -> str:
    """A bracket in a title would otherwise end the link early."""
    return re.sub(r"([\\\[\]])", r"\\\1", label)


__all__ = [
    "SPAWN_TIMEOUT_SECONDS",
    "START_SESSION_TOOL_DESCRIPTION",
    "START_SESSION_TOOL_NAME",
    "SessionOriginResolver",
    "SessionSettings",
    "SessionSpawnError",
    "SessionSpawnOrigin",
    "SessionSpawner",
    "SpawnModel",
    "StartSessionArgs",
    "StartSessionResult",
    "run_start_session",
    "session_settings",
    "spawn_models",
    "start_session_input_schema",
]
