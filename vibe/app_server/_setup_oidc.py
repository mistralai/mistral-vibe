from __future__ import annotations

from collections.abc import Awaitable, Callable
import time
from typing import TYPE_CHECKING, Any, Literal

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import ClientInfo, ProtocolErrorCode
from vibe.core.telemetry.send import TelemetryClient
from vibe.setup.auth._oidc_config import OidcError, require_oidc_enabled

if TYPE_CHECKING:
    from vibe.setup.auth._oidc_service import OidcAttempt, OidcService


class OidcStartResponse(ProtocolModel):
    attempt_id: str


class OidcAttemptParams(ProtocolModel):
    attempt_id: str


class OidcPollResponse(ProtocolModel):
    status: Literal["waiting", "exchanging", "completed", "failed"]
    authorization_url: str | None = None
    detail: str | None = None


class OidcProbeResponse(ProtocolModel):
    issuer: str
    subject: str


class OidcHeadersParams(ProtocolModel):
    url: str


class OidcHeadersResponse(ProtocolModel):
    headers: dict[str, str]


class SetupOidcHandler:
    """Connection-owned OIDC setup and best-effort onboarding analytics."""

    def __init__(
        self,
        telemetry_factory: Callable[[ClientInfo], Awaitable[TelemetryClient]]
        | None = None,
    ) -> None:
        self._service: OidcService | None = None
        self._telemetry_factory = telemetry_factory
        self._telemetry: TelemetryClient | None = None
        self._buffered_start: dict[str, Any] | None = None

    async def close(self) -> None:
        try:
            if self._service is not None:
                await self._service.cancel()
        finally:
            if self._telemetry is not None:
                await self._telemetry.aclose()
            self._telemetry = None
            self._buffered_start = None

    async def _configure_telemetry(self, client_info: ClientInfo | None) -> None:
        if (
            self._telemetry is not None
            or self._telemetry_factory is None
            or client_info is None
        ):
            return
        service = self._service
        if service is None:
            return
        try:
            self._telemetry = await self._telemetry_factory(client_info)
            service.on_attempt_started = self._attempt_started
            service.on_attempt_done = self._attempt_done
        except Exception:
            # Missing configuration or analytics must not prevent sign-in.
            pass

    @staticmethod
    def _properties(attempt: OidcAttempt) -> dict[str, Any]:
        return {
            "attempt_id": attempt.attempt_id,
            "auth_method": "oidc",
            "started_timestamp": attempt.started_timestamp,
            "call_source": "vibe_code",
            "is_main_call": False,
            # Sign-in is not a model call; omit call_type until it has a DS mapping.
            "metadata": {},
        }

    def _attempt_started(self, attempt: OidcAttempt) -> None:
        properties = self._properties(attempt)
        if self._telemetry is not None and self._telemetry.is_active():
            self._telemetry.send_telemetry_event(
                "vibe.onboarding_sign_in.started", properties
            )
        else:
            # First login has no credential yet; flush this attempt when it ends.
            self._buffered_start = properties

    def _attempt_done(
        self, attempt: OidcAttempt, status: Literal["success", "error", "cancelled"]
    ) -> None:
        buffered = self._buffered_start
        self._buffered_start = None
        if self._telemetry is None:
            return
        if buffered is not None and buffered["attempt_id"] == attempt.attempt_id:
            self._telemetry.send_telemetry_event(
                "vibe.onboarding_sign_in.started", buffered
            )
        properties = self._properties(attempt) | {
            "status": status,
            "duration_ms": max(0, int((time.monotonic() - attempt.started_at) * 1000)),
        }
        if status != "success":
            properties["error_type"] = (
                "cancelled" if status == "cancelled" else attempt.error_type
            )
        self._telemetry.send_telemetry_event("vibe.onboarding_sign_in.done", properties)

    async def dispatch(
        self,
        method: str,
        raw_params: dict[str, Any],
        *,
        client_info: ClientInfo | None = None,
    ) -> ProtocolModel:
        try:
            require_oidc_enabled()
            if method == "setup/oidc-headers":
                params = validate_wire(OidcHeadersParams, raw_params)
                from vibe.setup.auth._oidc_runtime import oidc_headers

                return OidcHeadersResponse(headers=await oidc_headers(params.url))
            # Keep Authlib and JOSE out of the unflagged startup/import path.
            from vibe.setup.auth._oidc_service import OidcService

            if self._service is None:
                self._service = OidcService.from_env()
            match method:
                case "setup/oidc-start":
                    validate_wire(ProtocolModel, raw_params)
                    await self._configure_telemetry(client_info)
                    attempt = await self._service.start()
                    return OidcStartResponse(attempt_id=attempt.attempt_id)
                case "setup/oidc-poll":
                    params = validate_wire(OidcAttemptParams, raw_params)
                    attempt = self._service.poll(params.attempt_id)
                    return OidcPollResponse(
                        status=attempt.status,
                        authorization_url=attempt.authorization_url,
                        detail=attempt.detail,
                    )
                case "setup/oidc-cancel":
                    params = validate_wire(OidcAttemptParams, raw_params)
                    await self._service.cancel(params.attempt_id)
                    return ProtocolModel()
                case "setup/oidc-probe":
                    validate_wire(ProtocolModel, raw_params)
                    return OidcProbeResponse(**await self._service.probe())
                case "setup/oidc-clear":
                    validate_wire(ProtocolModel, raw_params)
                    await self._service.clear()
                    return ProtocolModel()
                case _:
                    raise method_not_found(method)
        except RequestFailure:
            raise
        except OidcError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from None
        except Exception:
            # Raw OAuth, validation and keyring errors may contain secrets.
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                "OIDC experiment failed; check flag/configuration, client registration, "
                "KeyRing availability, or explicitly re-login",
            ) from None
