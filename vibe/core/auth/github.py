from __future__ import annotations

import asyncio
from dataclasses import dataclass
import types
import webbrowser

import httpx
import keyring
import keyring.errors

GITHUB_CLIENT_ID = "Ov23liJ7sk5kFDMEyvDT"

_SERVICE_NAME = "vibe"
_KEYRING_USERNAME = "github_token"
_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_VALIDATE_URL = "https://api.github.com/user"
_SCOPES = "repo read:org write:org workflow read:user user:email"


class GitHubAuthError(Exception):
    pass


@dataclass
class DeviceFlowInfo:
    user_code: str
    verification_uri: str


@dataclass
class DeviceFlowHandle:
    device_code: str
    expires_in: int
    info: DeviceFlowInfo


class GitHubAuthProvider:
    def __init__(
        self,
        client_id: str = GITHUB_CLIENT_ID,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client_id = client_id
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def __aenter__(self) -> GitHubAuthProvider:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
            self._owns_client = True
        return self._client

    def get_token(self) -> str | None:
        try:
            return keyring.get_password(_SERVICE_NAME, _KEYRING_USERNAME)
        except keyring.errors.KeyringError:
            return None

    def has_token(self) -> bool:
        return bool(self.get_token())

    def delete_token(self) -> None:
        try:
            keyring.delete_password(_SERVICE_NAME, _KEYRING_USERNAME)
        except keyring.errors.KeyringError:
            pass

    async def get_valid_token(self) -> str | None:
        token = self.get_token()
        if not token:
            return None
        if await self._is_token_valid(token):
            return token
        self.delete_token()
        return None

    async def _is_token_valid(self, token: str) -> bool:
        client = self._get_client()
        try:
            response = await client.get(
                _VALIDATE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            return response.is_success
        except httpx.HTTPError:
            return False

    async def start_device_flow(self, open_browser: bool = True) -> DeviceFlowHandle:
        client = self._get_client()
        response = await client.post(
            _DEVICE_CODE_URL,
            data={"client_id": self._client_id, "scope": _SCOPES},
            headers={"Accept": "application/json"},
        )
        if not response.is_success:
            raise GitHubAuthError(f"Failed to initiate device flow: {response.text}")

        data = response.json()

        if open_browser:
            webbrowser.open(data["verification_uri"])

        return DeviceFlowHandle(
            device_code=data["device_code"],
            expires_in=data["expires_in"],
            info=DeviceFlowInfo(data["user_code"], data["verification_uri"]),
        )

    async def wait_for_token(self, handle: DeviceFlowHandle) -> str:
        client = self._get_client()
        token = await self._poll_for_token(
            client, handle.device_code, handle.expires_in, interval=1
        )
        self._save_token(token)
        return token

    def _save_token(self, token: str) -> None:
        try:
            keyring.set_password(_SERVICE_NAME, _KEYRING_USERNAME, token)
        except keyring.errors.KeyringError as e:
            raise GitHubAuthError(f"Failed to save token to keyring: {e}") from e

    async def _poll_for_token(
        self,
        client: httpx.AsyncClient,
        device_code: str,
        expires_in: int,
        interval: int,
    ) -> str:
        elapsed = 0.0
        while elapsed < expires_in:
            await asyncio.sleep(interval)
            elapsed += interval

            response = await client.post(
                _TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            result = response.json()

            if "access_token" in result:
                return result["access_token"]

            error = result.get("error")
            if error == "slow_down":
                interval = result.get("interval", interval + 5)
            elif error in {"expired_token", "access_denied"}:
                raise GitHubAuthError(f"Authentication failed: {error}")

        raise GitHubAuthError("Authentication timed out")
