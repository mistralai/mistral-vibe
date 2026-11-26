"""``--auto-approve``/``--yolo`` on a new session, resolved against its policies."""

from __future__ import annotations

from vibe.app_server.protocol import SessionOptions
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, ApprovalPolicyName


def resolve_start_auto_approve(
    options: SessionOptions, agents: AgentManager
) -> SessionOptions:
    """The options a new session starts with when it asks for auto-approve.

    The session-wide bypass becomes the ``auto-approve`` policy, which approves
    the same calls but which the user can leave, whatever the agent. A disabled
    mode, or an override of the policy that no longer bypasses, keeps the flag,
    which the config then clamps and reports.
    """
    if not options.auto_approve:
        return options
    if _bypasses(agents.active_policy):
        return options.model_copy(update={"auto_approve": False})
    target = agents.available_policies.get(ApprovalPolicyName.AUTO_APPROVE)
    if target is None or not _bypasses(target):
        return options
    update: dict[str, object] = {
        "approval_policy": ApprovalPolicyName.AUTO_APPROVE.value,
        "auto_approve": False,
    }
    if options.agent is not None and options.agent in agents.available_policies:
        update["agent"] = None
    return options.model_copy(update=update)


def _bypasses(profile: AgentProfile) -> bool:
    return profile.overrides.get("bypass_tool_permissions") is True
