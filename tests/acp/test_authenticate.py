from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InternalError, InvalidRequestError
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.auth import (
    BrowserSignInAttempt,
    BrowserSignInError,
    BrowserSignInErrorCode,
)
from vibe.setup.onboarding.context import OnboardingContext


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
    def __init__(
        self, provider: ProviderConfig, *, enable_browser_sign_in: bool = True
    ) -> None:
        self.provider = provider
        self.enable_browser_sign_in = enable_browser_sign_in

    def __call__(self) -> OnboardingContext:
        return OnboardingContext(
            provider=self.provider,
            enable_experimental_browser_sign_in=self.enable_browser_sign_in,
        )


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

    def persist(self, provider: ProviderConfig, api_key: str) -> str:
        self.saved.append((provider, api_key))
        return self.result


def build_acp_agent(
    *,
    provider: ProviderConfig | None = None,
    enable_browser_sign_in: bool = True,
    browser_sign_in: FakeBrowserSignInService | None = None,
    api_key_persister: InMemoryApiKeyPersister | None = None,
) -> tuple[VibeAcpAgentLoop, MutableOnboardingContextLoader, InMemoryApiKeyPersister]:
    provider = provider or build_mistral_provider()
    browser_sign_in = browser_sign_in or FakeBrowserSignInService()
    api_key_persister = api_key_persister or InMemoryApiKeyPersister()
    context_loader = MutableOnboardingContextLoader(
        provider, enable_browser_sign_in=enable_browser_sign_in
    )

    return (
        VibeAcpAgentLoop(
            onboarding_context_loader=context_loader,
            browser_sign_in_service_factory=lambda _provider: browser_sign_in,
            api_key_persister=api_key_persister.persist,
        ),
        context_loader,
        api_key_persister,
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
    async def test_authenticate_rejects_browser_sign_in_when_flag_disabled(
        self,
    ) -> None:
        browser_sign_in = FakeBrowserSignInService(api_key="api-key")
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            browser_sign_in=browser_sign_in, enable_browser_sign_in=False
        )

        with pytest.raises(
            InvalidRequestError,
            match="Browser sign-in is not available for the configured provider.",
        ):
            await acp_agent_loop.authenticate("browser-auth")

        assert api_key_persister.saved == []
        assert browser_sign_in.close_count == 0

    @pytest.mark.asyncio
    async def test_authenticate_rejects_delegated_browser_sign_in_when_flag_disabled(
        self,
    ) -> None:
        browser_sign_in = FakeBrowserSignInService()
        acp_agent_loop, _, api_key_persister = build_acp_agent(
            browser_sign_in=browser_sign_in, enable_browser_sign_in=False
        )

        with pytest.raises(
            InvalidRequestError,
            match="Browser sign-in is not available for the configured provider.",
        ):
            await acp_agent_loop.authenticate("browser-auth-delegated")

        assert api_key_persister.saved == []
        assert browser_sign_in.close_count == 0

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
    async def test_authenticate_delegated_completion_allows_flag_disabled_after_start(
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
        context_loader.enable_browser_sign_in = False

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
