from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC
import os
from typing import Any, Protocol

from acp.schema import AuthenticateResponse, AuthMethodAgent
from keyring.errors import KeyringError

from vibe.acp.exceptions import ConfigurationError, InternalError, InvalidRequestError
from vibe.core.config import ProviderConfig, load_dotenv_values
from vibe.core.config._defaults import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
)
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
    ProviderCredentialsPersistRequest,
    ProviderCredentialsPersistResult,
    persist_api_key,
    persist_provider_credentials,
    remove_api_key,
    resolve_api_key_provider,
)
from vibe.setup.auth.whoami import resolve_tenant_domains
from vibe.setup.onboarding.context import (
    OnboardingContext,
    is_valid_custom_domain,
    resolve_browser_auth_urls,
)

SIGN_IN_TARGET_MISTRAL = "mistral"
SIGN_IN_TARGET_CUSTOM = "custom"


class BrowserSignInServicePort(Protocol):
    async def authenticate(self) -> str: ...

    async def start_attempt(self) -> BrowserSignInAttempt: ...

    async def complete_attempt(self, attempt: BrowserSignInAttempt) -> str: ...

    async def aclose(self) -> None: ...


class ApiKeyPersister(Protocol):
    def __call__(
        self, provider: ProviderConfig, api_key: str, *, custom_domain: bool = False
    ) -> str: ...


type OnboardingContextLoader = Callable[[], OnboardingContext]
type BrowserSignInServiceFactory = Callable[[ProviderConfig], BrowserSignInServicePort]
type ApiKeyRemover = Callable[[ProviderConfig], None]
type CredentialsPersister = Callable[
    [ProviderCredentialsPersistRequest], Awaitable[ProviderCredentialsPersistResult]
]
type TenantDomainResolver = Callable[
    [ProviderConfig, str, str, str], Awaitable[tuple[ProviderConfig, str]]
]


@dataclass(frozen=True, slots=True)
class PendingBrowserSignIn:
    attempt: BrowserSignInAttempt
    provider: ProviderConfig


def _custom_domain_of(provider: ProviderConfig) -> str | None:
    base_url = provider.browser_auth_base_url
    if not base_url or base_url == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL:
        return None
    return base_url


async def _default_credentials_persister(
    request: ProviderCredentialsPersistRequest,
) -> ProviderCredentialsPersistResult:
    return await persist_provider_credentials(request)


class AcpAuthController:
    def __init__(
        self,
        *,
        context_loader: OnboardingContextLoader | None = None,
        service_factory: BrowserSignInServiceFactory | None = None,
        api_key_persister: ApiKeyPersister = persist_api_key,
        api_key_remover: ApiKeyRemover = remove_api_key,
        credentials_persister: CredentialsPersister = _default_credentials_persister,
        tenant_domain_resolver: TenantDomainResolver = resolve_tenant_domains,
        environ_before_dotenv_load: Mapping[str, str] | None = None,
    ) -> None:
        self._load_context = context_loader or OnboardingContext.load
        self._service_factory = service_factory or self._build_service
        self._persist_api_key = api_key_persister
        self._remove_api_key = api_key_remover
        self._persist_credentials_impl = credentials_persister
        self._resolve_tenant_domains = tenant_domain_resolver
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

    def custom_domain(self) -> str | None:
        return _custom_domain_of(self._load_context().provider)

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
        provider = self._resolve_sign_in_provider(arguments)
        service = self._service_factory(provider)
        try:
            api_key = await service.authenticate()
        except BrowserSignInError as exc:
            raise InternalError(str(exc)) from exc
        finally:
            await service.aclose()
        meta = await self._persist_credentials(provider, api_key)
        return AuthenticateResponse(
            field_meta={"browser-auth": {**meta, "status": "completed"}}
        )

    async def _authenticate_delegated(
        self, arguments: dict[str, Any]
    ) -> AuthenticateResponse:
        action = arguments.get("action", "start")
        if action == "start":
            return await self._start_delegated(arguments)
        if action == "complete":
            return await self._complete_delegated(arguments)
        raise InvalidRequestError(
            f"Unsupported delegated browser auth action: {action}"
        )

    async def _start_delegated(self, arguments: dict[str, Any]) -> AuthenticateResponse:
        provider = self._resolve_sign_in_provider(arguments)
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
        meta = await self._persist_credentials(pending.provider, api_key)
        return AuthenticateResponse(
            field_meta={
                "browser-auth-delegated": {
                    "attemptId": attempt_id,
                    **meta,
                    "status": "completed",
                }
            }
        )

    async def _persist_credentials(
        self, provider: ProviderConfig, api_key: str
    ) -> dict[str, str]:
        custom_domain = _custom_domain_of(provider)
        meta: dict[str, str] = {
            "persistResult": self._persist_api_key(
                resolve_api_key_provider(provider),
                api_key,
                custom_domain=custom_domain is not None,
            )
        }
        context = self._load_context()
        if provider == context.provider:
            return meta

        desired_console = custom_domain or DEFAULT_CONSOLE_BASE_URL
        desired_vibe_base_url = context.vibe_base_url
        # Only fetch tenant domains for on-prem consoles — the public Mistral
        # console has no per-tenant redirection to discover.
        if custom_domain is not None:
            provider, desired_vibe_base_url = await self._resolve_tenant_domains(
                provider, desired_console, api_key, context.vibe_base_url
            )

        request = ProviderCredentialsPersistRequest(
            provider=provider,
            console_base_url=(
                desired_console if desired_console != context.console_base_url else None
            ),
            vibe_base_url=(
                desired_vibe_base_url
                if desired_vibe_base_url != context.vibe_base_url
                else None
            ),
        )
        result = await self._persist_credentials_impl(request)
        meta["persistProviderResult"] = "completed" if result.provider else "failed"
        if result.console_base_url is not None:
            meta["persistConsoleBaseUrlResult"] = (
                "completed" if result.console_base_url else "failed"
            )
        if result.vibe_base_url is not None:
            meta["persistVibeBaseUrlResult"] = (
                "completed" if result.vibe_base_url else "failed"
            )
        return meta

    def _resolve_sign_in_provider(self, arguments: dict[str, Any]) -> ProviderConfig:
        provider = self._enabled_provider()
        target = arguments.get("signInTarget")
        if target is None:
            return provider
        if target == SIGN_IN_TARGET_MISTRAL:
            return provider.model_copy(
                update={
                    "browser_auth_base_url": DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
                    "browser_auth_api_base_url": (
                        DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
                    ),
                }
            )
        if target != SIGN_IN_TARGET_CUSTOM:
            raise InvalidRequestError(f"Unsupported sign-in target: {target}")
        domain = arguments.get("domain")
        if not isinstance(domain, str) or not is_valid_custom_domain(domain):
            raise InvalidRequestError(f"Invalid custom sign-in domain: {domain!r}")
        base_url, api_base_url = resolve_browser_auth_urls(domain)
        return provider.model_copy(
            update={
                "browser_auth_base_url": base_url,
                "browser_auth_api_base_url": api_base_url,
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
