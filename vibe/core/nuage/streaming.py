from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from vibe.core.nuage.events import WorkflowEvent


class StreamEventWorkflowContext(BaseModel):
    namespace: str = ""
    workflow_name: str = ""
    workflow_exec_id: str = ""
    parent_workflow_exec_id: str | None = None
    root_workflow_exec_id: str | None = None


class StreamEvent(BaseModel):
    stream: str = ""
    timestamp_unix_nano: int = Field(default_factory=lambda: time.time_ns())
    data: WorkflowEvent | dict[str, Any]
    workflow_context: StreamEventWorkflowContext = Field(
        default_factory=StreamEventWorkflowContext
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    broker_sequence: int | None = None


class StreamEventsQueryParams(BaseModel):
    workflow_exec_id: str = ""
    start_seq: int = 0
