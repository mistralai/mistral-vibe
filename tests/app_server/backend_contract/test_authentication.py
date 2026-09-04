"""Authentication parity: the same answers from both session backends.

Everything here runs twice — once with the legacy runtime and once, under
``--experimental-harness``, with the Unified one. A user must not be able to
tell which backend answered from what the account banner shows, what
``/whoami`` reports, or what a rejected key says.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_client,
    connect_backend_contract_host,
)
from tests.stubs.fake_account_gateway import FakeAccountGateway
from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.app_server._account import AccountGateway, WhoAmIResult
from vibe.app_server._identity import IdentityGateway
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import AccountActionKind, AccountPlanKind, AccountStatus
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionStartParams,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.core.identity import IdentityResult
from vibe.core.llm.exceptions import BackendError, PayloadSummary

# The sentence the legacy backend produces for a rejected credential. Derived
# from ``BackendError`` rather than typed out, so a reworded message fails here
# instead of leaving the two backends saying different things. The Harness
# holds its own copy of the string, pinned to this same source by
# ``tests/app_server/test_provider_credentials.py``.
_INVALID_API_KEY_MESSAGE = str(
    BackendError(
        provider="mistral",
        endpoint="/chat/completions",
        status=401,
        reason="Unauthorized",
        headers={},
        body_text="",
        parsed_error=None,
        model="mistral-vibe-cli-latest",
        payload_summary=PayloadSummary(
            model="mistral-vibe-cli-latest",
            message_count=1,
            approx_chars=0,
            temperature=0.0,
            has_tools=False,
            tool_choice=None,
        ),
    )
)


def _whoami(plan_name: str) -> WhoAmIResult:
    # No ``api_base``/``vibe_base``: tenant-domain reconciliation is exercised
    # by the Unified adapter's own tests, and a healed config here would move
    # the provider underneath the ladder being asserted.
    return WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name=plan_name)


def _identity() -> IdentityResult:
    return IdentityResult.model_validate({
        "id": "user-1",
        "email": "signed-in@example.com",
        "first_name": "Signed",
        "last_name": "In",
        "workspace": {"id": "ws-1", "name": "Workspace"},
        "organization": {"id": "org-1", "name": "Organization"},
    })


@asynccontextmanager
async def _connected(
    experimental_harness: bool,
    *,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
) -> AsyncIterator[BackendContractConnection]:
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
    )
    try:
        yield connection
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def unauthenticated_client(
    experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AppServerClient]:
    """A connected client for a user with no resolvable Mistral key."""
    monkeypatch.delenv("MISTRAL_API_KEY")
    client = await connect_backend_contract_client(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_session_start_without_a_key_is_unauthorized(
    unauthenticated_client,
) -> None:
    """*Prepare*: A connected client whose provider has no resolvable key.
    *Do*: Ask for a session.
    *Assert*: A typed ``UNAUTHORIZED`` naming the provider.

    The code and the provider are what ACP reads to offer a sign-in; a
    configuration error here would send the user to edit ``config.toml``.
    """
    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await unauthenticated_client.request("session/start", SessionStartParams())

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.UNAUTHORIZED
    assert exc_info.value.error.data == {"provider": "mistral"}


@pytest.mark.asyncio
async def test_session_list_without_a_key_succeeds(unauthenticated_client) -> None:
    """*Prepare*: A connected client whose provider has no resolvable key.
    *Do*: List sessions.
    *Assert*: The listing answers.

    ``vibe --resume`` has to work for a signed-out user: reading what is on
    disk needs no credential, and demanding one would hide their own history.
    """
    # Do
    response = SessionListResponse.model_validate(
        await unauthenticated_client.request("session/list", SessionListParams())
    )

    # Assert
    assert response.items == []


@pytest.mark.parametrize(
    ("build_gateway", "expected"),
    [
        pytest.param(
            lambda: FakeAccountGateway(unauthorized=True),
            AccountStatus.UNAUTHORIZED,
            id="rejected-key",
        ),
        pytest.param(
            lambda: FakeAccountGateway(unavailable=True),
            AccountStatus.UNAVAILABLE,
            id="gateway-down",
        ),
        pytest.param(
            lambda: FakeAccountGateway(_whoami("TEAM")),
            AccountStatus.READY,
            id="signed-in",
        ),
    ],
)
@pytest.mark.asyncio
async def test_account_read_walks_the_status_ladder(
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    build_gateway: Callable[[], FakeAccountGateway],
    expected: AccountStatus,
) -> None:
    """*Prepare*: A session over a fake account gateway in a given state.
    *Do*: Read the account.
    *Assert*: The status the legacy ladder produces for that state.
    """
    # Prepare
    gateway = build_gateway()
    async with _connected(experimental_harness, account_gateway=gateway) as connection:
        session = await connection.host.open_session()
        try:
            # Do
            account = await session.resources.account.read()
        finally:
            await session.close()

    # Assert
    assert account.status is expected
    assert gateway.calls, "the controller must reach the gateway, not a stub"


@pytest.mark.asyncio
async def test_account_read_reports_a_missing_key_after_the_key_disappears(
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An open session whose key is then removed.
    *Do*: Read the account.
    *Assert*: ``MISSING_KEY``, with the upgrade action still offered.

    The credential is resolved per read rather than frozen at session open, so
    revoking it downgrades the banner without reopening the session.
    """
    # Prepare
    gateway = FakeAccountGateway(_whoami("TEAM"))
    async with _connected(experimental_harness, account_gateway=gateway) as connection:
        session = await connection.host.open_session()
        try:
            monkeypatch.delenv("MISTRAL_API_KEY")

            # Do
            account = await session.resources.account.read()
        finally:
            await session.close()

    # Assert
    assert account.status is AccountStatus.MISSING_KEY
    assert account.teleport_action is not None
    assert not gateway.calls, "a missing key must not be sent to the gateway"


@pytest.mark.parametrize(
    ("plan_name", "title", "teleport_eligible"),
    [("TEAM", "[Subscription] Pro", True), ("FREE", "Free", False)],
)
@pytest.mark.asyncio
async def test_account_read_projects_the_plan_and_teleport_eligibility(
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    plan_name: str,
    title: str,
    teleport_eligible: bool,
) -> None:
    """*Prepare*: A session over an account gateway reporting a chat plan.
    *Do*: Read the account.
    *Assert*: The plan view, the upgrade offer, and teleport eligibility.

    ``teleport_eligible`` and ``teleport_action`` are the two fields
    ``VibeCodeController._require_teleport_available`` reads before it starts a
    teleport, so a paying customer on the Unified backend has to see the same
    pair the legacy one produces.
    """
    # Prepare
    gateway = FakeAccountGateway(_whoami(plan_name))
    async with _connected(experimental_harness, account_gateway=gateway) as connection:
        session = await connection.host.open_session()
        try:
            # Do
            account = await session.resources.account.read()
        finally:
            await session.close()

    # Assert
    assert account.status is AccountStatus.READY
    assert account.plan is not None
    assert account.plan.kind is AccountPlanKind.CHAT
    assert account.plan.name == plan_name
    assert account.plan.title == title
    assert account.teleport_eligible is teleport_eligible
    # An eligible plan offers no way out of a state it is not in.
    assert (account.teleport_action is None) is teleport_eligible
    if not teleport_eligible:
        assert account.plan_offer is not None
        assert account.plan_offer.kind is AccountActionKind.UPGRADE_TO_PRO


@pytest.mark.asyncio
async def test_identity_read_projects_a_fetched_identity(
    experimental_harness: bool, backend_contract_mistral_api: respx.Route
) -> None:
    """*Prepare*: A session over an identity gateway holding a signed-in user.
    *Do*: Read the identity.
    *Assert*: Every field ``/whoami`` renders is projected.
    """
    # Prepare
    gateway = FakeIdentityGateway(_identity())
    async with _connected(experimental_harness, identity_gateway=gateway) as connection:
        session = await connection.host.open_session()
        try:
            # Do
            identity = await session.resources.identity.read()
        finally:
            await session.close()

    # Assert
    assert identity is not None
    assert identity.id == "user-1"
    assert identity.email == "signed-in@example.com"
    assert identity.first_name == "Signed"
    assert identity.last_name == "In"
    assert identity.workspace is not None
    assert identity.workspace.name == "Workspace"
    assert identity.organization is not None
    assert identity.organization.name == "Organization"


@pytest.mark.asyncio
async def test_identity_read_is_silent_when_the_key_is_rejected(
    experimental_harness: bool, backend_contract_mistral_api: respx.Route
) -> None:
    """*Prepare*: A session over an identity gateway that rejects the key.
    *Do*: Read the identity.
    *Assert*: Nothing — ``/whoami`` reports no identity rather than failing.
    """
    # Prepare
    gateway = FakeIdentityGateway(unauthorized=True)
    async with _connected(experimental_harness, identity_gateway=gateway) as connection:
        session = await connection.host.open_session()
        try:
            # Do
            identity = await session.resources.identity.read()
        finally:
            await session.close()

    # Assert
    assert identity is None


@pytest.mark.asyncio
async def test_a_provider_401_mid_turn_yields_the_legacy_message(
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: A provider that answers the completion with ``401``.
    *Do*: Run a turn.
    *Assert*: The turn fails carrying the sentence the legacy backend produces.

    The Unified path builds this message in the Harness, which cannot import
    Vibe; this is the assertion that keeps the two copies the same sentence.

    Containment rather than equality: the legacy loop wraps a retried backend
    failure in ``API error from <provider> (model: <model>): ...``
    (``_loop.py:2969``) before it reaches the turn. Pinning the whole string
    would pin that wrapper too, and reproducing it on the Unified side means
    the Harness formatting a Vibe model name it has no business knowing.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )

    # Do
    with pytest.raises(AppServerTurnError) as exc_info:
        _ = [event async for event in backend_contract_session.act("hello")]

    # Assert
    assert _INVALID_API_KEY_MESSAGE in exc_info.value.error.message
