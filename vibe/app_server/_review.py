from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    EmptyResponse,
    ProtocolErrorCode,
    ReviewBaselineParams,
    ReviewBaselineResponse,
    ReviewHunksParams,
    ReviewHunksResponse,
    ReviewMutationParams,
    ReviewStateParams,
    ReviewStateResponse,
    ReviewTurnDiffParams,
    ReviewTurnDiffResponse,
)
from vibe.app_server.review import (
    OpaqueReviewRegion,
    ReviewAgentOwner,
    ReviewAllTarget,
    ReviewDecision,
    ReviewFile,
    ReviewFileStatus,
    ReviewFileTarget,
    ReviewHunk,
    ReviewLastTurnsTarget,
    ReviewManualOwner,
    ReviewOpaqueReason,
    ReviewOwner,
    ReviewRegionRef,
    ReviewRegionsTarget,
    ReviewRegionTarget,
    ReviewScope,
    ReviewScopeFile,
    ReviewScopeFileTarget,
    ReviewScopeTarget,
    ReviewTarget,
    TextReviewRegion,
)
from vibe.core.checkpoints import AgentTurn, Decision, ManualEdit, OpaqueReason, Owner
from vibe.core.review import (
    AllTarget as CoreAllTarget,
    FileTarget as CoreFileTarget,
    LastTurnsTarget as CoreLastTurnsTarget,
    OpaqueReviewRegion as CoreOpaqueReviewRegion,
    RegionsTarget as CoreRegionsTarget,
    RegionTarget as CoreRegionTarget,
    ReviewError,
    ReviewFileStatus as CoreReviewFileStatus,
    ReviewHunk as CoreReviewHunk,
    ReviewManager,
    ReviewRegion as CoreReviewRegion,
    ReviewState as CoreReviewState,
    ReviewTarget as CoreReviewTarget,
    ScopeFileTarget as CoreScopeFileTarget,
    ScopeTarget as CoreScopeTarget,
)


class ReviewRequestHandler:
    def __init__(
        self,
        review: ReviewManager,
        require_session: Callable[[str], None],
        require_idle: Callable[[], None],
    ) -> None:
        self._review = review
        self._require_session = require_session
        self._require_idle = require_idle

    def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "review/approve":
                params = validate_wire(ReviewMutationParams, raw_params)
                self._require_session(params.session_id)
                response: ProtocolModel = self._mutate(
                    params.target, self._review.approve_review
                )
            case "review/state":
                params = validate_wire(ReviewStateParams, raw_params)
                self._require_session(params.session_id)
                response = _project_state(self._review.review_state())
            case "review/baseline":
                params = validate_wire(ReviewBaselineParams, raw_params)
                self._require_session(params.session_id)
                response = ReviewBaselineResponse(
                    content=self._review.baseline_text(params.path)
                )
            case "review/turnDiff":
                params = validate_wire(ReviewTurnDiffParams, raw_params)
                self._require_session(params.session_id)
                diff = self._review.scope_file_diff(
                    params.path, _core_owner(params.owner)
                )
                response = ReviewTurnDiffResponse(
                    status=_project_status(diff.status),
                    baseline=diff.baseline,
                    current=diff.current,
                )
            case "review/hunks":
                params = validate_wire(ReviewHunksParams, raw_params)
                self._require_session(params.session_id)
                owner = _core_owner(params.owner) if params.owner is not None else None
                response = ReviewHunksResponse(
                    hunks=[
                        _project_hunk(hunk)
                        for hunk in self._review.file_hunks(params.path, owner)
                    ]
                )
            case "review/revert":
                params = validate_wire(ReviewMutationParams, raw_params)
                self._require_session(params.session_id)
                response = self._mutate(params.target, self._review.revert_review)
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    def _mutate(
        self, target: ReviewTarget, mutation: Callable[[CoreReviewTarget], list[str]]
    ) -> EmptyResponse:
        self._require_idle()
        try:
            mutation(_core_target(target))
        except ReviewError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        return EmptyResponse()


def _project_state(state: CoreReviewState) -> ReviewStateResponse:
    return ReviewStateResponse(
        files=[
            ReviewFile(
                path=file.path,
                status=_project_status(file.status),
                regions=[_project_region(region) for region in file.regions],
            )
            for file in state.files
        ],
        scopes=[
            ReviewScope(
                owner=_project_owner(scope.owner),
                files=[
                    ReviewScopeFile(
                        path=file.path,
                        status=_project_status(file.status),
                        region_count=file.region_count,
                    )
                    for file in scope.files
                ],
            )
            for scope in state.scopes
        ],
    )


def _project_region(region: CoreReviewRegion) -> TextReviewRegion | OpaqueReviewRegion:
    owner = _project_owner(region.owner)
    decision = _project_decision(region.decision)
    depends_on = [
        ReviewRegionRef(version_index=version_index, ordinal=ordinal)
        for version_index, ordinal in region.depends_on
    ]
    if isinstance(region, CoreOpaqueReviewRegion):
        return OpaqueReviewRegion(
            version_index=region.version_index,
            ordinal=region.ordinal,
            owner=owner,
            reason=_project_opaque_reason(region.reason),
            decision=decision,
            depends_on=depends_on,
        )
    return TextReviewRegion(
        version_index=region.version_index,
        ordinal=region.ordinal,
        owner=owner,
        baseline_start=region.baseline_start,
        baseline_line_count=region.baseline_line_count,
        current_start=region.current_start,
        current_line_count=region.current_line_count,
        decision=decision,
        depends_on=depends_on,
    )


def _project_hunk(hunk: CoreReviewHunk) -> ReviewHunk:
    return ReviewHunk(
        side=hunk.side,
        line=hunk.line,
        regions=[
            ReviewRegionRef(version_index=version_index, ordinal=ordinal)
            for version_index, ordinal in hunk.regions
        ],
    )


def _project_owner(owner: Owner) -> ReviewOwner:
    match owner:
        case AgentTurn(turn_id=turn_id):
            return ReviewAgentOwner(turn_id=turn_id)
        case ManualEdit(index=index):
            return ReviewManualOwner(index=index)


def _core_owner(owner: ReviewOwner) -> Owner:
    match owner:
        case ReviewAgentOwner(turn_id=turn_id):
            return AgentTurn(turn_id)
        case ReviewManualOwner(index=index):
            return ManualEdit(index)


def _core_target(target: ReviewTarget) -> CoreReviewTarget:
    match target:
        case ReviewRegionTarget(
            path=path, version_index=version_index, ordinal=ordinal
        ):
            core_target: CoreReviewTarget = CoreRegionTarget(
                path=path, version_index=version_index, ordinal=ordinal
            )
        case ReviewRegionsTarget(path=path, regions=regions):
            core_target = CoreRegionsTarget(
                path=path,
                regions=tuple(
                    (region.version_index, region.ordinal) for region in regions
                ),
            )
        case ReviewScopeTarget(owner=owner):
            core_target = CoreScopeTarget(owner=_core_owner(owner))
        case ReviewScopeFileTarget(owner=owner, path=path):
            core_target = CoreScopeFileTarget(owner=_core_owner(owner), path=path)
        case ReviewFileTarget(path=path):
            core_target = CoreFileTarget(path=path)
        case ReviewAllTarget():
            core_target = CoreAllTarget()
        case ReviewLastTurnsTarget(count=count):
            core_target = CoreLastTurnsTarget(count=count)
    return core_target


def _project_status(status: CoreReviewFileStatus) -> ReviewFileStatus:
    match status:
        case CoreReviewFileStatus.MODIFIED:
            return "modified"
        case CoreReviewFileStatus.CREATED:
            return "created"
        case CoreReviewFileStatus.DELETED:
            return "deleted"
        case CoreReviewFileStatus.BINARY_OR_UNDECODABLE:
            return "binary_or_undecodable"


def _project_decision(decision: Decision) -> ReviewDecision:
    match decision:
        case Decision.PENDING:
            return "pending"
        case Decision.KEEP:
            return "keep"
        case Decision.REVERT:
            return "revert"


def _project_opaque_reason(reason: OpaqueReason) -> ReviewOpaqueReason:
    match reason:
        case OpaqueReason.MISSING:
            return "missing"
        case OpaqueReason.BINARY_OR_UNDECODABLE:
            return "binary_or_undecodable"
