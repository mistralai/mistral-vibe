from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from vibe.app_server._model import ProtocolModel

type ReviewFileStatus = Literal[
    "modified", "created", "deleted", "binary_or_undecodable"
]
type ReviewDecision = Literal["pending", "keep", "revert"]
type ReviewOpaqueReason = Literal["missing", "binary_or_undecodable"]
type ReviewHunkSide = Literal["additions", "deletions"]


class ReviewAgentOwner(ProtocolModel):
    kind: Literal["agent"] = "agent"
    turn_id: int


class ReviewManualOwner(ProtocolModel):
    kind: Literal["manual"] = "manual"
    index: int


type ReviewOwner = Annotated[
    ReviewAgentOwner | ReviewManualOwner, Field(discriminator="kind")
]


class ReviewRegionRef(ProtocolModel):
    version_index: int = Field(ge=0)
    ordinal: int = Field(ge=0)


class ReviewRegionTarget(ProtocolModel):
    kind: Literal["region"] = "region"
    path: str
    version_index: int
    ordinal: int


class ReviewRegionsTarget(ProtocolModel):
    kind: Literal["regions"] = "regions"
    path: str
    regions: list[ReviewRegionRef]


class ReviewScopeTarget(ProtocolModel):
    kind: Literal["scope"] = "scope"
    owner: ReviewOwner


class ReviewScopeFileTarget(ProtocolModel):
    kind: Literal["scopeFile"] = "scopeFile"
    owner: ReviewOwner
    path: str


class ReviewFileTarget(ProtocolModel):
    kind: Literal["file"] = "file"
    path: str


class ReviewAllTarget(ProtocolModel):
    kind: Literal["all"] = "all"


class ReviewLastTurnsTarget(ProtocolModel):
    kind: Literal["lastTurns"] = "lastTurns"
    count: int


type ReviewTarget = Annotated[
    ReviewRegionTarget
    | ReviewRegionsTarget
    | ReviewScopeTarget
    | ReviewScopeFileTarget
    | ReviewFileTarget
    | ReviewAllTarget
    | ReviewLastTurnsTarget,
    Field(discriminator="kind"),
]


class TextReviewRegion(ProtocolModel):
    kind: Literal["text"] = "text"
    version_index: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    owner: ReviewOwner
    baseline_start: int = Field(ge=0)
    baseline_line_count: int = Field(ge=0)
    current_start: int = Field(ge=0)
    current_line_count: int = Field(ge=0)
    decision: ReviewDecision
    depends_on: list[ReviewRegionRef]


class OpaqueReviewRegion(ProtocolModel):
    kind: Literal["opaque"] = "opaque"
    version_index: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    owner: ReviewOwner
    reason: ReviewOpaqueReason
    decision: ReviewDecision
    depends_on: list[ReviewRegionRef]


type ReviewRegion = Annotated[
    TextReviewRegion | OpaqueReviewRegion, Field(discriminator="kind")
]


class ReviewFile(ProtocolModel):
    path: str
    status: ReviewFileStatus
    regions: list[ReviewRegion]


class ReviewScopeFile(ProtocolModel):
    path: str
    status: ReviewFileStatus
    region_count: int = Field(ge=0)


class ReviewScope(ProtocolModel):
    owner: ReviewOwner
    files: list[ReviewScopeFile]


class ReviewHunk(ProtocolModel):
    side: ReviewHunkSide
    line: int = Field(ge=0)
    regions: list[ReviewRegionRef]
