from __future__ import annotations

import asyncio
import threading
import time

import httpx2
import pytest
import respx

from vibe.setup.auth import _oidc_tenant
from vibe.setup.auth._oidc_config import OidcError
from vibe.setup.auth._oidc_tenant import (
    OidcTenant,
    OidcTenantResolver,
    OidcUnauthorized,
    TenantSelection,
    discover_tenant,
)

AUDIENCE = "https://api.mistral.ai"


class _HeldDiscovery:
    """A workspace lookup that answers only once released."""

    def __init__(self, *answers: OidcTenant | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self, audience: str, id_token: str, workspace_id: str
    ) -> OidcTenant:
        del audience, id_token, workspace_id
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if isinstance(answer, Exception):
            raise answer
        return answer


async def _resolve(resolver: OidcTenantResolver, user: str = "user") -> OidcTenant:
    return await resolver.resolve(AUDIENCE, user, "id-token", TenantSelection())


@pytest.mark.asyncio
async def test_a_sign_out_during_discovery_does_not_wait_for_it() -> None:
    """*Prepare*: A workspace lookup in flight that never answers on its own.
    *Do*: Invalidate the user's tenant, as a Desktop sign-out does, from a
      thread that would block were the lock held across the lookup.
    *Assert*: Invalidation returns at once, and the lookup's answer, landing
      after it, is not cached for the signed-out session.
    """
    # Prepare
    discovery = _HeldDiscovery(OidcTenant("org-old"), OidcTenant("org-new"))
    resolver = OidcTenantResolver(discovery)
    first = asyncio.create_task(_resolve(resolver))
    await discovery.started.wait()

    # Do
    invalidated = threading.Event()
    worker = threading.Thread(
        target=lambda: (resolver.invalidate("user"), invalidated.set())
    )
    worker.start()
    worker.join(timeout=1)
    discovery.release.set()
    stale = await first

    # Assert
    assert invalidated.is_set()
    assert stale == OidcTenant("org-old")
    assert resolver.cached(AUDIENCE, "user", TenantSelection()) is None
    assert await _resolve(resolver) == OidcTenant("org-new")
    assert discovery.calls == 2


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_discovery() -> None:
    """*Prepare*: A held workspace lookup.
    *Do*: Resolve the same user's tenant three times at once.
    *Assert*: One lookup runs and answers all three.
    """
    # Prepare
    discovery = _HeldDiscovery(OidcTenant("org-1", "ws-1"))
    resolver = OidcTenantResolver(discovery)

    # Do
    callers = [asyncio.create_task(_resolve(resolver)) for _ in range(3)]
    await discovery.started.wait()
    discovery.release.set()
    tenants = await asyncio.gather(*callers)

    # Assert
    assert tenants == [OidcTenant("org-1", "ws-1")] * 3
    assert discovery.calls == 1


@pytest.mark.asyncio
async def test_a_waiter_takes_over_when_the_discovering_caller_gives_up() -> None:
    """*Prepare*: A held lookup started by one caller, and a second caller
      waiting on it.
    *Do*: Cancel the first caller, as a request timing out does.
    *Assert*: The waiter runs its own lookup instead of failing.
    """
    # Prepare
    discovery = _HeldDiscovery(OidcTenant("org-1"))
    resolver = OidcTenantResolver(discovery)
    first = asyncio.create_task(_resolve(resolver))
    await discovery.started.wait()
    second = asyncio.create_task(_resolve(resolver))
    await asyncio.sleep(0)

    # Do
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    discovery.release.set()

    # Assert
    assert await second == OidcTenant("org-1")
    assert discovery.calls == 2


@pytest.mark.asyncio
async def test_a_failed_discovery_is_not_retried_before_the_backoff() -> None:
    """*Prepare*: A workspace lookup that fails, then succeeds.
    *Do*: Resolve, resolve again at once, then again after the backoff.
    *Assert*: The second call reuses the failure without a request; the third
      retries and succeeds.
    """
    # Prepare
    now = [1_000.0]
    discovery = _HeldDiscovery(RuntimeError("platform down"), OidcTenant("org-1"))
    discovery.release.set()
    resolver = OidcTenantResolver(discovery, clock=lambda: now[0])

    # Do
    with pytest.raises(OidcError) as first:
        await _resolve(resolver)
    with pytest.raises(OidcError) as second:
        await _resolve(resolver)
    now[0] += _oidc_tenant._FAILURE_RETRY_S
    recovered = await _resolve(resolver)

    # Assert
    assert "platform down" not in str(first.value)
    assert second.value is first.value
    assert recovered == OidcTenant("org-1")
    assert discovery.calls == 2


@pytest.mark.asyncio
async def test_a_sign_out_forgets_a_failed_discovery() -> None:
    """*Prepare*: A failed workspace lookup within its backoff.
    *Do*: Invalidate the user, then resolve again.
    *Assert*: The lookup runs again at once.
    """
    # Prepare
    discovery = _HeldDiscovery(RuntimeError("platform down"), OidcTenant("org-1"))
    discovery.release.set()
    resolver = OidcTenantResolver(discovery, clock=time.monotonic)
    with pytest.raises(OidcError):
        await _resolve(resolver)

    # Do
    resolver.invalidate("user")
    tenant = await _resolve(resolver)

    # Assert
    assert tenant == OidcTenant("org-1")
    assert discovery.calls == 2


@pytest.mark.asyncio
@respx.mock
async def test_discovery_reads_a_bounded_number_of_pages() -> None:
    """*Prepare*: A workspace list that never ends, none matching the
      selected workspace.
    *Do*: Discover the tenant.
    *Assert*: It stops after the page limit with a sign-in-safe error.
    """
    # Prepare
    full_page = {
        "workspaces": [
            {"id": f"ws-{index}", "organization_id": "org"}
            for index in range(_oidc_tenant._PAGE_SIZE)
        ]
    }
    route = respx.get(f"{AUDIENCE}/v1/users/me/workspaces").respond(json=full_page)

    # Do
    with pytest.raises(OidcError):
        await discover_tenant(AUDIENCE, "id-token", "not-listed")

    # Assert
    assert route.call_count == _oidc_tenant._MAX_PAGES


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, OidcUnauthorized),
        (403, httpx2.HTTPStatusError),
        (503, httpx2.HTTPStatusError),
    ],
)
async def test_only_a_401_says_discovery_refused_the_token(
    status: int, error: type[Exception]
) -> None:
    """*Prepare*: The workspace route answers ``status``.
    *Do*: Discover the tenant.
    *Assert*: Only a 401 says the token itself was refused; 403 and 5xx keep
      their existing failure.
    """
    # Prepare
    respx.get(f"{AUDIENCE}/v1/users/me/workspaces").respond(status)

    # Do / Assert
    with pytest.raises(error) as raised:
        await discover_tenant(AUDIENCE, "id-token", "")
    assert (type(raised.value) is OidcUnauthorized) == (status == 401)


@pytest.mark.asyncio
async def test_a_refused_token_failure_does_not_answer_for_its_replacement() -> None:
    """*Prepare*: A lookup that refuses the first token, then succeeds.
    *Do*: Resolve with the refused token twice, then with a refreshed token.
    *Assert*: The refused token reuses its failure; the refreshed token is
      looked up at once, without waiting for the backoff.
    """
    # Prepare
    discovery = _HeldDiscovery(OidcUnauthorized("refused"), OidcTenant("org-1"))
    discovery.release.set()
    resolver = OidcTenantResolver(discovery, clock=time.monotonic)

    # Do
    for _ in range(2):
        with pytest.raises(OidcUnauthorized):
            await _resolve(resolver)
    tenant = await resolver.resolve(
        AUDIENCE, "user", "refreshed-id-token", TenantSelection()
    )

    # Assert
    assert tenant == OidcTenant("org-1")
    assert discovery.calls == 2
