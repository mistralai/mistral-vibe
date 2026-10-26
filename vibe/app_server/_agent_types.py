"""Subagent types a session advertises, and the profiles that spawn them.

One resolve, two halves. The Core is told that a subagent type exists, and the
Host is told how to spawn it. A name advertised without a binding is a name the
model can call and the Runtime cannot answer, so both halves are derived here,
from the same source, under the same filter.

Two sources reach this module: the agents a plugin ships, and the agents a user
or a project wrote under an agents directory. They differ only in who owns them,
which the authority digest records so a child admitted under one owner's
definition is not mistaken for a child admitted under another's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from vibe.core.agents.models import BUILTIN_AGENTS
from vibe.core.prompts import MissingPromptFileError, load_system_prompt
from vibe.core.tools.models import ToolPermission

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from mistralai_vibe_local_harness.protocol import (
        RustAgentTypeDefinition,
        RustRuntimeBuiltinToolName,
    )
    from mistralai_vibe_local_harness.vibe._permissions import PermissionResolver
    from mistralai_vibe_local_harness.vibe.plugins import (
        DeclaredAgentTypeProfile,
        DeclaredCompletionRoute,
    )
    from vibe.app_server.models import ConfigIssue
    from vibe.core.agents.manager import AgentManager
    from vibe.core.agents.models import AgentProfile

logger = logging.getLogger(__name__)


def _session_route(_overrides: Mapping[str, Any]) -> DeclaredCompletionRoute | None:
    return None


@dataclass(frozen=True, slots=True)
class AgentToolCatalogue:
    """The tools a profile is measured against, how its calls are decided, and
    the model its children run on.

    Handed in rather than derived here: the catalogue is a function of the
    session's live config, and this module resolves agents, not tools. One
    snapshot serves a whole resolve, so its profiles cannot disagree about which
    tools exist.

    ``permission_resolver`` turns a profile's overrides into the resolver its
    child's calls go through. The ceiling only says whether a tool may run; the
    resolver decides each call to it under the profile's ``[tools.*]`` rules.

    ``completion_route`` turns a profile's overrides into the model its children
    run on, ``None`` for the session's, and raises ``ValueError`` when the
    profile selects a model the session cannot run.
    """

    available: frozenset[str]
    permission_of: Callable[[str], ToolPermission]
    permission_resolver: Callable[[Mapping[str, Any]], PermissionResolver] | None = None
    completion_route: Callable[[Mapping[str, Any]], DeclaredCompletionRoute | None] = (
        _session_route
    )


WORKSPACE_OWNER = "workspace"
"""Owner label for the agents a user or a project wrote, as opposed to a plugin."""


@dataclass(frozen=True, slots=True)
class AgentTypeSource:
    """One agent file, and who it came from."""

    name: str
    profile: AgentProfile
    path: Path
    owner: str
    source_name: str


@dataclass(frozen=True, slots=True)
class AgentTypeRejection:
    """An agent file that cannot become an agent type, and why.

    Handed to the Runtime by name so a spawn of the type reports the reason
    instead of an unknown type.
    """

    name: str
    file: str
    reason: str

    def issue(self) -> ConfigIssue:
        from vibe.app_server.models import ConfigIssue

        return ConfigIssue(
            file=self.file, message=f"Agent '{self.name}': {self.reason}"
        )


@dataclass(frozen=True, slots=True)
class ResolvedAgentTypes:
    """What the Core is told, what the Host binds, and what the user is told."""

    definitions: tuple[RustAgentTypeDefinition, ...]
    profiles: tuple[DeclaredAgentTypeProfile, ...]
    issues: tuple[ConfigIssue, ...]
    permission_resolvers: Mapping[str, PermissionResolver]
    rejections: tuple[AgentTypeRejection, ...] = ()


@dataclass(slots=True)
class _Report:
    issues: list[ConfigIssue] = field(default_factory=list)
    rejections: list[AgentTypeRejection] = field(default_factory=list)

    def reject(self, source: AgentTypeSource, message: str) -> None:
        logger.warning("Agent type %r from %r: %s", source.name, source.owner, message)
        rejection = _rejection(source, message)
        self.issues.append(rejection.issue())
        self.rejections.append(rejection)


def workspace_agent_types(agents: AgentManager) -> tuple[AgentTypeSource, ...]:
    """The subagents a user or a project wrote, in discovery order.

    Built-in subagents are left out. ``get_subagents`` reports ``explore``
    alongside the workspace's own, and the harness already spawns a generic
    child that inherits the parent's prompt and tools, so advertising it again
    would put a second name on behaviour a bare spawn already has. The test is
    on the profile rather than on its name, because a file named
    ``explore.toml`` replaces the built-in entry and a user who writes that file
    is asking for their version.
    """
    sources: list[AgentTypeSource] = []
    for profile in agents.get_subagents():
        if BUILTIN_AGENTS.get(profile.name) is profile:
            continue
        path = profile.source_path
        if path is None:
            # Every discovered profile records the file it was parsed from. One
            # that does not was built in memory, and both halves need a path:
            # the Core shows it to the model, the Host spawns against it.
            logger.warning(
                "Not advertising agent type %r: it has no source file", profile.name
            )
            continue
        sources.append(
            AgentTypeSource(
                name=profile.name,
                profile=profile,
                path=path,
                owner=WORKSPACE_OWNER,
                source_name=path.name,
            )
        )
    return tuple(sources)


def workspace_agent_rejections(agents: AgentManager) -> tuple[AgentTypeRejection, ...]:
    """The agent files discovery could not load at all.

    Their type is unknown, since the file never parsed, so every one is
    reported: a subagent among them would otherwise only be missing.
    """
    return tuple(
        AgentTypeRejection(
            name=failure.name, file=str(failure.path), reason=failure.reason
        )
        for failure in agents.load_failures
    )


def resolve_agent_types(
    sources: Iterable[AgentTypeSource],
    tools: AgentToolCatalogue | None = None,
    *,
    reserved: frozenset[str] = frozenset(),
) -> ResolvedAgentTypes:
    """Turn agent files into what the Core advertises and the Host binds.

    ``tools`` is what a ceiling is measured against, so a caller that only needs
    the advertised half can leave it out and gets no profiles. The filter runs
    the same way either way, so the two halves agree on which agents survive.

    ``reserved`` holds names another owner has already claimed. A name claimed
    twice is worse than a tie to break: the description the model reads would
    come from one owner and the child would run on the other owner's profile and
    ceiling, and Core validates agent types for the whole configuration, so a
    duplicate can invalidate every other agent rather than just these two. A
    source that lands on a reserved name is dropped and reported.
    """
    definitions: list[RustAgentTypeDefinition] = []
    profiles: list[DeclaredAgentTypeProfile] = []
    report = _Report()
    permission_resolvers: dict[str, PermissionResolver] = {}

    for source in sources:
        if source.name in reserved:
            report.reject(source, "an installed plugin already declares that name")
            continue
        accepted, instructions = _accepted_instructions(source, report)
        if not accepted:
            continue
        definition = _definition(source, report)
        if definition is None:
            continue
        if tools is None:
            definitions.append(definition)
            continue
        profile = _profile(source, instructions, tools, report)
        if profile is None:
            # Advertised without a binding, the Host strips the name anyway. Drop
            # both here so the two halves never disagree about what exists.
            continue
        definitions.append(definition)
        profiles.append(profile)
        if tools.permission_resolver is not None:
            permission_resolvers[profile.agent_type] = tools.permission_resolver(
                source.profile.overrides
            )

    return ResolvedAgentTypes(
        definitions=tuple(definitions),
        profiles=tuple(profiles),
        issues=tuple(report.issues),
        permission_resolvers=permission_resolvers,
        rejections=tuple(report.rejections),
    )


def agent_instructions(profile: AgentProfile) -> str | None:
    """The system prompt a subagent runs on, or ``None`` to inherit the parent's.

    A binding carries prompt text rather than a prompt id, so an agent file that
    names one has it read here. An explicit ``instructions`` wins over a named
    prompt: it is the more specific of the two, and the only one a plugin can
    write.
    """
    if profile.instructions:
        return profile.instructions
    prompt_id = profile.overrides.get("system_prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        return None
    return load_system_prompt(prompt_id)


def _accepted_instructions(
    source: AgentTypeSource, report: _Report
) -> tuple[bool, str | None]:
    """Whether one agent file is usable, and the prompt it asks for.

    The two answers travel together because a rejection and "inherits the
    parent's prompt" are both reported as no instructions, and only the first
    means the agent is out.

    Core validates agent types for the whole configuration at once, so one blank
    description invalidates every other agent's capabilities rather than its
    own. Both checks therefore run before anything is built.
    """
    from mistralai_vibe_local_harness.vibe.plugins import MAX_DECLARED_INSTRUCTION_CHARS

    if not source.name.strip() or not source.profile.description.strip():
        report.reject(source, "a name and a description are required")
        return False, None
    if source.profile.config_error is not None:
        # Discovery already reports it, with the file, among the agent files
        # that do not load; a second report here would show it twice.
        return False, None
    if "model" in source.profile.overrides:
        logger.warning(
            "Agent type %r from %r declares 'model', which is not read; it runs on "
            "the session's model unless 'active_model' names an alias from "
            "[[models]]",
            source.name,
            source.owner,
        )
    try:
        instructions = agent_instructions(source.profile)
    except (MissingPromptFileError, ValueError) as error:
        report.reject(source, str(error))
        return False, None
    if instructions is not None and len(instructions) > MAX_DECLARED_INSTRUCTION_CHARS:
        report.reject(
            source,
            f"its instructions are {len(instructions):,} characters long; a "
            f"subagent's instructions are limited to "
            f"{MAX_DECLARED_INSTRUCTION_CHARS:,} characters",
        )
        return False, None
    return True, instructions


def _definition(
    source: AgentTypeSource, report: _Report
) -> RustAgentTypeDefinition | None:
    from mistralai_vibe_local_harness.protocol import RustAgentTypeDefinition

    try:
        return RustAgentTypeDefinition(
            name=source.name,
            description=source.profile.description,
            path=str(source.path),
        )
    except ValidationError as error:
        report.reject(source, _validation_reason(error))
        return None


def _profile(
    source: AgentTypeSource,
    instructions: str | None,
    tools: AgentToolCatalogue,
    report: _Report,
) -> DeclaredAgentTypeProfile | None:
    # Everything the Runtime needs to spawn a child and nothing it would have to be
    # Vibe to read. ``active_model`` travels as the route it resolves to, so the
    # child runs on it; ``safety`` is not carried, since it gates nothing today.
    from mistralai_vibe_local_harness.vibe.plugins import DeclaredAgentTypeProfile
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    overrides = source.profile.overrides
    ceiling = rust_agent_tool_ceiling(
        set(tools.available), tools.permission_of, overrides
    )
    try:
        completion = tools.completion_route(overrides)
    except ValueError as error:
        # Falling back to the session's model would hide the choice the profile
        # made, so a model it cannot have takes the agent out instead.
        report.reject(source, str(error))
        return None
    path = str(source.path)
    try:
        return DeclaredAgentTypeProfile(
            agent_type=source.name,
            description=source.profile.description,
            profile_path=path,
            instructions=instructions,
            tool_ceiling=ceiling,
            # Discovery already validated these against the config schema.
            enabled_skills=overrides.get("enabled_skills", []),
            disabled_skills=overrides.get("disabled_skills", []),
            completion=completion,
            authority_digest=_authority_digest(
                source, path, ceiling, instructions, completion
            ),
        )
    except ValidationError as error:
        report.reject(source, _validation_reason(error))
        return None


def _authority_digest(
    source: AgentTypeSource,
    path: str,
    ceiling: Mapping[RustRuntimeBuiltinToolName, str],
    instructions: str | None,
    completion: DeclaredCompletionRoute | None,
) -> str:
    # Folded into the binding's template digest. A child spawned under an older
    # authority keeps the template it was admitted with, and only the digest tells
    # the two apart.
    authority: dict[str, object] = {
        "owner": source.owner,
        "source": source.source_name,
        "path": path,
        "description": source.profile.description,
        "instructions": instructions,
        "ceiling": dict(sorted(ceiling.items())),
    }
    # Only when set, so a profile on the session's model keeps its digest.
    if completion is not None:
        authority["completion"] = _route_authority(completion)
    payload = json.dumps(authority, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _route_authority(completion: DeclaredCompletionRoute) -> object:
    provider = completion.provider
    return {
        "model": asdict(completion.model),
        "model_facts": None
        if completion.model_facts is None
        else completion.model_facts.model_dump(mode="json"),
        "provider": None
        if provider is None
        else {
            "provider": provider.provider,
            "api_style": provider.api_style,
            "base_url": provider.base_url,
        },
    }


def _rejection(source: AgentTypeSource, message: str) -> AgentTypeRejection:
    return AgentTypeRejection(name=source.name, file=str(source.path), reason=message)


def _validation_reason(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        if item["loc"]
        else item["msg"]
        for item in error.errors()
    )


__all__ = [
    "WORKSPACE_OWNER",
    "AgentToolCatalogue",
    "AgentTypeRejection",
    "AgentTypeSource",
    "ResolvedAgentTypes",
    "agent_instructions",
    "resolve_agent_types",
    "workspace_agent_rejections",
    "workspace_agent_types",
]
