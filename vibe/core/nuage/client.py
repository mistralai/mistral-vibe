from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
import json
from typing import Any

import httpx
from pydantic import BaseModel

from vibe.core.logger import logger
from vibe.core.nuage.exceptions import ErrorCode, WorkflowsException
from vibe.core.nuage.streaming import StreamEvent, StreamEventsQueryParams
from vibe.core.nuage.workflow import (
    SignalWorkflowResponse,
    UpdateWorkflowResponse,
    WorkflowExecutionListResponse,
    WorkflowExecutionStatus,
)


class WorkflowsClient:
    def __init__(
        self, base_url: str, api_key: str | None = None, timeout: float = 60.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._owns_client = True

    async def __aenter__(self) -> WorkflowsClient:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=headers)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(timeout=self._timeout, headers=headers)
            self._owns_client = True
        return self._client

    def _api_url(self, endpoint: str) -> str:
        return f"{self._base_url}/v1/workflows{endpoint}"

    def _parse_sse_data(
        self, raw_data: str, event_type: str | None
    ) -> StreamEvent | None:
        parsed = json.loads(raw_data)
        if event_type == "error" or (isinstance(parsed, dict) and "error" in parsed):
            error_msg = (
                parsed.get("error", "Unknown stream error")
                if isinstance(parsed, dict)
                else str(parsed)
            )
            raise WorkflowsException(
                message=f"Stream error from server: {error_msg}",
                code=ErrorCode.GET_EVENTS_STREAM_ERROR,
            )
        return StreamEvent.model_validate(parsed)

    async def stream_events(
        self, params: StreamEventsQueryParams
    ) -> AsyncGenerator[StreamEvent, None]:
        endpoint = "/events/stream"
        query = params.model_dump(exclude_none=True)
        try:
            async with self._http_client.stream(
                "GET", self._api_url(endpoint), params=query
            ) as response:
                response.raise_for_status()
                async for event in self._iter_sse_events(response):
                    yield event
        except WorkflowsException:
            raise
        except Exception as exc:
            raise WorkflowsException.from_api_client_error(
                exc,
                message="Failed to stream events",
                code=ErrorCode.GET_EVENTS_STREAM_ERROR,
            ) from exc

    async def _iter_sse_events(
        self, response: httpx.Response
    ) -> AsyncGenerator[StreamEvent, None]:
        event_type: str | None = None
        async for line in response.aiter_lines():
            if line is None or line == "" or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
                continue
            if not line.startswith("data:"):
                continue
            raw_data = line[len("data:") :].strip()
            try:
                event = self._parse_sse_data(raw_data, event_type)
                if event:
                    yield event
            except WorkflowsException:
                raise
            except Exception:
                logger.warning(
                    "Failed to parse SSE event",
                    exc_info=True,
                    extra={"event_data": raw_data},
                )
            finally:
                event_type = None

    async def signal_workflow(
        self, execution_id: str, signal_name: str, input_data: BaseModel | None = None
    ) -> SignalWorkflowResponse:
        endpoint = f"/executions/{execution_id}/signals"
        try:
            input_data_dict = input_data.model_dump(mode="json") if input_data else {}
            request_body = {"name": signal_name, "input": input_data_dict}
            response = await self._http_client.post(
                self._api_url(endpoint),
                json=request_body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return SignalWorkflowResponse.model_validate(response.json())
        except WorkflowsException:
            raise
        except Exception as exc:
            raise WorkflowsException.from_api_client_error(
                exc,
                message="Failed to signal workflow",
                code=ErrorCode.POST_EXECUTIONS_SIGNALS_ERROR,
            ) from exc

    async def update_workflow(
        self, execution_id: str, update_name: str, input_data: BaseModel | None = None
    ) -> UpdateWorkflowResponse:
        endpoint = f"/executions/{execution_id}/updates"
        try:
            input_data_dict = input_data.model_dump(mode="json") if input_data else {}
            request_body = {"name": update_name, "input": input_data_dict}
            response = await self._http_client.post(
                self._api_url(endpoint),
                json=request_body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return UpdateWorkflowResponse.model_validate(response.json())
        except WorkflowsException:
            raise
        except Exception as exc:
            raise WorkflowsException.from_api_client_error(
                exc,
                message="Failed to update workflow",
                code=ErrorCode.POST_EXECUTIONS_UPDATES_ERROR,
            ) from exc

    async def get_workflow_runs(
        self,
        workflow_identifier: str | None = None,
        page_size: int = 50,
        next_page_token: str | None = None,
        status: Sequence[WorkflowExecutionStatus] | None = None,
        user_id: str = "current",
    ) -> WorkflowExecutionListResponse:
        params: dict[str, Any] = {"page_size": page_size, "user_id": user_id}
        if workflow_identifier:
            params["workflow_identifier"] = workflow_identifier
        if next_page_token:
            params["next_page_token"] = next_page_token
        if status:
            params["status"] = status
        endpoint = "/runs"

        try:
            response = await self._http_client.get(
                self._api_url(endpoint), params=params
            )
            response.raise_for_status()
            return WorkflowExecutionListResponse.model_validate(response.json())
        except Exception as exc:
            raise WorkflowsException.from_api_client_error(
                exc,
                message="Failed to get workflow runs",
                code=ErrorCode.GET_EXECUTIONS_ERROR,
            ) from exc
