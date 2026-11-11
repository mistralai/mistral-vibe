from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
import tomllib
from typing import Any, BinaryIO

import httpx
import respx
import tomli_w

from tests.conftest import build_test_vibe_config
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.skills.models import REGISTRY_LATEST_ALIAS
from vibe.core.skills.registry import (
    _manifest,
    _store,
    cleanup_candidates,
    evict_versions,
    still_served,
)
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry.models import RegistrySkillItem
from vibe.core.utils import file_lock


def make_item(
    *,
    skill_id: str = "id-1",
    name: str | None = "my_skill",
    skill_name: str = "Free Form",
    description: str = "does things",
    body: str = "# Body\n\ninstructions",
    assets: dict[str, Any] | None = None,
    version: int = 1,
    latest_version: int | None = None,
) -> RegistrySkillItem:
    payload: dict[str, Any] = {
        "skillId": skill_id,
        "skill": {
            "skillName": skill_name,
            "skillDescription": description,
            "skillBody": body,
            "skillAssets": assets or {},
        },
        "version": version,
    }
    metadata: dict[str, Any] = {}
    if name is not None:
        metadata["name"] = name
    if latest_version is not None:
        metadata["latestVersion"] = latest_version
    if metadata:
        payload["metadata"] = metadata
    return RegistrySkillItem.model_validate(payload)


def held_by_another_process(lock: Path) -> BinaryIO:
    """A handle on ``lock`` holding it, as a process busy with it would."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = lock.open("a+b")
    file_lock.acquire_file_lock(holder)
    return holder


def turn_registry_skills_on(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["experimental_enable_registry_skills"] = True
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def pin_deploy(
    version: int | str = REGISTRY_LATEST_ALIAS, *, project: Path | None = None
) -> None:
    """Tracks latest by default; with ``project``, in the manifest that project commits."""
    await _manifest.save(
        _manifest.global_manifest_path()
        if project is None
        else project / ".vibe" / "skills.toml",
        SkillManifest(
            skills=[ManifestEntry(name="deploy", skill_id="cid", version=version)]
        ),
    )


async def store_deploy(version: int) -> None:
    """As a sync stores it, its body naming the version."""
    body = f"Deploy with v{version}."
    item = make_item(skill_id="cid", name="deploy", version=version, body=body)
    await _store.materialize(item, "deploy")


REGISTRY_SKILLS_URL = "https://api.mistral.ai/v1/skills"
"""Where the test config's Mistral provider lists the registry's skills."""


def registry_serves_deploy(
    respx_mock: respx.MockRouter, *, latest: int = 1
) -> respx.Route:
    """At the version asked for, ``latest`` without one, as ``store_deploy`` stores it."""

    def answer(request: httpx.Request) -> httpx.Response:
        version = int(request.url.params.get("version", latest))
        return httpx.Response(
            200,
            json={
                "skillId": "cid",
                "skill": {
                    "skillName": "Free Form",
                    "skillDescription": "does things",
                    "skillBody": f"Deploy with v{version}.",
                    "skillAssets": {},
                },
                "version": version,
                "metadata": {"name": "deploy", "latestVersion": latest},
            },
        )

    return respx_mock.get(f"{REGISTRY_SKILLS_URL}/cid").mock(side_effect=answer)


def fetched_versions(route: respx.Route) -> list[int | None]:
    """None for a call that asked for the newest."""
    return [
        None
        if (version := call.request.url.params.get("version")) is None
        else int(version)
        for call in route.calls
    ]


def registry_stalls(respx_mock: respx.MockRouter) -> asyncio.Event:
    """Never answers, as a stalled network does, so a sync that reaches it never ends;
    the event is set once one did.
    """
    reached = asyncio.Event()

    async def stalled(_request: httpx.Request) -> httpx.Response:
        reached.set()
        await asyncio.Event().wait()
        raise AssertionError("a stalled registry answered")

    respx_mock.route(url__startswith=REGISTRY_SKILLS_URL).mock(side_effect=stalled)
    return reached


def catalog_lists(respx_mock: respx.MockRouter, **latest: int) -> respx.Route:
    """The one listing a cleanup asks for, each skill id at the latest version given."""
    items = [
        {
            "skillId": skill_id,
            "skill": {
                "skillName": skill_id,
                "skillDescription": "d",
                "skillBody": "# B",
            },
            "version": version,
            "metadata": {"name": skill_id, "latestVersion": version},
        }
        for skill_id, version in latest.items()
    ]
    return respx_mock.get(REGISTRY_SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"data": items, "nextPageToken": ""})
    )


async def clean_up(
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> list[tuple[str, int]]:
    """Run the store cleanup's steps in this process, in the order the background worker
    and the session run them in production.
    """
    unused = await cleanup_candidates(roots)
    if not unused:
        return []
    served = await still_served(
        build_test_vibe_config(), unused, credentials=credentials
    )
    return evict_versions(served)
