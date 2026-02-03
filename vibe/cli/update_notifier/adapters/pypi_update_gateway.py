from __future__ import annotations

import httpx
from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGateway,
    UpdateGatewayCause,
    UpdateGatewayError,
)

_STATUS_CAUSES: dict[int, UpdateGatewayCause] = {
    httpx.codes.NOT_FOUND: UpdateGatewayCause.NOT_FOUND,
    httpx.codes.FORBIDDEN: UpdateGatewayCause.FORBIDDEN,
    httpx.codes.TOO_MANY_REQUESTS: UpdateGatewayCause.TOO_MANY_REQUESTS,
}


class PyPIUpdateGateway(UpdateGateway):
    def __init__(
        self,
        project_name: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
        base_url: str = "https://pypi.org",
    ) -> None:
        self._project_name = project_name
        self._client = client
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    async def fetch_update(self) -> Update | None:
        response = await self._fetch()
        self._raise_gateway_error_if_any(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise UpdateGatewayError(cause=UpdateGatewayCause.INVALID_RESPONSE) from exc

        versions = data.get("versions") or []
        files = data.get("files") or []

        non_yanked_versions: set[Version] = set()
        for file in files:
            if not isinstance(file, dict) or file.get("yanked") is True:
                continue
            filename = file.get("filename")
            if not isinstance(filename, str):
                continue
            parsed_version = _parse_filename_version(filename)
            if parsed_version is not None:
                non_yanked_versions.add(parsed_version)

        valid_versions: list[Version] = []
        for raw_version in versions:
            try:
                valid_versions.append(Version(str(raw_version)))
            except InvalidVersion:
                continue

        for version in sorted(valid_versions, reverse=True):
            if version in non_yanked_versions:
                return Update(latest_version=str(version))

        return None

    async def _fetch(self) -> httpx.Response:
        headers = {"Accept": "application/vnd.pypi.simple.v1+json"}
        request_path = f"/simple/{self._project_name}/"

        try:
            if self._client is not None:
                return await self._client.get(
                    f"{self._base_url}{request_path}",
                    headers=headers,
                    timeout=self._timeout,
                )

            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            ) as client:
                return await client.get(request_path, headers=headers)
        except httpx.RequestError as exc:
            raise UpdateGatewayError(cause=UpdateGatewayCause.REQUEST_FAILED) from exc

    def _raise_gateway_error_if_any(self, response: httpx.Response) -> None:
        if response.status_code in _STATUS_CAUSES:
            raise UpdateGatewayError(cause=_STATUS_CAUSES[response.status_code])

        if response.is_error:
            raise UpdateGatewayError(cause=UpdateGatewayCause.ERROR_RESPONSE)


def _parse_filename_version(filename: str) -> Version | None:
    try:
        _, version, *_ = parse_wheel_filename(filename)
        return Version(str(version))
    except Exception:
        try:
            _, sdist_version = parse_sdist_filename(filename)
            return Version(str(sdist_version))
        except Exception:
            return None
