"""The ``goal/*`` API and the model's goal tools.

The app server owns the goal: clients render ``PublicSession.goal`` and send
``goal/*`` requests, the model reads and ends the goal through its tools. Every
mutation goes through one store, persisted before it returns.
"""

from __future__ import annotations

from html import escape
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._goal_engine import with_running_turns
from vibe.app_server._goal_state import (
    KEEP_BUDGET,
    GoalError,
    GoalSetRequest,
    ModelGoalStatus,
    apply_model_status,
    apply_user_set,
    create_goal,
    is_unfinished,
)
from vibe.app_server._goal_store import GoalAccounting, GoalStoreError, UnifiedGoalStore
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server._time import now_ms
from vibe.app_server.models import GoalStatus, PublicGoal
from vibe.app_server.protocol import (
    GoalClearParams,
    GoalClearResponse,
    GoalGetParams,
    GoalGetResponse,
    GoalSetParams,
    GoalSetResponse,
    ProtocolErrorCode,
)

__all__ = [
    "CREATE_GOAL_TOOL_NAME",
    "GET_GOAL_TOOL_NAME",
    "GOAL_TOOL_NAMES",
    "UPDATE_GOAL_TOOL_NAME",
    "CreateGoalArgs",
    "GetGoalArgs",
    "GoalChange",
    "GoalController",
    "GoalHost",
    "GoalToolResult",
    "UpdateGoalArgs",
]

GET_GOAL_TOOL_NAME = "get_goal"
CREATE_GOAL_TOOL_NAME = "create_goal"
UPDATE_GOAL_TOOL_NAME = "update_goal"
GOAL_TOOL_NAMES = (GET_GOAL_TOOL_NAME, CREATE_GOAL_TOOL_NAME, UPDATE_GOAL_TOOL_NAME)

type GoalChangeSource = Literal["user", "model"]


class GoalChange(BaseModel):
    """One committed change, for the host to publish and tell the model."""

    model_config = ConfigDict(frozen=True)

    source: GoalChangeSource
    action: Literal["set", "edit", "pause", "resume", "clear", "complete", "block"]
    previous: PublicGoal | None
    goal: PublicGoal | None


class GoalHost(Protocol):
    """What ``GoalController`` needs of its session."""

    def require_session(self, session_id: str) -> None:
        """Raise if ``session_id`` is not this root session."""
        ...

    def goals_available(self) -> bool:
        """Whether this session may hold a goal (interactive, not headless)."""
        ...

    def max_token_budget(self) -> int | None:
        """``max_goal_token_budget``, read per call so ``/config`` applies."""
        ...

    def goal_changed(self, change: GoalChange) -> None:
        """Publish a committed change; never raises."""
        ...


class GetGoalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGoalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(
        description="The objective, in the user's words. Concrete and checkable."
    )
    token_budget: int | None = Field(
        default=None,
        description="Token budget for the goal. Omit unless the user asked for one.",
    )


class UpdateGoalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ModelGoalStatus = Field(
        description=(
            "complete: every requirement is met and checked. blocked: the same "
            "blocker stopped progress for 3 goal turns in a row and only the user "
            "or an external change can lift it. paused: only when the user "
            "explicitly asked to pause the goal."
        )
    )


class GoalToolResult(BaseModel):
    goal: PublicGoal | None
    remaining_tokens: int | None = None
    instructions: str | None = None


GET_GOAL_TOOL_DESCRIPTION = (
    "Read the session's current goal: objective, status, token usage and budget."
)
CREATE_GOAL_TOOL_DESCRIPTION = (
    "Create a durable goal that Vibe keeps working on between turns until it is "
    "complete. Use it only when the user explicitly asks for a goal; never infer "
    "a goal from an ordinary task. Fails when an unfinished goal exists; never "
    "end that goal to make room for a new one."
)
UPDATE_GOAL_TOOL_DESCRIPTION = (
    "End the active goal. Mark it complete only after checking every requirement "
    "of the objective against real evidence. Mark it blocked only when the same "
    "blocker stopped progress for 3 goal turns in a row. Pause it only when the "
    "user explicitly asked. Never end a goal to make room for another one. Any "
    "other change belongs to the user."
)

_COMPLETION_REPORT = (
    "The goal is complete. In your final answer, report each requirement of the "
    "objective with the evidence that it is met, then the tokens and time used."
)


class GoalController:
    def __init__(self, host: GoalHost, store: UnifiedGoalStore) -> None:
        self._host = host
        self._store = store

    @property
    def goal(self) -> PublicGoal | None:
        return self._store.goal

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        try:
            match method:
                case "goal/get":
                    get = validate_wire(GoalGetParams, raw_params)
                    self._host.require_session(get.session_id)
                    response: ProtocolModel = GoalGetResponse(
                        goal=with_running_turns(
                            self._store.goal, self._store.accounting
                        )
                    )
                case "goal/set":
                    response = await self._set(validate_wire(GoalSetParams, raw_params))
                case "goal/clear":
                    response = await self._clear(
                        validate_wire(GoalClearParams, raw_params)
                    )
                case _:
                    raise method_not_found(method)
        except GoalError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT
                if exc.kind == "conflict"
                else ProtocolErrorCode.INVALID_PARAMS,
                str(exc),
            ) from exc
        except GoalStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc
        return DispatchResult(response)

    async def _set(self, params: GoalSetParams) -> GoalSetResponse:
        self._require_available(params.session_id)
        request = GoalSetRequest(
            objective=params.objective,
            status=params.status,
            token_budget=(
                params.token_budget
                if "token_budget" in params.model_fields_set
                else KEEP_BUDGET
            ),
            replace=params.replace,
            expected_goal_id=params.expected_goal_id,
            expected_revision=params.expected_revision,
        )
        changes: list[GoalChange] = []
        live: list[PublicGoal | None] = []
        max_budget = self._host.max_token_budget()

        def mutation(
            current: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            goal = apply_user_set(
                current, request, max_budget=max_budget, now_ms=now_ms()
            )
            if goal == current:
                live.append(with_running_turns(current, accounting))
                return current, accounting
            if current is None or goal.goal_id != current.goal_id:
                accounting = GoalAccounting()
            elif goal.status is GoalStatus.ACTIVE and current.status is not (
                GoalStatus.ACTIVE
            ):
                accounting = accounting.model_copy(
                    update={"no_progress_streak": 0, "failure_streak": 0}
                )
            changes.append(
                GoalChange(
                    source="user",
                    action=_user_action(current, goal),
                    previous=current,
                    goal=goal,
                )
            )
            live.append(with_running_turns(goal, accounting))
            return goal, accounting

        # The goal as clients see it, its running turns counted.
        result = await self._store.mutate(
            mutation,
            receipt_key=_receipt_key("set", params.idempotency_key),
            respond=lambda goal: GoalSetResponse(
                goal=_required(live[-1] if live else goal)
            ),
            encode=lambda response: response.model_dump(mode="json"),
            decode=lambda value: GoalSetResponse.model_validate(value),
        )
        for change in changes:
            self._host.goal_changed(change)
        return result

    async def _clear(self, params: GoalClearParams) -> GoalClearResponse:
        self._require_available(params.session_id)
        changes: list[GoalChange] = []

        def mutation(
            current: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            if current is None:
                return None, accounting
            if current.goal_id != params.expected_goal_id:
                raise GoalError(
                    "conflict", "The goal was replaced. Reload it and try again."
                )
            changes.append(
                GoalChange(source="user", action="clear", previous=current, goal=None)
            )
            return None, GoalAccounting()

        result = await self._store.mutate(
            mutation,
            receipt_key=_receipt_key("clear", params.idempotency_key),
            respond=lambda _goal: GoalClearResponse(cleared=bool(changes)),
            encode=lambda response: response.model_dump(mode="json"),
            decode=lambda value: GoalClearResponse.model_validate(value),
        )
        for change in changes:
            self._host.goal_changed(change)
        return result

    async def run_tool(
        self,
        tool_name: str,
        arguments: object,
        *,
        call_id: str,
        turn_id: str | None = None,
    ) -> GoalToolResult:
        """Run one model goal tool. A ``GoalError`` reaches the model as a failure.

        ``turn_id`` is the turn that called it. Results count the goal's running
        turns, so the model reports what the goal really used.
        """
        if not self._host.goals_available():
            raise GoalError("invalid", "Goals are not available in this session.")
        match tool_name:
            case "get_goal":
                GetGoalArgs.model_validate(arguments)
                return _tool_result(
                    with_running_turns(self._store.goal, self._store.accounting)
                )
            case "create_goal":
                return await self._create_by_model(
                    CreateGoalArgs.model_validate(arguments), call_id=call_id
                )
            case "update_goal":
                return await self._update_by_model(
                    UpdateGoalArgs.model_validate(arguments),
                    call_id=call_id,
                    turn_id=turn_id,
                )
            case _:
                raise GoalError("invalid", f"Unknown goal tool: {tool_name}")

    async def _create_by_model(
        self, args: CreateGoalArgs, *, call_id: str
    ) -> GoalToolResult:
        changes: list[GoalChange] = []
        max_budget = self._host.max_token_budget()

        def mutation(
            current: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            if current is not None and is_unfinished(current):
                raise GoalError(
                    "conflict",
                    "An unfinished goal already exists. Only the user can replace it: "
                    "ask them to run /goal with the new objective. Do not mark the "
                    "existing goal complete, blocked or paused to make room.",
                )
            goal = create_goal(
                args.objective,
                token_budget=args.token_budget,
                max_budget=max_budget,
                now_ms=now_ms(),
            )
            changes.append(
                GoalChange(source="model", action="set", previous=current, goal=goal)
            )
            return goal, GoalAccounting()

        # A new goal has no turn charged yet: the stored goal is the live one.
        result = await self._store.mutate(
            mutation,
            receipt_key=f"tool:{call_id}",
            respond=_tool_result,
            encode=lambda result: result.model_dump(mode="json"),
            decode=GoalToolResult.model_validate,
        )
        for change in changes:
            self._host.goal_changed(change)
        return result

    async def _update_by_model(
        self, args: UpdateGoalArgs, *, call_id: str, turn_id: str | None
    ) -> GoalToolResult:
        changes: list[GoalChange] = []
        live: list[PublicGoal | None] = []

        def mutation(
            current: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            if current is None:
                raise GoalError("invalid", "There is no goal to update.")
            if args.status == "complete":
                _require_current_objective(current, accounting, turn_id)
            goal = apply_model_status(current, args.status, now_ms=now_ms())
            live.append(with_running_turns(goal, accounting))
            changes.append(
                GoalChange(
                    source="model",
                    action=_MODEL_ACTIONS[args.status],
                    previous=current,
                    goal=goal,
                )
            )
            return goal, accounting

        result = await self._store.mutate(
            mutation,
            receipt_key=f"tool:{call_id}",
            respond=lambda goal: _tool_result(
                live[-1] if live else goal,
                instructions=(
                    _COMPLETION_REPORT if args.status == "complete" else None
                ),
            ),
            encode=lambda result: result.model_dump(mode="json"),
            decode=GoalToolResult.model_validate,
        )
        for change in changes:
            self._host.goal_changed(change)
        return result

    def _require_available(self, session_id: str) -> None:
        self._host.require_session(session_id)
        if not self._host.goals_available():
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED, "Goals need an interactive session."
            )


_MODEL_ACTIONS: dict[ModelGoalStatus, Literal["complete", "block", "pause"]] = {
    "complete": "complete",
    "blocked": "block",
    "paused": "pause",
}


def _user_action(
    previous: PublicGoal | None, goal: PublicGoal
) -> Literal["set", "edit", "pause", "resume"]:
    if previous is None or previous.goal_id != goal.goal_id:
        return "set"
    if goal.status is GoalStatus.PAUSED and previous.status is not GoalStatus.PAUSED:
        return "pause"
    if goal.status is GoalStatus.ACTIVE and previous.status is not GoalStatus.ACTIVE:
        return "resume"
    return "edit"


def _require_current_objective(
    goal: PublicGoal, accounting: GoalAccounting, turn_id: str | None
) -> None:
    """Refuse a completion from a turn that worked on an older objective.

    The user may edit the objective while a turn runs; the model only reads
    the edit before its next turn, so it would check the wrong requirements.
    """
    bound = accounting.turns.get(turn_id) if turn_id is not None else None
    if (
        bound is None
        or bound.goal_id != goal.goal_id
        or bound.objective_revision >= goal.objective_revision
    ):
        return
    raise GoalError(
        "conflict",
        "The user changed the goal objective while you worked (now revision "
        f"{goal.objective_revision}), so the goal is not complete yet. Check "
        "every requirement of the new objective, then call update_goal again:\n"
        f"<goal_objective>\n{escape(goal.objective, quote=False)}\n"
        "</goal_objective>",
    )


def _tool_result(
    goal: PublicGoal | None, *, instructions: str | None = None
) -> GoalToolResult:
    remaining = (
        None
        if goal is None or goal.token_budget is None
        else max(0, goal.token_budget - goal.tokens_used)
    )
    return GoalToolResult(
        goal=goal, remaining_tokens=remaining, instructions=instructions
    )


def _required(goal: PublicGoal | None) -> PublicGoal:
    assert goal is not None, "goal/set always leaves a goal"
    return goal


def _receipt_key(method: str, idempotency_key: str | None) -> str | None:
    return None if idempotency_key is None else f"{method}:{idempotency_key}"
