from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import suppress
import socket
import time
from types import TracebackType
from unittest.mock import patch
import urllib.parse

import httpx
import keyring
from keyring.backend import KeyringBackend
import keyring.backends.fail
import keyring.errors
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
import pytest
import respx

from vibe.core.auth.mcp_oauth import (
    Fingerprint,
    KeyringTokenStorage,
    LoopbackCallbackHandler,
    MCPOAuthError,
    MCPOAuthHeadlessError,
    MCPOAuthInvalidGrant,
    MCPOAuthLoginFailed,
    MCPOAuthPortInUse,
    MCPOAuthTransientRefreshError,
    RefreshAwareOAuthClientProvider,
    build_oauth_provider,
    perform_oauth_login,
    unwrap_oauth_refresh_error,
)
from vibe.core.config import MCPOAuth, MCPStreamableHttp

_KEYRING_SERVICE = "ai.mistral.vibe"


class _MemoryKeyring(KeyringBackend):
    priority = 100  # type: ignore[assignment]

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError()
        del self.store[(service, username)]


@pytest.fixture
def memory_keyring() -> Iterator[_MemoryKeyring]:
    original = keyring.get_keyring()
    fake = _MemoryKeyring()
    keyring.set_keyring(fake)
    try:
        yield fake
    finally:
        keyring.set_keyring(original)


@pytest.fixture
def headless_keyring() -> Iterator[None]:
    original = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.fail.Keyring())
    try:
        yield
    finally:
        keyring.set_keyring(original)


def _oauth_server(
    *,
    name: str = "demo",
    url: str = "https://mcp.example.com/mcp",
    scopes: list[str] | None = None,
    client_id: str | None = None,
    client_metadata_url: str | None = None,
    redirect_port: int = 47823,
) -> MCPStreamableHttp:
    auth = MCPOAuth(
        type="oauth",
        scopes=scopes if scopes is not None else ["read", "write"],
        client_id=client_id,
        client_metadata_url=client_metadata_url,  # type: ignore[arg-type]
        redirect_port=redirect_port,
    )
    return MCPStreamableHttp(transport="streamable-http", name=name, url=url, auth=auth)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _send_callback(port: int, query: str, *, timeout: float = 5.0) -> bytes:
    deadline = asyncio.get_event_loop().time() + timeout
    last_err: BaseException | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except (ConnectionRefusedError, OSError) as exc:
            last_err = exc
            await asyncio.sleep(0.02)
            continue
        try:
            request = (
                f"GET /callback?{query} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            writer.write(request.encode("ascii"))
            await writer.drain()
            return await reader.read()
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
    raise RuntimeError(f"loopback never bound on port {port}: {last_err}")


class TestKeyringTokenStorage:
    @pytest.mark.asyncio
    async def test_round_trip_tokens(self, memory_keyring: _MemoryKeyring) -> None:
        storage = KeyringTokenStorage(alias="linear")
        assert await storage.get_tokens() is None

        tokens = OAuthToken(
            access_token="at",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="rt",
            scope="read write",
        )
        await storage.set_tokens(tokens)

        loaded = await storage.get_tokens()
        assert loaded is not None
        assert loaded.access_token == "at"
        assert loaded.refresh_token == "rt"
        assert loaded.scope == "read write"
        assert (_KEYRING_SERVICE, "mcp-oauth:linear:tokens") in memory_keyring.store

    @pytest.mark.asyncio
    async def test_round_trip_client_info(self, memory_keyring: _MemoryKeyring) -> None:
        storage = KeyringTokenStorage(alias="linear")
        assert await storage.get_client_info() is None

        info = OAuthClientInformationFull(
            client_id="abc123",
            redirect_uris=["http://127.0.0.1:47823/callback"],  # type: ignore[list-item]
            token_endpoint_auth_method="none",
        )
        await storage.set_client_info(info)

        loaded = await storage.get_client_info()
        assert loaded is not None
        assert loaded.client_id == "abc123"
        assert (
            _KEYRING_SERVICE,
            "mcp-oauth:linear:client_info",
        ) in memory_keyring.store

    @pytest.mark.asyncio
    async def test_per_alias_isolation(self, memory_keyring: _MemoryKeyring) -> None:
        a = KeyringTokenStorage(alias="linear")
        b = KeyringTokenStorage(alias="notion")
        await a.set_tokens(
            OAuthToken(access_token="A", token_type="Bearer", expires_in=60)
        )
        await b.set_tokens(
            OAuthToken(access_token="B", token_type="Bearer", expires_in=60)
        )
        loaded_a = await a.get_tokens()
        loaded_b = await b.get_tokens()
        assert loaded_a is not None and loaded_a.access_token == "A"
        assert loaded_b is not None and loaded_b.access_token == "B"

    def test_headless_init_raises(self, headless_keyring: None) -> None:
        with pytest.raises(MCPOAuthHeadlessError) as exc_info:
            KeyringTokenStorage(alias="linear")
        msg = str(exc_info.value)
        assert "linear" in msg
        assert "api_key_env" in msg
        assert exc_info.value.server_alias == "linear"

    def test_unloadable_backend_raises_headless(self) -> None:
        # PYTHON_KEYRING_BACKEND can point at a backend module that is absent from
        # Vibe's isolated venv (e.g. flyte._keyring.file in Flyte/Slurm envs), so
        # get_keyring() raises ModuleNotFoundError instead of returning a backend.
        with patch(
            "vibe.core.auth.mcp_oauth.keyring.get_keyring",
            side_effect=ModuleNotFoundError("No module named 'flyte'"),
        ):
            with pytest.raises(MCPOAuthHeadlessError) as exc_info:
                KeyringTokenStorage(alias="notion")
        assert exc_info.value.server_alias == "notion"


class TestFingerprint:
    def test_compute_stable_across_scope_order(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        a = _oauth_server(scopes=["read", "write", "admin"])
        b = _oauth_server(scopes=["admin", "write", "read"])
        assert Fingerprint.compute(a) == Fingerprint.compute(b)

    def test_compute_strips_whitespace_and_dedupes(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        a = _oauth_server(scopes=["read", "write"])
        b = _oauth_server(scopes=[" read ", "write", "write", ""])
        assert Fingerprint.compute(a) == Fingerprint.compute(b)

    def test_compute_marker_for_client_id(self, memory_keyring: _MemoryKeyring) -> None:
        srv = _oauth_server(client_id="pre-registered-id")
        assert Fingerprint.compute(srv).client_marker == "pre-registered-id"

    def test_compute_marker_for_client_metadata_url(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(client_metadata_url="https://vibe.example/cm.json")
        fp = Fingerprint.compute(srv)
        assert fp.client_marker.startswith("https://vibe.example/cm.json")

    def test_compute_marker_for_dcr(self, memory_keyring: _MemoryKeyring) -> None:
        assert Fingerprint.compute(_oauth_server()).client_marker == "<dcr>"

    def test_compute_rejects_static_auth(self, memory_keyring: _MemoryKeyring) -> None:
        from vibe.core.config import MCPStaticAuth

        srv = MCPStreamableHttp(
            transport="streamable-http",
            name="x",
            url="https://x/mcp",
            auth=MCPStaticAuth(),
        )
        with pytest.raises(TypeError, match="OAuth"):
            Fingerprint.compute(srv)

    def test_matches_detects_url_change(self, memory_keyring: _MemoryKeyring) -> None:
        a = Fingerprint.compute(_oauth_server(url="https://a/mcp"))
        b = Fingerprint.compute(_oauth_server(url="https://b/mcp"))
        assert a != b

    def test_matches_detects_scope_change(self, memory_keyring: _MemoryKeyring) -> None:
        a = Fingerprint.compute(_oauth_server(scopes=["read"]))
        b = Fingerprint.compute(_oauth_server(scopes=["read", "write"]))
        assert a != b

    def test_matches_detects_marker_change(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        a = Fingerprint.compute(_oauth_server(client_id="x"))
        b = Fingerprint.compute(_oauth_server(client_id="y"))
        assert a != b

    @pytest.mark.asyncio
    async def test_load_returns_none_when_missing(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        assert await Fingerprint.load("nope") is None

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        fp = Fingerprint.compute(_oauth_server(name="linear"))
        await fp.save("linear")
        loaded = await Fingerprint.load("linear")
        assert loaded == fp


class TestLoopbackCallbackHandler:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        port = _free_port()
        handler = LoopbackCallbackHandler(port=port, server_alias="demo")

        async def driver() -> bytes:
            return await _send_callback(port, "code=AUTH_CODE_123&state=STATE_XYZ")

        serve_task = asyncio.create_task(handler.serve_once())
        driver_task = asyncio.create_task(driver())
        code, state = await serve_task
        response = await driver_task

        assert code == "AUTH_CODE_123"
        assert state == "STATE_XYZ"
        assert b"200 OK" in response
        assert b"Login complete" in response

    @pytest.mark.asyncio
    async def test_port_in_use_raises(self) -> None:
        port = _free_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        try:
            handler = LoopbackCallbackHandler(port=port, server_alias="demo")
            with pytest.raises(MCPOAuthPortInUse) as exc_info:
                await handler.serve_once()
            assert exc_info.value.port == port
            assert exc_info.value.server_alias == "demo"
            assert "redirect_port" in str(exc_info.value)
        finally:
            sock.close()

    @pytest.mark.asyncio
    async def test_missing_code_raises(self) -> None:
        port = _free_port()
        handler = LoopbackCallbackHandler(port=port, server_alias="demo")

        async def driver() -> bytes:
            return await _send_callback(port, "error=access_denied&state=S")

        serve_task = asyncio.create_task(handler.serve_once())
        driver_task = asyncio.create_task(driver())
        with pytest.raises(MCPOAuthError, match="missing 'code'"):
            await serve_task
        response = await driver_task
        assert b"400 Bad Request" in response


class TestBuildOAuthProvider:
    @pytest.mark.asyncio
    async def test_metadata_matches_config(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(scopes=["read", "write"], redirect_port=51234)

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        md = provider.context.client_metadata
        assert md.scope == "read write"
        assert md.client_name == "Mistral Vibe"
        assert md.token_endpoint_auth_method == "none"
        assert md.grant_types == ["authorization_code", "refresh_token"]
        assert md.redirect_uris is not None
        assert str(md.redirect_uris[0]) == "http://127.0.0.1:51234/callback"

    @pytest.mark.asyncio
    async def test_empty_scopes_becomes_none(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(scopes=[])

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        assert provider.context.client_metadata.scope is None

    @pytest.mark.asyncio
    async def test_client_metadata_url_forwarded(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(client_metadata_url="https://vibe.example/cm.json")

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        assert provider.context.client_metadata_url == "https://vibe.example/cm.json"

    @pytest.mark.asyncio
    async def test_client_id_is_exposed_as_fallback_client_info(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(client_id="pre-registered-client")

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        client_info = await provider.context.storage.get_client_info()

        assert client_info is not None
        assert client_info.client_id == "pre-registered-client"

    @pytest.mark.asyncio
    async def test_rejects_static_auth(self, memory_keyring: _MemoryKeyring) -> None:
        from vibe.core.config import MCPStaticAuth

        srv = MCPStreamableHttp(
            transport="streamable-http",
            name="x",
            url="https://x/mcp",
            auth=MCPStaticAuth(),
        )

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        with pytest.raises(TypeError, match="OAuth"):
            build_oauth_provider(srv, redirect_handler=on_url, callback_handler=cb)


class TestRefreshAwareProvider:
    def _provider(
        self, memory_keyring: _MemoryKeyring
    ) -> RefreshAwareOAuthClientProvider:
        srv = _oauth_server(name="demo")

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        assert isinstance(provider, RefreshAwareOAuthClientProvider)
        provider.context.current_tokens = OAuthToken(
            access_token="ACCESS", token_type="Bearer", refresh_token="REFRESH"
        )
        return provider

    @pytest.mark.asyncio
    async def test_invalid_grant_raises_and_clears_in_memory_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        provider = self._provider(memory_keyring)
        response = httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "expired"}
        )

        with pytest.raises(MCPOAuthInvalidGrant):
            await provider._handle_refresh_response(response)

        assert provider.context.current_tokens is None

    @pytest.mark.asyncio
    async def test_invalid_grant_on_unread_stream_response_still_clears_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        provider = self._provider(memory_keyring)
        response = httpx.Response(
            400, stream=httpx.ByteStream(b'{"error": "invalid_grant"}')
        )

        with pytest.raises(MCPOAuthInvalidGrant):
            await provider._handle_refresh_response(response)

        assert provider.context.current_tokens is None

    @pytest.mark.asyncio
    async def test_server_error_is_transient_and_keeps_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        provider = self._provider(memory_keyring)
        response = httpx.Response(503, text="upstream unavailable")

        with pytest.raises(MCPOAuthTransientRefreshError):
            await provider._handle_refresh_response(response)

        assert provider.context.current_tokens is not None
        assert provider.context.current_tokens.refresh_token == "REFRESH"

    @pytest.mark.asyncio
    async def test_non_invalid_grant_oauth_error_is_transient(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        provider = self._provider(memory_keyring)
        response = httpx.Response(400, json={"error": "temporarily_unavailable"})

        with pytest.raises(MCPOAuthTransientRefreshError):
            await provider._handle_refresh_response(response)

        assert provider.context.current_tokens is not None


class TestRefreshAwareProviderThroughHttpxFlow:
    """Drive the real httpx auth flow so the token-endpoint response reaches our
    override unread, exactly as it does against a live server (e.g. Sentry).

    A direct call to ``_handle_refresh_response`` cannot catch the unread-body
    class of bug because httpx only reads the body *after* resuming the auth-flow
    generator; these tests exercise that seam via ``httpx.AsyncClient``.
    """

    def _armed_provider(
        self, memory_keyring: _MemoryKeyring
    ) -> RefreshAwareOAuthClientProvider:
        srv = _oauth_server(name="sentry", url="https://mcp.sentry.dev/mcp")

        async def on_url(_url: str) -> None:
            return None

        async def cb() -> tuple[str, str | None]:
            return "code", None

        provider = build_oauth_provider(
            srv, redirect_handler=on_url, callback_handler=cb
        )
        assert isinstance(provider, RefreshAwareOAuthClientProvider)
        provider.context.client_info = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=[AnyUrl("http://localhost:47823/callback")],
            token_endpoint_auth_method="none",
        )
        provider.context.current_tokens = OAuthToken(
            access_token="EXPIRED", token_type="Bearer", refresh_token="REFRESH"
        )
        provider.context.token_expiry_time = time.time() - 60
        provider._initialized = True
        return provider

    async def _drive_refresh(self, provider: RefreshAwareOAuthClientProvider) -> None:
        async with httpx.AsyncClient() as client:
            await client.get("https://mcp.sentry.dev/mcp", auth=provider)

    @respx.mock
    @pytest.mark.asyncio
    async def test_live_invalid_grant_clears_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        respx.post("https://mcp.sentry.dev/token").mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "revoked"}
            )
        )
        provider = self._armed_provider(memory_keyring)

        with pytest.raises(MCPOAuthInvalidGrant):
            await self._drive_refresh(provider)

        assert provider.context.current_tokens is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_live_server_5xx_is_transient_and_keeps_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        respx.post("https://mcp.sentry.dev/token").mock(
            return_value=httpx.Response(503, text="upstream unavailable")
        )
        provider = self._armed_provider(memory_keyring)

        with pytest.raises(MCPOAuthTransientRefreshError):
            await self._drive_refresh(provider)

        assert provider.context.current_tokens is not None
        assert provider.context.current_tokens.refresh_token == "REFRESH"

    @respx.mock
    @pytest.mark.asyncio
    async def test_live_malformed_body_is_transient_and_keeps_tokens(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        respx.post("https://mcp.sentry.dev/token").mock(
            return_value=httpx.Response(400, text="<html>not json</html>")
        )
        provider = self._armed_provider(memory_keyring)

        with pytest.raises(MCPOAuthTransientRefreshError):
            await self._drive_refresh(provider)

        assert provider.context.current_tokens is not None
        assert provider.context.current_tokens.refresh_token == "REFRESH"

    @respx.mock
    @pytest.mark.asyncio
    async def test_live_non_invalid_grant_oauth_error_is_transient(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        respx.post("https://mcp.sentry.dev/token").mock(
            return_value=httpx.Response(400, json={"error": "temporarily_unavailable"})
        )
        provider = self._armed_provider(memory_keyring)

        with pytest.raises(MCPOAuthTransientRefreshError):
            await self._drive_refresh(provider)

        assert provider.context.current_tokens is not None


class TestUnwrapOAuthRefreshError:
    def test_returns_bare_error(self) -> None:
        err = MCPOAuthInvalidGrant(server_alias="linear", reason="invalid_grant")
        assert unwrap_oauth_refresh_error(err) is err

    def test_finds_error_inside_exception_group(self) -> None:
        err = MCPOAuthTransientRefreshError(server_alias="linear", reason="HTTP 503")
        group = ExceptionGroup("unhandled errors in a TaskGroup", [err])
        assert unwrap_oauth_refresh_error(group) is err

    def test_finds_error_inside_nested_group(self) -> None:
        err = OAuthFlowError("needs interactive login")
        nested = ExceptionGroup("inner", [err])
        outer = ExceptionGroup("outer", [nested])
        assert unwrap_oauth_refresh_error(outer) is err

    def test_prefers_invalid_grant_over_transient(self) -> None:
        invalid = MCPOAuthInvalidGrant(server_alias="linear", reason="invalid_grant")
        transient = MCPOAuthTransientRefreshError(server_alias="linear", reason="503")
        group = ExceptionGroup("g", [transient, invalid])
        assert unwrap_oauth_refresh_error(group) is invalid

    def test_returns_none_for_unrelated_error(self) -> None:
        group = ExceptionGroup("g", [ValueError("boom")])
        assert unwrap_oauth_refresh_error(group) is None

    @pytest.mark.asyncio
    async def test_streamable_http_stack_wraps_auth_flow_error(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        from vibe.core.tools.mcp.tools import list_tools_http

        raised = MCPOAuthInvalidGrant(server_alias="linear", reason="invalid_grant")

        class _RaisingAuth(httpx.Auth):
            requires_response_body = True

            async def async_auth_flow(
                self, request: httpx.Request
            ) -> AsyncGenerator[httpx.Request, httpx.Response]:
                raise raised
                yield request  # pragma: no cover

        with pytest.raises(BaseException) as excinfo:
            await list_tools_http(
                "https://mcp.example.com/mcp",
                auth=_RaisingAuth(),
                startup_timeout_sec=5,
            )

        assert isinstance(excinfo.value, BaseExceptionGroup)
        assert unwrap_oauth_refresh_error(excinfo.value) is raised


class TestPerformOAuthLogin:
    @pytest.mark.asyncio
    async def test_oauth_flow_error_becomes_login_failed(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        srv = _oauth_server(name="demo")

        class OAuthFlowFailingClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> OAuthFlowFailingClient:
                return self

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                traceback: TracebackType | None,
            ) -> None:
                pass

            async def get(self, _url: str) -> None:
                raise OAuthFlowError("cancelled")

        async def on_url(_url: str) -> None:
            pass

        with patch(
            "vibe.core.auth.mcp_oauth.VibeAsyncHTTPClient", new=OAuthFlowFailingClient
        ):
            with pytest.raises(MCPOAuthLoginFailed, match="cancelled"):
                await perform_oauth_login(srv, on_url=on_url)

    @pytest.mark.asyncio
    async def test_full_flow_persists_tokens_and_fingerprint(
        self, memory_keyring: _MemoryKeyring
    ) -> None:
        port = _free_port()
        server_url = "https://mcp.example.com/mcp"
        as_url = "https://as.example.com"
        srv = _oauth_server(
            name="demo", url=server_url, scopes=["read"], redirect_port=port
        )

        async def on_url(url: str) -> None:
            qs = urllib.parse.urlparse(url).query
            state = urllib.parse.parse_qs(qs)["state"][0]

            async def fire() -> None:
                await _send_callback(port, f"code=THE_CODE&state={state}")

            asyncio.get_event_loop().create_task(fire())

        async with respx.mock(assert_all_called=False) as router:
            router.get(server_url).mock(side_effect=_mcp_responses())
            router.get(
                "https://mcp.example.com/.well-known/oauth-protected-resource"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"resource": server_url, "authorization_servers": [as_url]},
                )
            )
            router.get(
                "https://as.example.com/.well-known/oauth-authorization-server"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "issuer": as_url,
                        "authorization_endpoint": f"{as_url}/authorize",
                        "token_endpoint": f"{as_url}/token",
                        "registration_endpoint": f"{as_url}/register",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "grant_types_supported": [
                            "authorization_code",
                            "refresh_token",
                        ],
                    },
                )
            )
            router.post(f"{as_url}/register").mock(
                return_value=httpx.Response(
                    201,
                    json={
                        "client_id": "dcr-client-id",
                        "redirect_uris": [f"http://127.0.0.1:{port}/callback"],
                        "token_endpoint_auth_method": "none",
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                    },
                )
            )
            router.post(f"{as_url}/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "ACCESS_TOKEN",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "REFRESH_TOKEN",
                        "scope": "read",
                    },
                )
            )
            router.route(host="127.0.0.1").pass_through()

            await perform_oauth_login(srv, on_url=on_url)

        storage = KeyringTokenStorage(alias="demo")
        tokens = await storage.get_tokens()
        assert tokens is not None
        assert tokens.access_token == "ACCESS_TOKEN"
        assert tokens.refresh_token == "REFRESH_TOKEN"

        fp = await Fingerprint.load("demo")
        assert fp is not None
        assert fp == Fingerprint.compute(srv)


def _mcp_responses() -> Callable[[httpx.Request], httpx.Response]:
    state = {"calls": 0}

    def _factory(_request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        "Bearer resource_metadata="
                        '"https://mcp.example.com/.well-known/oauth-protected-resource"'
                    )
                },
            )
        return httpx.Response(200, json={"ok": True})

    return _factory
