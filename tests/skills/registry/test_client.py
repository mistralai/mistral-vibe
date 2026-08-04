from __future__ import annotations

import httpx
import pytest
import respx

from vibe.core.skills.registry._client import RegistrySkillsClient, RegistrySkillsError

_URL = "https://api.mistral.ai/v1/skills"


def _page(skill_id: str, *, next_token: str = "") -> dict[str, object]:
    return {
        "data": [
            {"skillId": skill_id, "skill": {"skillName": skill_id, "skillBody": "b"}}
        ],
        "nextPageToken": next_token,
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_catalog_paginates() -> None:
    route = respx.get(_URL)
    route.side_effect = [
        httpx.Response(200, json=_page("a", next_token="p2")),
        httpx.Response(200, json=_page("b")),
    ]

    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        items = await client.list_catalog(page_size=50)

    assert [item.skill_id for item in items] == ["a", "b"]
    assert route.calls[0].request.url.params["pageSize"] == "50"
    assert "pageToken" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["pageToken"] == "p2"
    assert route.calls[0].request.headers["Authorization"] == "Bearer key"


@pytest.mark.asyncio
@respx.mock
async def test_list_catalog_single_page() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_page("only")))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        items = await client.list_catalog(page_size=100)
    assert [item.skill_id for item in items] == ["only"]


@pytest.mark.asyncio
@respx.mock
async def test_unauthorized_raises() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(401, json={"message": "no"}))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="unauthorized"):
            await client.list_catalog(page_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_server_error_raises() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(503))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="unexpected status"):
            await client.list_catalog(page_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_non_json_raises() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, text="not json"))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="valid JSON"):
            await client.list_catalog(page_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_network_error_raises() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="request failed"):
            await client.list_catalog(page_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_list_catalog_sends_fields_mask() -> None:
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=_page("a")))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        await client.list_catalog(page_size=10)
    params = route.calls[0].request.url.params
    assert params["pageSize"] == "10"
    assert "skillBody" not in params["fields"]
    assert "skillName" in params["fields"]


@pytest.mark.asyncio
@respx.mock
async def test_list_versions_parses_aliases_and_sorts_desc() -> None:
    respx.get(f"{_URL}/sid/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"version": 1, "versionAttributes": {"aliases": ["old"]}},
                    {
                        "version": 3,
                        "versionAttributes": {"aliases": ["stable", "main"]},
                    },
                    {"version": 2},
                ]
            },
        )
    )
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        versions = await client.list_versions("sid")

    assert [v.version for v in versions] == [3, 2, 1]
    assert versions[0].aliases == ["stable", "main"]
    assert versions[1].aliases == []
    assert versions[2].aliases == ["old"]


@pytest.mark.asyncio
@respx.mock
async def test_list_versions_invalid_payload_raises() -> None:
    respx.get(f"{_URL}/sid/versions").mock(
        return_value=httpx.Response(200, json={"items": [{"noVersion": 1}]})
    )
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="invalid versions response"):
            await client.list_versions("sid")


@pytest.mark.asyncio
@respx.mock
async def test_get_skill_by_id() -> None:
    respx.get(f"{_URL}/sid").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "sid",
                "skill": {"skillName": "n", "skillBody": "b"},
                "version": 3,
            },
        )
    )
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        item = await client.get_skill("sid")
    assert item.skill_id == "sid"
    assert item.version == 3


@pytest.mark.asyncio
@respx.mock
async def test_get_skill_not_found_raises() -> None:
    respx.get(f"{_URL}/missing").mock(return_value=httpx.Response(404))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="not found"):
            await client.get_skill("missing")


@pytest.mark.asyncio
@respx.mock
async def test_get_skill_invalid_payload_raises() -> None:
    # A 200 whose body isn't a valid skill object must surface as a registry
    # error, not a bare pydantic ValidationError.
    respx.get(f"{_URL}/sid").mock(
        return_value=httpx.Response(200, json="not-an-object")
    )
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="invalid skill response"):
            await client.get_skill("sid")


@pytest.mark.asyncio
@respx.mock
async def test_list_catalog_invalid_payload_raises() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, json="not-an-object"))
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="invalid catalog response"):
            await client.list_catalog(page_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_list_catalog_raises_when_page_cap_exceeded() -> None:
    # Every page reports more pages -> the cap is hit with data remaining, which
    # must raise rather than silently return a truncated catalog.
    respx.get(_URL).mock(
        return_value=httpx.Response(200, json=_page("a", next_token="more"))
    )
    async with RegistrySkillsClient("https://api.mistral.ai/v1", "key") as client:
        with pytest.raises(RegistrySkillsError, match="maximum number of pages"):
            await client.list_catalog(page_size=10)
