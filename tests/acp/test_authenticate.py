from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from vibe.acp.agent import VibeAcpAgent as VibeAcpAgentLoop
from vibe.acp.exceptions import InternalError, InvalidRequestError
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.auth import (
    BrowserSignInAttempt,
    BrowserSignInError,
    BrowserSignInErrorCode,
)
from vibe.setup.auth.api_key_persistence import (
    ProviderCredentialsPersistRequest,
    ProviderCredentialsPersistResult,
)
from vibe.setup.onboarding.context import OnboardingContext


async def _noop_tenant_domain_resolver(
    provider: ProviderConfig,
    console_base_url: str,
    api_key: str,
    current_vibe_base_url: str,
) -> tuple[ProviderConfig, str]:
    """Tests skip tenant-domain discovery by default so ``_persist_credentials``
    doesn't try to reach real hosts.
    """
    return provider, current_vibe_base_url


def build_browser_sign_in_attempt(
    process_id: str = "process-123",
) -> BrowserSignInAttempt:
    return BrowserSignInAttempt(
        process_id=process_id,
        sign_in_url=f"https://console.mistral.ai/vibe/sign-in/{process_id}",
        poll_url=f"https://console.mistral.ai/api/vibe/sign-in/{process_id}",
        expires_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        code_verifier="secret-code-verifier",
    )


def build_mistral_provider(
    *,
    api_key_env_var: str = "MISTRAL_API_KEY",
    browser_auth_base_url: str = "https://console.mistral.ai",
    browser_auth_api_base_url: str = "https://console.mistral.ai/api",
) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=api_key_env_var,
        browser_auth_base_url=browser_auth_base_url,
        browser_auth_api_base_url=browser_auth_api_base_url,
        backend=Backend.MISTRAL,
    )


def build_unsupported_provider() -> ProviderConfig:
    return ProviderConfig(
        name="llamacpp",
        api_base="http://127.0.0.1:8080/v1",
        api_key_env_var="LLAMACPP_API_KEY",
        backend=Backend.GENERIC,
    )


class MutableOnboardingContextLoader:
    def __init__(self, provider: ProviderConfig) -> None:
        self.provider = provider

    def __call__(self) -> OnboardingContext:
        return OnboardingContext(provider=self.provider)


class FakeBrowserSignInService:
    def __init__(
        self,
        *,
        attempt: BrowserSignInAttempt | None = None,
        api_key: str = "api-key",
        authenticate_error: BrowserSignInError | None = None,
        start_error: BrowserSignInError | None = None,
        complete_errors: list[BrowserSignInError] | None = None,
        complete_error: BrowserSignInError | None = None,
    ) -> None:
        self.attempt = attempt or build_browser_sign_in_attempt()
        self.api_key = api_key
        self.authenticate_error = authenticate_error
        self.start_error = start_error
        self.complete_errors = list(complete_errors or [])
        if complete_error is not None:
            self.complete_errors.append(complete_error)
        self.close_count = 0

    async def authenticate(self) -> str:
        if self.authenticate_error is not None:
            raise self.authenticate_error
        return self.api_key

    async def start_attempt(self) -> BrowserSignInAttempt:
        if self.start_error is not None:
            raise self.start_error
        return self.attempt

    async def complete_attempt(self, attempt: BrowserSignInAttempt) -> str:
        if self.complete_errors:
            raise self.complete_errors.pop(0)
        if attempt != self.attempt:
            raise AssertionError("Unexpected browser sign-in attempt.")
        return self.api_key

    async def aclose(self) -> None:
        self.close_count += 1


class InMemoryApiKeyPersister:
    def __init__(self, result: str = "completed") -> None:
        self.result = result
        self.saved: list[tuple[ProviderConfig, str]] = []
        self.custom_domain_flags: list[bool] = []

    def persist(
        self, provider: ProviderConfig, api_key: str, *, custom_domain: bool = False
    ) -> str:
        self.saved.append((provider, api_key))
        self.custom_domain_flags.append(custom_domain)
        return self.result


class InMemoryCredentialsPersister:
    def __init__(
        self,
        provider_result: bool = True,
        console_base_url_result: bool = True,
        vibe_base_url_result: bool = True,
    ) -> None:
        self.provider_result = provider_result
        self.console_base_url_result = console_base_url_result
        self.vibe_base_url_result = vibe_base_url_result
        self.saved: list[ProviderCredentialsPersistRequest] = []

    async def persist(
        self, request: ProviderCredentialsPersistRequest
    ) -> ProviderCredentialsPersistResult:
        self.saved.append(request)
        return ProviderCredentialsPersistResult(
            provider=self.provider_result,
            console_base_url=(
                self.console_base_url_result
                if request.console_base_url is not None
                else None
            ),
            vibe_base_url=(
                self.vibe_base_url_result if request.vibe_base_url is not None else None
            ),
        )


def build_acp_agent(
    *,
    provider: ProviderConfig | None = None,
    browser_sign_in: FakeBrowserSignInService | None = None,
    api_key_persister: InMemoryApiKeyPersister | None = None,
) -> tuple[VibeAcpAgentLoop, MutableOnboardingContextLoader, InMemoryApiKeyPersister]:
    agent, context_loader, key_persister, _ = (
        build_acp_agent_with_credentials_persister(
            provider=provider,
            browser_sign_in=browser_sign_in,
            api_key_persister=api_key_persister,
        )
    )
    return agent, context_loader, key_persister


def build_acp_agent_with_credentials_persister(
    *,
    provider: ProviderConfig | None = None,
    browser_sign_in: FakeBrowserSignInService | None = None,
    api_key_persister: InMemoryApiKeyPersister | None = None,
    credentials_persister: InMemoryCredentialsPersister | None = None,
) -> tuple[
    VibeAcpAgentLoop,
    MutableOnboardingContextLoader,
    InMemoryApiKeyPersister,
    InMemoryCredentialsPersister,
]:
    provider = provider or build_mistral_provider()
    browser_sign_in = browser_sign_in or FakeBrowserSignInService()
    api_key_persister = api_key_persister or InMemoryApiKeyPersister()
    credentials_persister = credentials_persister or InMemoryCredentialsPersister()
    context_loader = MutableOnboardingContextLoader(provider)

    return (
        VibeAcpAgentLoop(
            onboarding_context_loader=context_loader,
            browser_sign_in_service_factory=lambda _provider: browser_sign_in,
            api_key_persister=api_key_persister.persist,
            credentials_persister=credentials_persister.persist,
            tenant_domain_resolver=_noop_tenant_domain_resolver,
        ),
        context_loader,
        api_key_persister,
        credentials_persister,
    )


def require_auth_meta(response: Any, method_id: str) -> dict[str, Any]:
    assert response is not None
    assert response.field_meta is not None
    meta = response.field_meta[method_id]
    assert isinstance(meta, dict)
    return meta


class TestACPAuthenticate:
    @pytest.mark.asyncio
    async def test_authenticate_completes_browser_sign_in_and_persists_api_key(
        self,
    ) -> None:
        provider = build_mistral_provider()
        browser_sign_in = FakeBrowserSignInService(api_key="api-key")
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            provider=provider, browser_sign_in=browser_sign_in
        )

        response = await acp_agent_loop.authenticate("browser-auth")

        assert require_auth_meta(response, "browser-auth") == {
            "persistResult": "completed",
            "status": "completed",
        }
        assert api_key_persister.saved == [(provider, "api-key")]
        assert browser_sign_in.close_count == 1

    @pytest.mark.asyncio
    async def test_authenticate_starts_delegated_browser_sign_in(self) -> None:
        attempt = build_browser_sign_in_attempt()
        browser_sign_in = FakeBrowserSignInService(attempt=attempt)
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            browser_sign_in=browser_sign_in
        )

        response = await acp_agent_loop.authenticate("browser-auth-delegated")

        assert require_auth_meta(response, "browser-auth-delegated") == {
            "attemptId": "process-123",
            "expiresAt": "2026-04-23T12:00:00Z",
            "signInUrl": "https://console.mistral.ai/vibe/sign-in/process-123",
        }
        assert api_key_persister.saved == []
        assert browser_sign_in.close_count == 1

    @pytest.mark.asyncio
    async def test_authenticate_rejects_unsupported_method(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        with pytest.raises(
            InvalidRequestError, match="Unsupported auth method: vibe-setup"
        ):
            await acp_agent_loop.authenticate("vibe-setup")

    @pytest.mark.asyncio
    async def test_authenticate_rejects_browser_sign_in_when_unavailable(self) -> None:
        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=lambda: OnboardingContext(
                provider=build_unsupported_provider()
            )
        )

        with pytest.raises(
            InvalidRequestError,
            match="Browser sign-in is not available for the configured provider.",
        ):
            await acp_agent_loop.authenticate("browser-auth")

    @pytest.mark.asyncio
    async def test_authenticate_surfaces_start_failures(self) -> None:
        browser_sign_in = FakeBrowserSignInService(
            authenticate_error=BrowserSignInError(
                "Failed to start browser sign-in.",
                code=BrowserSignInErrorCode.START_FAILED,
            )
        )
        acp_agent_loop, _, _ = build_acp_agent(browser_sign_in=browser_sign_in)

        with pytest.raises(InternalError, match="Failed to start browser sign-in."):
            await acp_agent_loop.authenticate("browser-auth")

        assert browser_sign_in.close_count == 1

    @pytest.mark.asyncio
    async def test_authenticate_completes_delegated_browser_sign_in_and_persists_api_key(
        self,
    ) -> None:
        provider = build_mistral_provider()
        attempt = build_browser_sign_in_attempt()
        browser_sign_in = FakeBrowserSignInService(attempt=attempt, api_key="api-key")
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            provider=provider, browser_sign_in=browser_sign_in
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert require_auth_meta(response, "browser-auth-delegated") == {
            "attemptId": "process-123",
            "persistResult": "completed",
            "status": "completed",
        }
        assert api_key_persister.saved == [(provider, "api-key")]
        assert browser_sign_in.close_count == 2

    @pytest.mark.asyncio
    async def test_authenticate_delegated_completion_uses_provider_captured_at_start(
        self,
    ) -> None:
        start_provider = build_mistral_provider()
        current_provider = build_mistral_provider(
            api_key_env_var="OTHER_API_KEY",
            browser_auth_base_url="https://example.com",
            browser_auth_api_base_url="https://example.com/api",
        )
        browser_sign_in = FakeBrowserSignInService(api_key="api-key")
        acp_agent_loop, context_loader, api_key_persister = build_acp_agent(
            provider=start_provider, browser_sign_in=browser_sign_in
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]
        context_loader.provider = current_provider

        await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert api_key_persister.saved == [(start_provider, "api-key")]

    @pytest.mark.asyncio
    async def test_authenticate_delegated_completion_uses_started_provider(
        self,
    ) -> None:
        provider = build_mistral_provider()
        browser_sign_in = FakeBrowserSignInService(api_key="api-key")
        acp_agent_loop, context_loader, api_key_persister = build_acp_agent(
            provider=provider, browser_sign_in=browser_sign_in
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]
        context_loader.provider = build_unsupported_provider()

        await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert api_key_persister.saved == [(provider, "api-key")]

    @pytest.mark.asyncio
    async def test_authenticate_delegated_completion_requires_attempt_id(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        with pytest.raises(
            InvalidRequestError, match="Missing browser sign-in attempt ID."
        ):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated", action="complete"
            )

    @pytest.mark.asyncio
    async def test_authenticate_delegated_completion_rejects_unknown_attempt_id(
        self,
    ) -> None:
        acp_agent_loop, _, _ = build_acp_agent()

        with pytest.raises(
            InvalidRequestError, match="Unknown browser sign-in attempt: process-123"
        ):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated", action="complete", attemptId="process-123"
            )

    @pytest.mark.asyncio
    async def test_authenticate_delegated_completion_surfaces_browser_sign_in_failures(
        self,
    ) -> None:
        browser_sign_in = FakeBrowserSignInService(
            complete_error=BrowserSignInError(
                "Browser sign-in timed out.", code=BrowserSignInErrorCode.TIMED_OUT
            )
        )
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            browser_sign_in=browser_sign_in
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        with pytest.raises(InvalidRequestError, match="Browser sign-in timed out."):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated", action="complete", attemptId=attempt_id
            )

        assert api_key_persister.saved == []
        assert browser_sign_in.close_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_code",
        [BrowserSignInErrorCode.EXCHANGE_FAILED, BrowserSignInErrorCode.POLL_FAILED],
    )
    async def test_authenticate_delegated_completion_keeps_retryable_attempts(
        self, error_code: BrowserSignInErrorCode
    ) -> None:
        provider = build_mistral_provider()
        browser_sign_in = FakeBrowserSignInService(
            api_key="api-key",
            complete_errors=[
                BrowserSignInError(
                    "Transient browser sign-in failure.", code=error_code
                )
            ],
        )
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            provider=provider, browser_sign_in=browser_sign_in
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        with pytest.raises(
            InvalidRequestError, match="Transient browser sign-in failure."
        ):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated", action="complete", attemptId=attempt_id
            )

        retry_response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert require_auth_meta(retry_response, "browser-auth-delegated") == {
            "attemptId": attempt_id,
            "persistResult": "completed",
            "status": "completed",
        }
        assert api_key_persister.saved == [(provider, "api-key")]
        assert browser_sign_in.close_count == 3


class TestACPAuthenticateCustomDomain:
    @pytest.mark.asyncio
    async def test_start_uses_custom_domain_for_browser_auth_urls(self) -> None:
        captured: list[ProviderConfig] = []
        browser_sign_in = FakeBrowserSignInService()

        def factory(provider: ProviderConfig) -> FakeBrowserSignInService:
            captured.append(provider)
            return browser_sign_in

        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=MutableOnboardingContextLoader(
                build_mistral_provider()
            ),
            browser_sign_in_service_factory=factory,
        )

        await acp_agent_loop.authenticate(
            "browser-auth-delegated",
            action="start",
            signInTarget="custom",
            domain="console.acme.internal",
        )

        assert captured[0].browser_auth_base_url == "https://console.acme.internal"
        assert (
            captured[0].browser_auth_api_base_url == "https://console.acme.internal/api"
        )

    @pytest.mark.asyncio
    async def test_start_without_sign_in_target_keeps_configured_urls(self) -> None:
        captured: list[ProviderConfig] = []
        provider = build_mistral_provider(
            browser_auth_base_url="https://console.acme.internal",
            browser_auth_api_base_url="https://console.acme.internal/api",
        )

        def factory(started: ProviderConfig) -> FakeBrowserSignInService:
            captured.append(started)
            return FakeBrowserSignInService()

        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=MutableOnboardingContextLoader(provider),
            browser_sign_in_service_factory=factory,
        )

        await acp_agent_loop.authenticate("browser-auth-delegated")

        assert captured[0].browser_auth_base_url == "https://console.acme.internal"

    @pytest.mark.asyncio
    async def test_start_with_mistral_target_resets_configured_custom_domain(
        self,
    ) -> None:
        captured: list[ProviderConfig] = []
        provider = build_mistral_provider(
            browser_auth_base_url="https://console.acme.internal",
            browser_auth_api_base_url="https://console.acme.internal/api",
        )

        def factory(started: ProviderConfig) -> FakeBrowserSignInService:
            captured.append(started)
            return FakeBrowserSignInService()

        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=MutableOnboardingContextLoader(provider),
            browser_sign_in_service_factory=factory,
        )

        await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="start", signInTarget="mistral"
        )

        assert captured[0].browser_auth_base_url == "https://console.mistral.ai"
        assert captured[0].browser_auth_api_base_url == "https://console.mistral.ai/api"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("domain", ["", "   ", "https://", "not a domain", None])
    async def test_start_rejects_invalid_custom_domain(self, domain: Any) -> None:
        acp_agent_loop, _, _ = build_acp_agent()

        with pytest.raises(InvalidRequestError, match="Invalid custom sign-in domain"):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated",
                action="start",
                signInTarget="custom",
                domain=domain,
            )

    @pytest.mark.asyncio
    async def test_start_rejects_unknown_sign_in_target(self) -> None:
        acp_agent_loop, _, _ = build_acp_agent()

        with pytest.raises(InvalidRequestError, match="Unsupported sign-in target"):
            await acp_agent_loop.authenticate(
                "browser-auth-delegated", action="start", signInTarget="elsewhere"
            )

    @pytest.mark.asyncio
    async def test_completion_persists_provider_when_domain_was_overridden(
        self,
    ) -> None:
        (acp_agent_loop, _, api_key_persister, credentials_persister) = (
            build_acp_agent_with_credentials_persister()
        )
        start_response = await acp_agent_loop.authenticate(
            "browser-auth-delegated",
            action="start",
            signInTarget="custom",
            domain="console.acme.internal",
        )
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert require_auth_meta(response, "browser-auth-delegated") == {
            "attemptId": attempt_id,
            "persistResult": "completed",
            "persistProviderResult": "completed",
            "persistConsoleBaseUrlResult": "completed",
            "status": "completed",
        }
        assert len(credentials_persister.saved) == 1
        request = credentials_persister.saved[0]
        assert request.provider.browser_auth_base_url == "https://console.acme.internal"
        assert request.console_base_url == "https://console.acme.internal"
        assert request.vibe_base_url is None
        assert api_key_persister.custom_domain_flags == [True]

    @pytest.mark.asyncio
    async def test_completion_does_not_persist_provider_without_override(self) -> None:
        (acp_agent_loop, _, api_key_persister, credentials_persister) = (
            build_acp_agent_with_credentials_persister()
        )
        start_response = await acp_agent_loop.authenticate("browser-auth-delegated")
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert "persistProviderResult" not in require_auth_meta(
            response, "browser-auth-delegated"
        )
        assert credentials_persister.saved == []
        assert api_key_persister.custom_domain_flags == [False]

    @pytest.mark.asyncio
    async def test_completion_persists_reset_to_mistral_defaults(self) -> None:
        provider = build_mistral_provider(
            browser_auth_base_url="https://console.acme.internal",
            browser_auth_api_base_url="https://console.acme.internal/api",
        )
        (acp_agent_loop, _, api_key_persister, credentials_persister) = (
            build_acp_agent_with_credentials_persister(provider=provider)
        )
        start_response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="start", signInTarget="mistral"
        )
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        assert len(credentials_persister.saved) == 1
        assert (
            credentials_persister.saved[0].provider.browser_auth_base_url
            == "https://console.mistral.ai"
        )
        assert api_key_persister.custom_domain_flags == [False]

    @pytest.mark.asyncio
    async def test_completion_reports_failed_provider_persistence_without_failing(
        self,
    ) -> None:
        (acp_agent_loop, _, _, _) = build_acp_agent_with_credentials_persister(
            credentials_persister=InMemoryCredentialsPersister(provider_result=False)
        )
        start_response = await acp_agent_loop.authenticate(
            "browser-auth-delegated",
            action="start",
            signInTarget="custom",
            domain="console.acme.internal",
        )
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        meta = require_auth_meta(response, "browser-auth-delegated")
        assert meta["persistResult"] == "completed"
        assert meta["persistProviderResult"] == "failed"
        assert meta["status"] == "completed"

    @pytest.mark.asyncio
    async def test_completion_applies_tenant_domains_from_whoami(self) -> None:
        provider = build_mistral_provider()
        credentials_persister = InMemoryCredentialsPersister()

        async def _resolver(
            _provider: ProviderConfig, _console: str, _key: str, _current_vibe: str
        ) -> tuple[ProviderConfig, str]:
            return (
                _provider.model_copy(
                    update={"api_base": "https://api.acme.internal/v1"}
                ),
                "https://chat.acme.internal",
            )

        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=MutableOnboardingContextLoader(provider),
            browser_sign_in_service_factory=lambda _: FakeBrowserSignInService(),
            api_key_persister=InMemoryApiKeyPersister().persist,
            credentials_persister=credentials_persister.persist,
            tenant_domain_resolver=_resolver,
        )
        start_response = await acp_agent_loop.authenticate(
            "browser-auth-delegated",
            action="start",
            signInTarget="custom",
            domain="console.acme.internal",
        )
        attempt_id = require_auth_meta(start_response, "browser-auth-delegated")[
            "attemptId"
        ]

        response = await acp_agent_loop.authenticate(
            "browser-auth-delegated", action="complete", attemptId=attempt_id
        )

        meta = require_auth_meta(response, "browser-auth-delegated")
        assert meta["persistProviderResult"] == "completed"
        assert meta["persistConsoleBaseUrlResult"] == "completed"
        assert meta["persistVibeBaseUrlResult"] == "completed"
        assert len(credentials_persister.saved) == 1
        request = credentials_persister.saved[0]
        assert request.provider.api_base == "https://api.acme.internal/v1"
        assert request.console_base_url == "https://console.acme.internal"
        assert request.vibe_base_url == "https://chat.acme.internal"

    @pytest.mark.asyncio
    async def test_completion_skips_tenant_resolver_for_public_console(self) -> None:
        provider = build_mistral_provider()
        credentials_persister = InMemoryCredentialsPersister()
        resolver_calls: list[str] = []

        async def _resolver(
            _provider: ProviderConfig, console: str, _key: str, current_vibe: str
        ) -> tuple[ProviderConfig, str]:
            resolver_calls.append(console)
            return _provider, current_vibe

        acp_agent_loop = VibeAcpAgentLoop(
            onboarding_context_loader=MutableOnboardingContextLoader(provider),
            browser_sign_in_service_factory=lambda _: FakeBrowserSignInService(),
            api_key_persister=InMemoryApiKeyPersister().persist,
            credentials_persister=credentials_persister.persist,
            tenant_domain_resolver=_resolver,
        )
        # No custom sign-in target → provider matches context, whole persist
        # branch is skipped.
        await acp_agent_loop.authenticate("browser-auth")

        assert resolver_calls == []
        assert credentials_persister.saved == []


class TestACPAuthStatusCustomDomain:
    @pytest.mark.asyncio
    async def test_status_reports_configured_custom_domain(self) -> None:
        acp_agent_loop, _, _ = build_acp_agent(
            provider=build_mistral_provider(
                browser_auth_base_url="https://console.acme.internal",
                browser_auth_api_base_url="https://console.acme.internal/api",
            )
        )

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response["customDomain"] == "https://console.acme.internal"

    @pytest.mark.asyncio
    async def test_status_reports_no_custom_domain_for_default_urls(self) -> None:
        acp_agent_loop, _, _ = build_acp_agent()

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response["customDomain"] is None
