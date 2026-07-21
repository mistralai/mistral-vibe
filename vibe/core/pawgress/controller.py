from __future__ import annotations

from dataclasses import dataclass

from vibe.core.pawgress.events import Criterion, IslandState, IslandStatus
from vibe.core.pawgress.goal import Goal, GoalStatus
from vibe.core.pawgress.verifier import VerificationResult, run_verification

_ISLAND_STATUS: dict[GoalStatus, IslandStatus] = {
    GoalStatus.WORKING: IslandStatus.WORKING,
    GoalStatus.VERIFYING: IslandStatus.VERIFYING,
    GoalStatus.WAITING: IslandStatus.WAITING,
    GoalStatus.BLOCKED: IslandStatus.BLOCKED,
    GoalStatus.PAUSED: IslandStatus.PAUSED,
    GoalStatus.COMPLETED: IslandStatus.COMPLETED,
}


@dataclass
class ContinuationDecision:
    should_continue: bool
    completed: bool = False
    prompt: str | None = None


class GoalController:
    def __init__(self, goal: Goal, cwd: str | None = None) -> None:
        self._goal = goal
        self._cwd = cwd
        self._paused = False
        self._stopped = False

    @property
    def goal(self) -> Goal:
        return self._goal

    def pause(self) -> None:
        self._paused = True
        self._goal.status = GoalStatus.PAUSED

    def resume(self) -> None:
        self._paused = False
        self._goal.status = GoalStatus.WORKING

    def stop(self) -> None:
        self._stopped = True

    async def record_turn_end(self) -> ContinuationDecision:
        if self._stopped or self._goal.completed:
            return ContinuationDecision(
                should_continue=False, completed=self._goal.completed
            )
        if self._paused:
            return ContinuationDecision(should_continue=False)
        if not self._goal.verify_command:
            self._mark_completed()
            return ContinuationDecision(should_continue=False, completed=True)

        self._goal.status = GoalStatus.VERIFYING
        result = await run_verification(
            self._goal.verify_command, self._goal.repeat, self._cwd
        )
        self._goal.last_pass_count = result.pass_count
        if result.passed:
            self._mark_completed()
            return ContinuationDecision(should_continue=False, completed=True)
        if self._goal.iteration >= self._goal.max_iterations:
            self._goal.status = GoalStatus.BLOCKED
            return ContinuationDecision(should_continue=False)

        self._goal.iteration += 1
        self._goal.status = GoalStatus.WORKING
        return ContinuationDecision(
            should_continue=True, prompt=self._continue_prompt(result)
        )

    def status_line(self) -> str:
        g = self._goal
        if g.completed:
            return "🐾 Pawgress · complete"
        if g.verify_command:
            return f"🐾 Pawgress · {g.status.value} {g.last_pass_count}/{g.repeat}"
        return f"🐾 Pawgress · {g.status.value}"

    def island_state(
        self,
        *,
        cost: float | None = None,
        budget: float | None = None,
        detail: str = "",
        elapsed: str | None = None,
        context_tokens: int | None = None,
        context_max: int | None = None,
        usage_used: int | None = None,
        usage_limit: int | None = None,
        usage_reset_seconds: int | None = None,
    ) -> IslandState:
        g = self._goal
        criteria: list[Criterion] = []
        if g.verify_command:
            criteria.append(
                Criterion(
                    label="Verification",
                    done=g.completed,
                    progress=None if g.completed else f"{g.last_pass_count}/{g.repeat}",
                )
            )
        criteria.extend(Criterion(label=c, done=g.completed) for c in g.constraints)
        return IslandState(
            goal=g.description,
            state=_ISLAND_STATUS[g.status],
            detail=detail,
            criteria=criteria,
            iteration=f"{g.iteration}/{g.max_iterations}",
            elapsed=elapsed,
            cost=cost,
            budget=budget,
            context_tokens=context_tokens,
            context_max=context_max,
            usage_used=usage_used,
            usage_limit=usage_limit,
            usage_reset_seconds=usage_reset_seconds,
            evidence=g.evidence if g.completed else [],
        )

    def _mark_completed(self) -> None:
        self._goal.completed = True
        self._goal.status = GoalStatus.COMPLETED
        self._goal.evidence = self._build_evidence()

    def _build_evidence(self) -> list[str]:
        evidence: list[str] = []
        if self._goal.verify_command:
            evidence.append(
                f"{self._goal.repeat}/{self._goal.repeat} verification runs passed"
            )
        evidence.extend(self._goal.constraints)
        return evidence

    def _continue_prompt(self, result: VerificationResult) -> str:
        return (
            f"The goal '{self._goal.description}' is not yet complete. "
            f"Verification `{self._goal.verify_command}` passed only "
            f"{result.pass_count}/{result.total} runs. Keep working until it passes "
            f"{self._goal.repeat}/{self._goal.repeat}."
        )
