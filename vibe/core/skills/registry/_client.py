from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from vibe.core.skills.registry.models import (
    ListSkillsResponse,
    ListVersionsResponse,
    RegistrySkillItem,
    SkillVersionInfo,
)
from vibe.core.utils.http import build_ssl_context

_MAX_PAGES = 50


class RegistrySkillsError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse[M: BaseModel](model: type[M], payload: Any, what: str) -> M:
    """Validate a registry payload, normalizing failures to RegistrySkillsError.

    Keeps every response-parsing path consistent: a malformed 200 surfaces as a
    registry error, not a bare ValidationError that callers wouldn't catch.
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise RegistrySkillsError(f"invalid {what} response") from exc


class RegistrySkillsClient:
    _CATALOG_FIELDS = (
        "skillId,skill.skillName,skill.skillDescription,attributes,metadata,version"
    )

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._skills_url = f"{base_url.rstrip('/')}/skills"
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RegistrySkillsClient:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            verify=build_ssl_context(),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_catalog(self, *, page_size: int) -> list[RegistrySkillItem]:
        return await self._list(page_size=page_size, fields=self._CATALOG_FIELDS)

    async def list_versions(self, skill_id: str) -> list[SkillVersionInfo]:
        payload = await self._get_json(f"{self._skills_url}/{skill_id}/versions", {})
        parsed = _parse(ListVersionsResponse, payload, "versions")
        infos = [row.to_info() for row in parsed.items]
        infos.sort(key=lambda v: v.version, reverse=True)
        return infos

    async def get_skill(
        self, skill_id: str, *, version: int | None = None, alias: str | None = None
    ) -> RegistrySkillItem:
        # version_selector is a oneof: version XOR alias (omit both = latest).
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        elif alias is not None:
            params["alias"] = alias
        url = f"{self._skills_url}/{skill_id}"
        payload = await self._get_json(url, params)
        return _parse(RegistrySkillItem, payload, "skill")

    async def _list(
        self, *, page_size: int, fields: str | None
    ) -> list[RegistrySkillItem]:
        items: list[RegistrySkillItem] = []
        page_token = ""
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"pageSize": page_size}
            if fields:
                params["fields"] = fields
            if page_token:
                params["pageToken"] = page_token
            page = _parse(
                ListSkillsResponse,
                await self._get_json(self._skills_url, params),
                "catalog",
            )
            items.extend(page.data)
            if not page.next_page_token:
                return items
            page_token = page.next_page_token
        # Cap reached with pages still remaining: fail loudly rather than return
        # a silently-truncated catalog that a caller would treat as complete.
        raise RegistrySkillsError("catalog exceeds the maximum number of pages")

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        if self._client is None:
            raise RegistrySkillsError("client used outside of an async context")
        try:
            response = await self._client.get(url, params=params)
        except httpx.RequestError as exc:
            raise RegistrySkillsError(f"request failed: {exc}") from exc

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise RegistrySkillsError(f"unauthorized ({response.status_code})")
        if response.status_code == httpx.codes.NOT_FOUND:
            raise RegistrySkillsError("not found (404)")
        if not response.is_success:
            raise RegistrySkillsError(f"unexpected status {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise RegistrySkillsError("response was not valid JSON") from exc
