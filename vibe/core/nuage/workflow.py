from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TERMINATED = "TERMINATED"
    CONTINUED_AS_NEW = "CONTINUED_AS_NEW"
    TIMED_OUT = "TIMED_OUT"
    RETRYING_AFTER_ERROR = "RETRYING_AFTER_ERROR"


class WorkflowExecutionWithoutResultResponse(BaseModel):
    workflow_name: str
    execution_id: str
    parent_execution_id: str | None = None
    root_execution_id: str = ""
    status: WorkflowExecutionStatus | None = None
    start_time: datetime
    end_time: datetime | None = None
    total_duration_ms: int | None = None


class WorkflowExecutionListResponse(BaseModel):
    executions: list[WorkflowExecutionWithoutResultResponse] = Field(
        default_factory=list
    )
    next_page_token: str | None = None


class SignalWorkflowResponse(BaseModel):
    message: str = "Signal accepted"


class UpdateWorkflowResponse(BaseModel):
    update_name: str = ""
    result: Any = None
