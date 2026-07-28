from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC
import os
from typing import Any, Protocol

from acp.schema import AuthenticateResponse, AuthMethodAgent
from keyring.errors import KeyringError

from vibe.acp.exceptions import ConfigurationError, InternalError, InvalidRequestError
from vibe.core.config import ProviderConfig, load_dotenv_values
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.setup.auth import (
    AuthState,
    BrowserSignInAttempt,
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInService,
    HttpBrowserSignInGateway,
    assess_auth_state,
)
from vibe.setup.auth.api_key_persistence import (
    persist_api_key,
    remove_api_key,
    resolve_api_key_provider,
)
from vibe.setup.onboarding.context import OnboardingContext


class BrowserSignInServicePort(Protocol):
    async def authenticate(self) -> str: ...

    async def start_attempt(self) -> BrowserSignInAttempt: ...

    async def complete_attempt(self, attempt: BrowserSignInAttempt) -> str: ...

    async def aclose(self) -> None: ...


type OnboardingContextLoader = Callable[[], OnboardingContext]
type BrowserSignInServiceFactory = Callable[[ProviderConfig], BrowserSignInServicePort]
type ApiKeyPersister = Callable[[ProviderConfig, str], str]
type ApiKeyRemover = Callable[[ProviderConfig], None]


@dataclass(frozen=True, slots=True)
class PendingBrowserSignIn:
    attempt: BrowserSignInAttempt
    provider: ProviderConfig


class AcpAuthController:
    def __init__(
        self,
        *,
        context_loader: OnboardingContextLoader | None = None,
        service_factory: BrowserSignInServiceFactory | None = None,
        api_key_persister: ApiKeyPersister = persist_api_key,
        api_key_remover: ApiKeyRemover = remove_api_key,
        environ_before_dotenv_load: Mapping[str, str] | None = None,
    ) -> None:
        self._load_context = context_loader or OnboardingContext.load
        self._service_factory = service_factory or self._build_service
        self._persist_api_key = api_key_persister
        self._remove_api_key = api_key_remover
        self._initial_environment = dict(
            environ_before_dotenv_load
            if environ_before_dotenv_load is not None
            else os.environ
        )
        self._pending: dict[str, PendingBrowserSignIn] = {}

    def browser_methods(self, *, delegated: bool) -> list[AuthMethodAgent]:
        context = self._load_context()
        if not context.supports_browser_sign_in:
            return []
        methods = [self._browser_method("browser-auth")]
        if delegated:
            methods.append(self._browser_method("browser-auth-delegated"))
        return methods

    async def authenticate(
        self, method_id: str, arguments: dict[str, Any]
    ) -> AuthenticateResponse:
        if method_id == "browser-auth":
            return await self._authenticate_browser(arguments)
        if method_id == "browser-auth-delegated":
            return await self._authenticate_delegated(arguments)
        raise InvalidRequestError(f"Unsupported auth method: {method_id}")

    def status(self) -> AuthState:
        load_dotenv_values(env_path=GLOBAL_ENV_FILE.path)
        provider = self._load_context().provider
        return assess_auth_state(
            provider,
            process_env_had_value_before_dotenv_load=bool(
                provider.api_key_env_var
                and self._initial_environment.get(provider.api_key_env_var)
            ),
        )

    def sign_out(self) -> None:
        provider = self._load_context().provider
        state = self.status()
        if not state.sign_out_available:
            raise InvalidRequestError(
                f"Sign out is not available for auth state: {state.kind.value}"
            )
        try:
            self._remove_api_key(provider)
        except (OSError, ValueError, KeyringError) as exc:
            raise InternalError(f"Failed to sign out: {exc}") from exc

    async def _authenticate_browser(
        self, arguments: dict[str, Any]
    ) -> AuthenticateResponse:
        action = arguments.get("action")
        if action not in {None, "start"}:
            raise InvalidRequestError(f"Unsupported browser auth action: {action}")
        provider = self._enabled_provider()
        service = self._service_factory(provider)
        try:
            api_key = await service.authenticate()
        except BrowserSignInError as exc:
            raise InternalError(str(exc)) from exc
        finally:
            await service.aclose()
        result = self._persist_api_key(resolve_api_key_provider(provider), api_key)
        return AuthenticateResponse(
            field_meta={
                "browser-auth": {"persistResult": result, "status": "completed"}
            }
        )

    async def _authenticate_delegated(
        self, arguments: dict[str, Any]
    ) -> AuthenticateResponse:
        action = arguments.get("action", "start")
        if action == "start":
            return await self._start_delegated()
        if action == "complete":
            return await self._complete_delegated(arguments)
        raise InvalidRequestError(
            f"Unsupported delegated browser auth action: {action}"
        )

    async def _start_delegated(self) -> AuthenticateResponse:
        provider = self._enabled_provider()
        service = self._service_factory(provider)
        try:
            attempt = await service.start_attempt()
        except BrowserSignInError as exc:
            raise InternalError(str(exc)) from exc
        finally:
            await service.aclose()
        self._pending[attempt.process_id] = PendingBrowserSignIn(attempt, provider)
        expires_at = attempt.expires_at.astimezone(UTC)
        return AuthenticateResponse(
            field_meta={
                "browser-auth-delegated": {
                    "attemptId": attempt.process_id,
                    "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
                    "signInUrl": attempt.sign_in_url,
                }
            }
        )

    async def _complete_delegated(
        self, arguments: dict[str, Any]
    ) -> AuthenticateResponse:
        attempt_id = arguments.get("attemptId") or arguments.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise InvalidRequestError("Missing browser sign-in attempt ID.")
        pending = self._pending.get(attempt_id)
        if pending is None:
            raise InvalidRequestError(f"Unknown browser sign-in attempt: {attempt_id}")
        service = self._service_factory(pending.provider)
        try:
            api_key = await service.complete_attempt(pending.attempt)
        except BrowserSignInError as exc:
            if exc.code not in {
                BrowserSignInErrorCode.EXCHANGE_FAILED,
                BrowserSignInErrorCode.POLL_FAILED,
            }:
                self._pending.pop(attempt_id, None)
            raise InvalidRequestError(str(exc)) from exc
        finally:
            await service.aclose()
        self._pending.pop(attempt_id, None)
        result = self._persist_api_key(
            resolve_api_key_provider(pending.provider), api_key
        )
        return AuthenticateResponse(
            field_meta={
                "browser-auth-delegated": {
                    "attemptId": attempt_id,
                    "persistResult": result,
                    "status": "completed",
                }
            }
        )

    def _enabled_provider(self) -> ProviderConfig:
        context = self._load_context()
        if not context.supports_browser_sign_in:
            raise InvalidRequestError(
                "Browser sign-in is not available for the configured provider."
            )
        return context.provider

    @staticmethod
    def _browser_method(method_id: str) -> AuthMethodAgent:
        return AuthMethodAgent(
            id=method_id,
            name="Sign in through Mistral AI Studio",
            description=(
                "Sign into Mistral Vibe through your Mistral AI Studio account."
            ),
        )

    @staticmethod
    def _build_service(provider: ProviderConfig) -> BrowserSignInService:
        browser_url = provider.browser_auth_base_url
        api_url = provider.browser_auth_api_base_url
        if browser_url is None or api_url is None:
            raise ConfigurationError("Browser sign-in requires both browser auth URLs")
        return BrowserSignInService(
            HttpBrowserSignInGateway(browser_base_url=browser_url, api_base_url=api_url)
        )
