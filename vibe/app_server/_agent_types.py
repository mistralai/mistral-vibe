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

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import TYPE_CHECKING

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
    from mistralai_vibe_local_harness.vibe.plugins import DeclaredAgentTypeProfile

    from vibe.app_server.models import ConfigIssue
    from vibe.core.agents.manager import AgentManager
    from vibe.core.agents.models import AgentProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentToolCatalogue:
    """The tools a ceiling is measured against.

    Handed in rather than derived here: the catalogue is a function of the
    session's live config, and this module resolves agents, not tools. One
    snapshot serves a whole resolve, so its profiles cannot disagree about which
    tools exist.
    """

    available: frozenset[str]
    permission_of: Callable[[str], ToolPermission]


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
class ResolvedAgentTypes:
    """What the Core is told, what the Host binds, and what the user is told."""

    definitions: tuple[RustAgentTypeDefinition, ...]
    profiles: tuple[DeclaredAgentTypeProfile, ...]
    issues: tuple[ConfigIssue, ...]


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
    issues: list[ConfigIssue] = []

    for source in sources:
        if source.name in reserved:
            issues.append(
                _issue(source, "an installed plugin already declares that name")
            )
            continue
        accepted, instructions = _accepted_instructions(source, issues)
        if not accepted:
            continue
        definition = _definition(source, issues)
        if definition is None:
            continue
        if tools is None:
            definitions.append(definition)
            continue
        profile = _profile(source, instructions, tools, issues)
        if profile is None:
            # Advertised without a binding, the Host strips the name anyway. Drop
            # both here so the two halves never disagree about what exists.
            continue
        definitions.append(definition)
        profiles.append(profile)

    return ResolvedAgentTypes(
        definitions=tuple(definitions), profiles=tuple(profiles), issues=tuple(issues)
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
    source: AgentTypeSource, issues: list[ConfigIssue]
) -> tuple[bool, str | None]:
    """Whether one agent file is usable, and the prompt it asks for.

    The two answers travel together because a rejection and "inherits the
    parent's prompt" are both reported as no instructions, and only the first
    means the agent is out.

    Core validates agent types for the whole configuration at once, so one blank
    description invalidates every other agent's capabilities rather than its
    own. Both checks therefore run before anything is built.
    """
    if not source.name.strip() or not source.profile.description.strip():
        issues.append(_issue(source, "a name and a description are required"))
        return False, None
    for key in ("active_model", "model"):
        if key in source.profile.overrides:
            logger.warning(
                "Agent type %r from %r declares %r; it runs on the session's "
                "model instead",
                source.name,
                source.owner,
                key,
            )
    try:
        return True, agent_instructions(source.profile)
    except (MissingPromptFileError, ValueError) as error:
        issues.append(_issue(source, str(error)))
        return False, None


def _definition(
    source: AgentTypeSource, issues: list[ConfigIssue]
) -> RustAgentTypeDefinition | None:
    from mistralai_vibe_local_harness.protocol import RustAgentTypeDefinition

    try:
        return RustAgentTypeDefinition(
            name=source.name,
            description=source.profile.description,
            path=str(source.path),
        )
    except ValidationError as error:
        issues.append(_issue(source, str(error)))
        return None


def _profile(
    source: AgentTypeSource,
    instructions: str | None,
    tools: AgentToolCatalogue,
    issues: list[ConfigIssue],
) -> DeclaredAgentTypeProfile | None:
    # Everything the Runtime needs to spawn a child and nothing it would have to be
    # Vibe to read. ``active_model`` and ``safety`` are deliberately not carried: the
    # first is a widening, since the policy ceiling grants exactly one completion, and
    # the second gates nothing today.
    from mistralai_vibe_local_harness.vibe.plugins import DeclaredAgentTypeProfile

    from vibe.app_server._runtime import (
        agent_ceiling_downgrades,
        rust_agent_tool_ceiling,
    )

    overrides = source.profile.overrides
    available = set(tools.available)
    ceiling = rust_agent_tool_ceiling(available, tools.permission_of, overrides)
    if downgraded := agent_ceiling_downgrades(
        available, tools.permission_of, overrides
    ):
        issues.append(
            _issue(
                source,
                f"{', '.join(downgraded)} narrowed by an allowlist the child cannot "
                "carry, so it asks for approval instead of running unattended",
            )
        )
    path = str(source.path)
    try:
        return DeclaredAgentTypeProfile(
            agent_type=source.name,
            description=source.profile.description,
            profile_path=path,
            instructions=instructions,
            tool_ceiling=ceiling,
            authority_digest=_authority_digest(source, path, ceiling, instructions),
        )
    except ValidationError as error:
        issues.append(_issue(source, str(error)))
        return None


def _authority_digest(
    source: AgentTypeSource,
    path: str,
    ceiling: Mapping[RustRuntimeBuiltinToolName, str],
    instructions: str | None,
) -> str:
    # Folded into the binding's template digest. A child spawned under an older
    # authority keeps the template it was admitted with, and only the digest tells
    # the two apart.
    payload = json.dumps(
        {
            "owner": source.owner,
            "source": source.source_name,
            "path": path,
            "description": source.profile.description,
            "instructions": instructions,
            "ceiling": dict(sorted(ceiling.items())),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _issue(source: AgentTypeSource, message: str) -> ConfigIssue:
    from vibe.app_server.models import ConfigIssue

    logger.warning("Agent type %r from %r: %s", source.name, source.owner, message)
    return ConfigIssue(
        file=str(source.path), message=f"Agent '{source.name}': {message}"
    )


__all__ = [
    "WORKSPACE_OWNER",
    "AgentToolCatalogue",
    "AgentTypeSource",
    "ResolvedAgentTypes",
    "agent_instructions",
    "resolve_agent_types",
    "workspace_agent_types",
]
