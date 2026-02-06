from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.middleware import MiddlewareAction, MiddlewareResult, ResetReason
from vibe.core.orchestrator.architect import Architect
from vibe.core.orchestrator.persistence import PersistenceManager
from vibe.core.orchestrator.planning import PlanningSystem

if TYPE_CHECKING:
    from vibe.core.middleware import ConversationContext

class Orchestrator:
    def __init__(self, workdir: Path = Path(".")) -> None:
        self.workdir = workdir
        self.persistence = PersistenceManager(workdir)
        self.planning = PlanningSystem(workdir)
        self.architect = Architect(workdir)

    def initialize_project(self) -> None:
        self.planning.ensure_brainfile()
        self.architect.ensure_architecture_doc()
        # Initialize state if needed

class OrchestratorMiddleware:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        active_task = self.orchestrator.planning.get_active_task()
        state = self.orchestrator.persistence.load_state()

        spec_warning = ""
        if not self.orchestrator.architect.validate_spec():
             spec_warning = "\n<warning>SPEC.md is missing or empty. You must define the specification in SPEC.md before modifying code.</warning>"

        context_msg = f"""
<orchestrator_context>
Current Phase: {state['phase']}
Progress: {state['progress']}%
Active Task: {active_task or "None (Check brainfile.md)"}
{spec_warning}

Remember to use <ai_plan>, <ai_notes>, and <ai_review> tags in your response to update the plan and log your reasoning.
</orchestrator_context>
"""
        return MiddlewareResult(
            action=MiddlewareAction.INJECT_MESSAGE,
            message=context_msg
        )

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        if not context.messages:
            return MiddlewareResult()

        last_msg = context.messages[-1]
        if last_msg.role == "assistant" and last_msg.content:
            # Update planning from reflection tags
            self.orchestrator.planning.update_from_reflection(last_msg.content)

            # Log decision if ai_plan is present, treating it as a significant decision point
            if "<ai_plan>" in last_msg.content:
                self.orchestrator.persistence.log_decision(
                     context=last_msg.content,
                     verdict="Action planned/taken based on ai_plan."
                )

        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass
