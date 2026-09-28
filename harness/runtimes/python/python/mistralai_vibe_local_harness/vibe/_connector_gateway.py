"""Mistral connector gateway transport built on generic MCP HTTP machinery."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
import time
from urllib.parse import quote

from opentelemetry import trace

from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorNormalizedResult,
    ConnectorRuntimeFailure,
    JsonObject,
)
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPAuthorizationRef,
    MCPAuthorizationRejected,
    MCPAuthorizationSnapshot,
    MCPHTTPStatusFailure,
    MCPHTTPTransportPolicy,
    MCPRuntimeFailure,
    MCPTransportDisconnected,
    ResolvedMCPServerConfig,
)
from mistralai_vibe_local_harness.vibe._mcp_transport import call_http
from mistralai_vibe_local_harness.vibe._observability import record_connector_operation

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)


class ConnectorGatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        enable_system_trust_store: bool = False,
        startup_timeout_s: float = 30.0,
        tool_timeout_s: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http_policy = MCPHTTPTransportPolicy(
            enable_system_trust_store=enable_system_trust_store
        )
        self._startup_timeout_s = startup_timeout_s
        self._tool_timeout_s = tool_timeout_s
        self._closed = False

    async def call(
        self, *, raw_connector_id: str, remote_tool_name: str, arguments: JsonObject
    ) -> ConnectorNormalizedResult:
        started_at = time.perf_counter()
        with _tracer.start_as_current_span("connector.gateway.call") as span:
            span.set_attribute("mistral_ai.vibe_harness.backend", "unified")
            try:
                result = await self._call(
                    raw_connector_id=raw_connector_id,
                    remote_tool_name=remote_tool_name,
                    arguments=arguments,
                )
            except asyncio.CancelledError:
                span.set_attribute(
                    "mistral_ai.vibe_harness.connector.outcome", "cancelled"
                )
                record_connector_operation(
                    time.perf_counter() - started_at,
                    operation="gateway_call",
                    outcome="cancelled",
                )
                logger.info(
                    "Connector gateway call cancelled",
                    extra={
                        "connector_operation": "gateway_call",
                        "connector_outcome": "cancelled",
                    },
                )
                raise
            except ConnectorRuntimeFailure as exc:
                span.set_attribute(
                    "mistral_ai.vibe_harness.connector.outcome", "failure"
                )
                span.set_attribute("error.type", exc.code)
                record_connector_operation(
                    time.perf_counter() - started_at,
                    operation="gateway_call",
                    outcome="failure",
                    error_type=exc.code,
                )
                logger.warning(
                    "Connector gateway call failed",
                    extra={
                        "connector_operation": "gateway_call",
                        "connector_outcome": "failure",
                        "error_type": exc.code,
                    },
                )
                raise
            span.set_attribute("mistral_ai.vibe_harness.connector.outcome", "success")
            record_connector_operation(
                time.perf_counter() - started_at,
                operation="gateway_call",
                outcome="success",
            )
            logger.debug(
                "Connector gateway call completed",
                extra={
                    "connector_operation": "gateway_call",
                    "connector_outcome": "success",
                },
            )
            return result

    async def _call(
        self, *, raw_connector_id: str, remote_tool_name: str, arguments: JsonObject
    ) -> ConnectorNormalizedResult:
        if self._closed:
            raise ConnectorRuntimeFailure(
                "connector_runtime_closed", "Connector gateway is closed"
            )
        encoded_id = quote(raw_connector_id, safe="")
        server = ResolvedMCPServerConfig(
            name="connector-gateway",
            transport="streamable-http",
            url=f"{self._base_url}/v1/connectors-gateway/{encoded_id}/mcp",
            authorization=MCPAuthorizationRef(
                server_name="connector-gateway",
                server_fingerprint="connector-gateway",
                kind="static",
                descriptor_revision="connector-gateway/v1",
            ),
            startup_timeout_s=self._startup_timeout_s,
            tool_timeout_s=self._tool_timeout_s,
            sampling_enabled=False,
        )
        authorization = MCPAuthorizationSnapshot(
            headers={"Authorization": f"Bearer {self._api_key}"},
            connection_revision="connector-gateway/v1",
            descriptor_revision="connector-gateway/v1",
        )
        try:
            result = await call_http(
                server,
                authorization,
                http_transport_policy=self._http_policy,
                tool_name=remote_tool_name,
                arguments=arguments,
            )
        except asyncio.CancelledError:
            raise
        except MCPAuthorizationRejected as exc:
            raise ConnectorRuntimeFailure(
                "connector_authorization_required",
                "Connector authorization is required",
            ) from exc
        except MCPHTTPStatusFailure as exc:
            if exc.status_code == HTTPStatus.FORBIDDEN:
                raise ConnectorRuntimeFailure(
                    "connector_authorization_required",
                    "Connector authorization is required",
                ) from exc
            if exc.status_code == HTTPStatus.NOT_FOUND:
                raise ConnectorRuntimeFailure(
                    "connector_not_found", "Connector gateway route was not found"
                ) from exc
            raise ConnectorRuntimeFailure(
                "connector_gateway_failed", "Connector gateway returned an HTTP error"
            ) from exc
        except MCPTransportDisconnected as exc:
            raise ConnectorRuntimeFailure(
                "connector_transport_failed",
                "Connector gateway transport disconnected",
                retryable=True,
            ) from exc
        except MCPRuntimeFailure as exc:
            if exc.code == "mcp_timeout":
                raise ConnectorRuntimeFailure(
                    "connector_timeout", "Connector gateway timed out", retryable=True
                ) from exc
            if exc.code in {"mcp_invalid_payload", "mcp_invalid_result"}:
                raise ConnectorRuntimeFailure(
                    "connector_invalid_result",
                    "Connector gateway returned an invalid result",
                ) from exc
            raise ConnectorRuntimeFailure(
                "connector_gateway_failed", "Connector gateway call failed"
            ) from exc
        return ConnectorNormalizedResult(
            content=result.content,
            structured_content=result.structured_content,
            meta=result.meta,
            is_error=result.is_error,
        )

    async def aclose(self) -> None:
        started_at = time.perf_counter()
        self._closed = True
        record_connector_operation(
            time.perf_counter() - started_at, operation="cleanup", outcome="success"
        )


__all__ = ["ConnectorGatewayClient"]
