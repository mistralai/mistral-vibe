from __future__ import annotations

import httpx

from vibe.cli.update_notifier.version_update_gateway import (
    VersionUpdate,
    VersionUpdateGateway,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)


class GitHubVersionUpdateGateway(VersionUpdateGateway):
    def __init__(
        self,
        owner: str,
        repository: str,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._owner = owner
        self._repository = repository
        self._token = token
        self._client = client
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    async def fetch_update(self) -> VersionUpdate | None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "mistral-vibe-update-notifier",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        request_path = f"/repos/{self._owner}/{self._repository}/releases"

        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._base_url}{request_path}",
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(
                    base_url=self._base_url, timeout=self._timeout
                ) as client:
                    response = await client.get(request_path, headers=headers)
        except httpx.RequestError as exc:
            raise VersionUpdateGatewayError(
                cause=VersionUpdateGatewayCause.REQUEST_FAILED
            ) from exc

        rate_limit_remaining = response.headers.get("X-RateLimit-Remaining")
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS or (
            rate_limit_remaining is not None and rate_limit_remaining == "0"
        ):
            raise VersionUpdateGatewayError(
                cause=VersionUpdateGatewayCause.TOO_MANY_REQUESTS
            )

        if response.status_code == httpx.codes.FORBIDDEN:
            raise VersionUpdateGatewayError(cause=VersionUpdateGatewayCause.FORBIDDEN)

        if response.status_code == httpx.codes.NOT_FOUND:
            raise VersionUpdateGatewayError(
                cause=VersionUpdateGatewayCause.NOT_FOUND,
                message="Unable to fetch the GitHub releases. Did you export a GITHUB_TOKEN environment variable?",
            )

        if response.is_error:
            raise VersionUpdateGatewayError(
                cause=VersionUpdateGatewayCause.ERROR_RESPONSE
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise VersionUpdateGatewayError(
                cause=VersionUpdateGatewayCause.INVALID_RESPONSE
            ) from exc

        if not data:
            return None

        # pick the most recently published non-prerelease and non-draft release
        # github "list releases" API most likely returns ordered results, but this is not guaranteed
        for release in sorted(
            data, key=lambda x: x.get("published_at") or "", reverse=True
        ):
            if release.get("prerelease") or release.get("draft"):
                continue
            if version := _extract_version(release.get("tag_name")):
                return VersionUpdate(latest_version=version)

        return None


def _extract_version(tag_name: str | None) -> str | None:
    if not tag_name:
        return None
    tag = tag_name.strip()
    if not tag:
        return None
    return tag[1:] if tag.startswith(("v", "V")) else tag
