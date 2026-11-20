"""Skill catalogue expectations both backends have to meet.

The CLI resolves ``/skill-name`` out of the runtime snapshot and the settings
screen counts custom skills from it, so a backend that reports no skills is one
where those features silently do nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
import shutil
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from tests.skills.registry.conftest import REGISTRY_SKILLS_URL, registry_stalls
from vibe.app_server import _unified_harness_backend_adapter as _adapter_module
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import ServerWarning
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeMutationStatus,
    SessionDeleteParams,
    SessionForkParams,
    SessionForkResponse,
    SessionOptions,
    SessionResumeParams,
    SkillsCatalogParams,
    SkillsCatalogResponse,
    SkillsDeleteLocalParams,
    SkillsImportParams,
    SkillsInstalledParams,
    SkillsInstalledResponse,
    SkillsListParams,
    SkillsListResponse,
    SkillsSessionImportResponse,
    SkillsSetEnabledParams,
)
from vibe.app_server.session import AppServerSession
from vibe.core.skills.registry import _leases, _store
from vibe.core.types import SessionMetadata

_PROMPT = "Read the diff twice before commenting."


def _write_skill(cwd: Path, name: str, description: str, prompt: str) -> None:
    skill = cwd / ".vibe" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{prompt}\n",
        encoding="utf-8",
    )


@pytest.fixture
def backend_contract_session_options(tmp_path: Path) -> SessionOptions:
    _write_skill(tmp_path, "code-review", "Review a diff for defects.", _PROMPT)
    return SessionOptions(cwd=str(tmp_path), trust_workspace=True)


@pytest.fixture
def skill_filters(config_dir: Path, request: pytest.FixtureRequest) -> None:
    """Write ``request.param`` into the user config before the host connects."""
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(request.param)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _set_enabled(
    connection: BackendContractConnection, session: AppServerSession, name: str
) -> AppServerResponseError:
    with pytest.raises(AppServerResponseError) as exc_info:
        await connection.client.request(
            "skills/setEnabled",
            SkillsSetEnabledParams(
                session_id=session.state.session.id, name=name, enabled=False
            ),
        )
    return exc_info.value


async def _catalog(
    connection: BackendContractConnection, session: AppServerSession
) -> SkillsCatalogResponse:
    return validate_wire(
        SkillsCatalogResponse,
        await connection.client.request(
            "skills/catalog", SkillsCatalogParams(session_id=session.state.session.id)
        ),
    )


@pytest.mark.asyncio
async def test_a_workspace_skill_reaches_the_runtime_snapshot(
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a workspace ``SKILL.md``.

    Do open a session.

    Assert the client can resolve it by name. This is the lookup ``/skill-name``
    goes through, so an empty catalogue is a dead slash command.
    """
    skill = backend_contract_session.resources.runtime.get_skill("code-review")

    assert skill is not None
    assert skill.description == "Review a diff for defects."
    assert _PROMPT in skill.prompt
    assert skill.source == "local"
    assert skill.user_invocable is True


@pytest.mark.asyncio
async def test_builtin_skills_are_reported_as_builtin(
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session.

    Do look up a skill that ships with Vibe.

    Assert it is not counted as custom. The Unified backend writes builtins to
    disk so Core has a real path, and a file on disk must not make them look
    like something the user installed.
    """
    builtin = next(
        (
            skill
            for skill in backend_contract_session.resources.runtime.skills
            if skill.name in {"vibe", "vibe:vibe"}
        ),
        None,
    )

    assert builtin is not None
    assert builtin.source == "builtin" or builtin.scope == "builtin"
    assert backend_contract_session.resources.runtime.custom_skills_count == 1


@pytest.mark.asyncio
async def test_skills_list_answers_with_the_same_catalogue(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session.

    Do call ``skills/list``.

    Assert it agrees with the snapshot the session was opened with. Two sources
    of truth for one catalogue is how a client ends up offering a skill the
    backend will not load.
    """
    response = validate_wire(
        SkillsListResponse,
        await backend_contract_connection.client.request(
            "skills/list",
            SkillsListParams(session_id=backend_contract_session.state.session.id),
        ),
    )

    listed = {skill.name for skill in response.skills}
    assert "code-review" in listed
    assert listed == {
        skill.name for skill in backend_contract_session.resources.runtime.skills
    }


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_reload_picks_up_a_skill_written_after_the_session_opened(
    backend_contract_session: AppServerSession, tmp_path: Path
) -> None:
    """Prepare a session, then write a second ``SKILL.md`` into the workspace.

    Do reload the configuration.

    Assert the new skill is in the catalogue. Authoring a skill and reloading is
    the whole edit loop; a backend that only scans at bind time makes the user
    restart to see their own file.
    """
    _write_skill(tmp_path, "release-notes", "Draft release notes.", "Group by theme.")

    await backend_contract_session.resources.config.reload()

    skill = backend_contract_session.resources.runtime.get_skill("release-notes")
    assert skill is not None
    assert skill.description == "Draft release notes."


@pytest.mark.asyncio
async def test_a_disabled_skill_stays_listed_so_it_can_be_turned_back_on(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session with a workspace skill.

    Do turn it off with ``skills/setEnabled``, then read ``skills/installed``.

    Assert the row is still there and reports itself off. A backend that filters
    disabled skills out of the browser leaves no row to switch back on, so the
    skill becomes unreachable the moment it is turned off.
    """
    session_id = backend_contract_session.state.session.id

    await backend_contract_connection.client.request(
        "skills/setEnabled",
        SkillsSetEnabledParams(
            session_id=session_id, name="code-review", enabled=False
        ),
    )
    installed = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed", SkillsInstalledParams(session_id=session_id)
        ),
    )

    rows = {skill.name: skill for skill in installed.skills}
    assert "code-review" in rows
    assert rows["code-review"].enabled is False

    await backend_contract_connection.client.request(
        "skills/setEnabled",
        SkillsSetEnabledParams(session_id=session_id, name="code-review", enabled=True),
    )
    restored = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed", SkillsInstalledParams(session_id=session_id)
        ),
    )

    assert {s.name: s for s in restored.skills}["code-review"].enabled is True


@pytest.mark.asyncio
async def test_a_skill_a_nearer_one_shadows_stays_listed_but_inactive(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    config_dir: Path,
) -> None:
    _write_skill(config_dir.parent, "code-review", "Everywhere.", _PROMPT)

    installed = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed",
            SkillsInstalledParams(session_id=backend_contract_session.state.session.id),
        ),
    )

    rows = [
        (skill.scope, skill.active)
        for skill in installed.skills
        if skill.name == "code-review"
    ]
    assert rows == [("project", True), ("global", False)]


@pytest.mark.asyncio
async def test_a_user_skill_named_like_a_builtin_is_listed_as_the_one_in_use(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    config_dir: Path,
) -> None:
    _write_skill(config_dir.parent, "vibe", "My own vibe notes.", _PROMPT)

    installed = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed",
            SkillsInstalledParams(session_id=backend_contract_session.state.session.id),
        ),
    )

    rows = [
        (skill.scope, skill.source, skill.active)
        for skill in installed.skills
        if skill.name == "vibe"
    ]
    assert rows == [("global", "local", True)]


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_skill_written_after_the_session_opened_is_picked_up_without_reload(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
) -> None:
    """Prepare an idle session, then write a second ``SKILL.md`` into the workspace.

    Do nothing else.

    Assert ``skills/list`` soon reports it. The session follows its skill
    directories, so a newly authored skill is invocable without ``/reload``.
    """
    _write_skill(tmp_path, "release-notes", "Draft release notes.", "Group by theme.")

    session_id = backend_contract_session.state.session.id
    for attempt in range(100):
        # The watch starts after the open response, so a write landing first
        # is only seen on the next save; re-saving is what an author does.
        if attempt % 10 == 9:
            _write_skill(
                tmp_path, "release-notes", "Draft release notes.", "Group by theme."
            )
        listed = validate_wire(
            SkillsListResponse,
            await backend_contract_connection.client.request(
                "skills/list", SkillsListParams(session_id=session_id)
            ),
        )
        if "release-notes" in {skill.name for skill in listed.skills}:
            return
        await asyncio.sleep(0.1)
    pytest.fail("the new skill never reached skills/list")


@pytest.mark.asyncio
async def test_set_enabled_refuses_a_name_no_skill_has(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    error = await _set_enabled(
        backend_contract_connection, backend_contract_session, "does-not-exist"
    )

    assert error.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "no installed skill named" in error.error.message


@pytest.mark.asyncio
async def test_set_enabled_refuses_a_plugin_skill(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    installed = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed",
            SkillsInstalledParams(session_id=backend_contract_session.state.session.id),
        ),
    )
    plugin = next(skill for skill in installed.skills if skill.source == "plugin")

    error = await _set_enabled(
        backend_contract_connection, backend_contract_session, plugin.name
    )

    assert plugin.locked is True
    assert "fixed by configuration" in error.error.message
    assert plugin.path is not None and Path(plugin.path).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_filters",
    [{"enabled_skills": ["something-else"]}, {"disabled_skills": ["code-*"]}],
    ids=["outside-the-allowlist", "disabled-by-a-pattern"],
    indirect=True,
)
async def test_set_enabled_refuses_a_skill_a_config_pattern_holds(
    skill_filters: None,
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    installed = validate_wire(
        SkillsInstalledResponse,
        await backend_contract_connection.client.request(
            "skills/installed",
            SkillsInstalledParams(session_id=backend_contract_session.state.session.id),
        ),
    )
    row = next(skill for skill in installed.skills if skill.name == "code-review")

    error = await _set_enabled(
        backend_contract_connection, backend_contract_session, "code-review"
    )

    assert (row.enabled, row.locked) == (False, True)
    assert "fixed by configuration" in error.error.message


@pytest.mark.asyncio
async def test_the_catalog_says_when_nobody_is_signed_in(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY")

    catalog = await _catalog(backend_contract_connection, backend_contract_session)

    assert (catalog.authenticated, catalog.loaded, catalog.skills) == (False, True, [])


@pytest.mark.asyncio
async def test_the_catalog_says_when_it_could_not_load(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(REGISTRY_SKILLS_URL).mock(return_value=httpx.Response(503))

    catalog = await _catalog(backend_contract_connection, backend_contract_session)

    assert (catalog.authenticated, catalog.loaded, catalog.skills) == (True, False, [])


async def _installed(
    connection: BackendContractConnection, session: AppServerSession
) -> SkillsInstalledResponse:
    return validate_wire(
        SkillsInstalledResponse,
        await connection.client.request(
            "skills/installed",
            SkillsInstalledParams(session_id=session.state.session.id),
        ),
    )


@pytest.mark.asyncio
async def test_installed_lists_each_skill_s_file_and_the_ones_that_failed_to_load(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
) -> None:
    """Prepare a workspace skill, and a second one whose description is too long.

    Do read ``skills/installed``.

    Assert the loaded skill says where its file is, and the broken one is an
    issue naming its file: without them a browser cannot say which skill
    failed or where to fix it.
    """
    _write_skill(tmp_path, "too-long", "x" * 1025, _PROMPT)

    installed = await _installed(backend_contract_connection, backend_contract_session)

    rows = {skill.name: skill for skill in installed.skills}
    skill_file = tmp_path / ".vibe" / "skills" / "code-review" / "SKILL.md"
    assert rows["code-review"].path == str(skill_file.resolve())
    broken = tmp_path / ".vibe" / "skills" / "too-long" / "SKILL.md"
    assert [issue.file for issue in installed.issues] == [str(broken.resolve())]
    assert "too-long" not in rows


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_skill_turned_off_mid_turn_is_gone_from_the_next_turn(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
) -> None:
    """Prepare a session running a turn, with the ``code-review`` skill.

    Do turn the skill off while the turn runs, end it, then run another turn.

    Assert the switch is accepted as pending and its runtime already lists the
    skill off, the running turn keeps the skills it started with, and the next
    turn no longer offers the skill to the model.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.content.decode())
        if len(requests) == 1:
            return backend_contract_gated_mistral_response(
                "finished", started=started, release=release
            )
        return backend_contract_mistral_response("noted")

    backend_contract_mistral_api.mock(side_effect=respond)
    session = backend_contract_session

    async def consume_turn(message: str) -> None:
        _ = [event async for event in session.act(message)]

    turn = asyncio.create_task(consume_turn("wait"))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        answer = validate_wire(
            RuntimeMutationResponse,
            await backend_contract_connection.client.request(
                "skills/setEnabled",
                SkillsSetEnabledParams(
                    session_id=session.state.session.id,
                    name="code-review",
                    enabled=False,
                ),
            ),
        )
    finally:
        release.set()
        await turn
    await consume_turn("again")

    assert answer.status is RuntimeMutationStatus.PENDING
    assert "code-review" not in {skill.name for skill in answer.runtime.skills}
    assert "code-review" in requests[0]
    assert "code-review" not in requests[-1]


@pytest.mark.asyncio
async def test_delete_local_removes_a_listed_skill_s_folder_and_nothing_else(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
) -> None:
    """Prepare a workspace skill, and a file that is not a skill.

    Do delete the skill by the path ``skills/installed`` lists, then ask to
    delete the other file.

    Assert the skill's folder is gone and no longer listed, and the other file
    is refused and kept: the path must name a local skill the session lists.
    """
    session_id = backend_contract_session.state.session.id
    installed = await _installed(backend_contract_connection, backend_contract_session)
    path = next(s.path for s in installed.skills if s.name == "code-review")
    assert path is not None
    other = tmp_path / "notes.md"
    other.write_text("keep me", encoding="utf-8")

    await backend_contract_connection.client.request(
        "skills/deleteLocal", SkillsDeleteLocalParams(session_id=session_id, path=path)
    )
    with pytest.raises(AppServerResponseError) as refused:
        await backend_contract_connection.client.request(
            "skills/deleteLocal",
            SkillsDeleteLocalParams(session_id=session_id, path=str(other)),
        )

    assert not Path(path).parent.exists()
    listed = await _installed(backend_contract_connection, backend_contract_session)
    assert "code-review" not in {skill.name for skill in listed.skills}
    assert refused.value.error.code is ProtocolErrorCode.NOT_FOUND
    assert other.read_text(encoding="utf-8") == "keep me"


@pytest.mark.asyncio
async def test_delete_local_drops_only_the_link_of_a_linked_skill_folder(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Prepare a workspace skill folder that links to a folder outside the project.

    Do delete it by the path ``skills/installed`` lists.

    Assert the link is gone and the folder it pointed at survives: the listed
    path is the link's target, and deleting it would wipe the user's files.
    """
    outside = tmp_path_factory.mktemp("outside") / "linked"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        f"---\nname: linked\ndescription: Linked skill.\n---\n\n{_PROMPT}\n",
        encoding="utf-8",
    )
    link = tmp_path / ".vibe" / "skills" / "linked"
    link.symlink_to(outside, target_is_directory=True)
    session_id = backend_contract_session.state.session.id
    installed = await _installed(backend_contract_connection, backend_contract_session)
    path = next(s.path for s in installed.skills if s.name == "linked")
    assert path is not None

    await backend_contract_connection.client.request(
        "skills/deleteLocal", SkillsDeleteLocalParams(session_id=session_id, path=path)
    )

    assert not link.exists() and not link.is_symlink()
    assert (outside / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_delete_local_drops_a_link_named_apart_from_its_target(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Prepare a workspace link ``alias`` to a skill folder named ``target``.

    Do delete it by the path ``skills/installed`` lists.

    Assert the row names the link and says it is one, and the delete drops the
    link and keeps the folder: the listed path must never be the link's target.
    """
    outside = tmp_path_factory.mktemp("outside") / "target"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        f"---\nname: target\ndescription: Linked skill.\n---\n\n{_PROMPT}\n",
        encoding="utf-8",
    )
    link = tmp_path / ".vibe" / "skills" / "alias"
    link.symlink_to(outside, target_is_directory=True)
    session_id = backend_contract_session.state.session.id
    installed = await _installed(backend_contract_connection, backend_contract_session)
    row = next(s for s in installed.skills if s.name == "target")

    await backend_contract_connection.client.request(
        "skills/deleteLocal",
        SkillsDeleteLocalParams(session_id=session_id, path=str(row.path)),
    )

    assert row.path == str(link / "SKILL.md") and row.linked
    assert not link.is_symlink()
    assert (outside / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_delete_local_of_a_global_skill_keeps_a_project_link_to_it(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    tmp_path: Path,
    config_dir: Path,
) -> None:
    """Prepare a global skill and a workspace link to that same folder.

    Do delete the global row.

    Assert the global folder is gone and the workspace link is untouched: each
    row deletes the entry its own root lists.
    """
    folder = config_dir / "skills" / "shared"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: shared\ndescription: Shared skill.\n---\n\n{_PROMPT}\n",
        encoding="utf-8",
    )
    link = tmp_path / ".vibe" / "skills" / "shared"
    link.symlink_to(folder, target_is_directory=True)
    session_id = backend_contract_session.state.session.id
    installed = await _installed(backend_contract_connection, backend_contract_session)
    rows = [s for s in installed.skills if s.name == "shared"]
    global_row = next(s for s in rows if s.scope == "global")

    await backend_contract_connection.client.request(
        "skills/deleteLocal",
        SkillsDeleteLocalParams(session_id=session_id, path=str(global_row.path)),
    )

    assert {s.scope for s in rows} == {"global", "project"}
    assert not folder.exists()
    assert link.is_symlink()


@pytest.mark.asyncio
async def test_the_runtime_lists_skills_without_their_files(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session with a workspace skill.

    Do list its skills through ``skills/list``.

    Assert no row carries a file: only ``skills/installed`` names files, for
    the browser that opens and deletes them.
    """
    session_id = backend_contract_session.state.session.id

    listed = await backend_contract_connection.client.request(
        "skills/list", SkillsListParams(session_id=session_id)
    )

    skills = SkillsListResponse.model_validate(listed).skills
    assert skills and all(skill.path is None for skill in skills)


def _serve_registry_skill(
    respx_mock: respx.MockRouter,
    *,
    name: str,
    version: int = 2,
    description: str = "Query company data.",
) -> respx.Route:
    """A registry that serves skill ``cid``, at version 2 unless told otherwise."""
    return respx_mock.get(f"{REGISTRY_SKILLS_URL}/cid").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "cid",
                "skill": {
                    "skillName": "Ask Data",
                    "skillDescription": description,
                    "skillBody": "Run the query.",
                },
                "version": version,
                "metadata": {"name": name},
            },
        )
    )


async def _import_for_session(
    connection: BackendContractConnection, session_id: str
) -> SkillsSessionImportResponse:
    return validate_wire(
        SkillsSessionImportResponse,
        await connection.client.request(
            "skills/import",
            SkillsImportParams(
                session_id=session_id, skill_id="cid", alias="latest", scope="session"
            ),
        ),
    )


@pytest.mark.asyncio
async def test_a_session_import_loads_without_registry_skills_on(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    respx_mock: respx.MockRouter,
    config_dir: Path,
) -> None:
    """Prepare a session with registry skills off, as every Desktop session runs.

    Do import a cloud skill with ``scope: "session"``.

    Assert the session lists it, writes no manifest, and holds its version against
    a cleanup.
    """
    _serve_registry_skill(respx_mock, name="ask-data")

    response = await _import_for_session(
        backend_contract_connection, backend_contract_session.state.session.id
    )

    skills = {s.name: s for s in response.runtime.skills}
    assert skills["ask-data"].source == "registry"
    assert skills["ask-data"].scope == "session"
    assert not response.runtime.config.experimental_enable_registry_skills
    assert not (config_dir / "skills.toml").exists()
    for _ in range(50):
        if ("cid", 2) in _leases.held_versions():
            break
        await asyncio.sleep(0.02)
    assert ("cid", 2) in _leases.held_versions()


@pytest.mark.asyncio
async def test_a_session_pick_named_like_a_workspace_skill_takes_a_cloud_name(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    backend_contract_session_options: SessionOptions,
    respx_mock: respx.MockRouter,
) -> None:
    assert backend_contract_session_options.cwd is not None
    _write_skill(
        Path(backend_contract_session_options.cwd), "code-review", "Local.", _PROMPT
    )
    _serve_registry_skill(respx_mock, name="code-review")

    response = await _import_for_session(
        backend_contract_connection, backend_contract_session.state.session.id
    )

    skills = {s.name: s for s in response.runtime.skills}
    assert response.skill_name == "code-review-cloud"
    assert skills["code-review"].source != "registry"
    assert skills["code-review-cloud"].source == "registry"
    assert skills["code-review-cloud"].registry is not None
    assert skills["code-review-cloud"].registry.skill_id == "cid"


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_session_pick_survives_reopening_the_session(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """Prepare a saved session with a cloud skill picked in it.

    Do close the app server and resume the session in a new one.

    Assert the skill is still there, without another download.
    """
    registry = _serve_registry_skill(respx_mock, name="ask-data")
    session_id = backend_contract_persistent_session.session_id
    # A session is only saved once it has a turn to resume.
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("noted")
    )
    _ = [event async for event in backend_contract_persistent_session.act("hello")]
    await _import_for_session(backend_contract_persistent_connection, session_id)
    await backend_contract_persistent_session.close()
    await backend_contract_persistent_connection.host.close()
    downloads = registry.call_count

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        await reopened.client.request(
            "session/resume", SessionResumeParams(session_id=session_id)
        )
        listed = validate_wire(
            SkillsListResponse,
            await reopened.client.request(
                "skills/list", SkillsListParams(session_id=session_id)
            ),
        )
    finally:
        await reopened.host.close()

    assert {s.name: s for s in listed.skills}["ask-data"].scope == "session"
    assert registry.call_count == downloads


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_fork_keeps_the_skills_picked_in_its_source(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
) -> None:
    _serve_registry_skill(respx_mock, name="ask-data")
    source_id = backend_contract_persistent_session.session_id
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("noted")
    )
    _ = [event async for event in backend_contract_persistent_session.act("hello")]
    await _import_for_session(backend_contract_persistent_connection, source_id)
    client = backend_contract_persistent_connection.client

    fork = SessionForkResponse.model_validate(
        await client.request(
            "session/fork", SessionForkParams(source_session_id=source_id)
        )
    )
    fork_id = fork.state.session.id
    await client.request("session/delete", SessionDeleteParams(session_id=source_id))
    listed = validate_wire(
        SkillsListResponse,
        await client.request("skills/list", SkillsListParams(session_id=fork_id)),
    )

    assert "ask-data" in {s.name for s in listed.skills}


async def _saved_session_with_a_pick(
    connection: BackendContractConnection,
    session: AppServerSession,
    mistral_api: respx.Route,
    mistral_response: Callable[[str], httpx.Response],
) -> str:
    """A saved session with ``cid`` picked in it, closed with its app server."""
    session_id = session.session_id
    # A session is only saved once it has a turn to resume.
    mistral_api.mock(return_value=mistral_response("noted"))
    _ = [event async for event in session.act("hello")]
    await _import_for_session(connection, session_id)
    await session.close()
    await connection.host.close()
    return session_id


async def _skills_of(
    connection: BackendContractConnection, session_id: str
) -> dict[str, str]:
    listed = validate_wire(
        SkillsListResponse,
        await connection.client.request(
            "skills/list", SkillsListParams(session_id=session_id)
        ),
    )
    return {s.name: s.scope for s in listed.skills}


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_fork_of_a_closed_session_saves_its_picks(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
    tmp_path: Path,
) -> None:
    """Prepare a saved session with a cloud skill picked in it, not open anywhere.

    Do fork it.

    Assert the fork runs the pick and saves it in its own meta.json, so it has the
    pick when it is resumed later.
    """
    _serve_registry_skill(respx_mock, name="ask-data")
    source_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )

    forking = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        fork = SessionForkResponse.model_validate(
            await forking.client.request(
                "session/fork", SessionForkParams(source_session_id=source_id)
            )
        )
        fork_id = fork.state.session.id
        skills = await _skills_of(forking, fork_id)
    finally:
        await forking.host.close()
    saved = SessionMetadata.model_validate_json(
        (tmp_path / "sessions" / "unified" / fork_id / "meta.json").read_text()
    )

    assert skills.get("ask-data") == "session"
    assert [(pin.skill_id, pin.version) for pin in saved.skill_pins] == [("cid", 2)]


async def _skill_warnings(
    session: AppServerSession, *, until: str
) -> AsyncIterator[ServerWarning]:
    async for event in session.events():
        if not isinstance(event, ServerWarning):
            continue
        if "cloud skills picked in this session" not in event.params.warning.message:
            continue
        yield event
        if until in event.params.warning.message:
            return


async def _restored() -> None:
    """Until the reopened session holds the version it fetched again, which it does
    once that version loads.
    """
    async with asyncio.timeout(10):
        while ("cid", 2) not in _leases.held_versions():
            await asyncio.sleep(0.02)


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_reopen_fetches_again_a_pick_a_cleanup_took(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """Prepare a saved session with a pick whose stored version is then removed.

    Do resume the session.

    Assert the session fetches that version again after the open, and then offers
    the skill.
    """
    registry = _serve_registry_skill(respx_mock, name="ask-data")
    session_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )
    shutil.rmtree(_store.skill_dir("cid", 2))
    downloads = registry.call_count

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        await reopened.client.request(
            "session/resume", SessionResumeParams(session_id=session_id)
        )
        await _restored()
        skills = await _skills_of(reopened, session_id)
    finally:
        await reopened.host.close()

    assert registry.call_count == downloads + 1
    assert registry.calls.last.request.url.params["version"] == "2"
    assert skills.get("ask-data") == "session"


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_refused_pick_keeps_the_version_the_session_already_ran(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """Prepare a saved session that runs v2 of a cloud skill.

    Do pick it again once the registry serves a v3 the session cannot read.

    Assert the pick is refused and the session still runs, and saves, v2.
    """
    session_id = backend_contract_persistent_session.session_id
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("noted")
    )
    _ = [event async for event in backend_contract_persistent_session.act("hello")]
    _serve_registry_skill(respx_mock, name="ask-data")
    await _import_for_session(backend_contract_persistent_connection, session_id)
    _serve_registry_skill(
        respx_mock, name="ask-data", version=3, description="x" * 2000
    )

    with pytest.raises(AppServerResponseError):
        await _import_for_session(backend_contract_persistent_connection, session_id)
    listed = validate_wire(
        SkillsListResponse,
        await backend_contract_persistent_connection.client.request(
            "skills/list", SkillsListParams(session_id=session_id)
        ),
    )
    saved = SessionMetadata.model_validate_json(
        (tmp_path / "sessions" / "unified" / session_id / "meta.json").read_text()
    )

    skill = {s.name: s for s in listed.skills}["ask-data"]
    assert skill.registry is not None
    assert skill.registry.version == 2
    assert [(pin.skill_id, pin.version) for pin in saved.skill_pins] == [("cid", 2)]


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_pick_whose_reload_fails_is_not_kept(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import _UnifiedSkillsHost

    session_id = backend_contract_persistent_session.session_id
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("noted")
    )
    _ = [event async for event in backend_contract_persistent_session.act("hello")]
    _serve_registry_skill(respx_mock, name="ask-data")
    refresh = _UnifiedSkillsHost.refresh
    calls = 0

    async def fails_once(self: _UnifiedSkillsHost) -> RuntimeMutationResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("config unreadable")
        return await refresh(self)

    monkeypatch.setattr(_UnifiedSkillsHost, "refresh", fails_once)

    with pytest.raises(AppServerResponseError):
        await _import_for_session(backend_contract_persistent_connection, session_id)
    saved = SessionMetadata.model_validate_json(
        (tmp_path / "sessions" / "unified" / session_id / "meta.json").read_text()
    )

    assert saved.skill_pins == []


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_reopen_does_not_wait_on_the_registry_for_a_pick_a_cleanup_took(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """Prepare a saved session whose picked version a cleanup removed, and a
    registry that never answers.

    Do resume the session.

    Assert the resume answers while the download still hangs, without the pick.
    """
    _serve_registry_skill(respx_mock, name="ask-data")
    session_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )
    shutil.rmtree(_store.skill_dir("cid", 2))
    respx_mock.routes.clear()
    reached = registry_stalls(respx_mock)

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        async with asyncio.timeout(10):
            await reopened.client.request(
                "session/resume", SessionResumeParams(session_id=session_id)
            )
            skills = await _skills_of(reopened, session_id)
            await reached.wait()
    finally:
        await reopened.host.close()

    assert "ask-data" not in skills


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_the_first_turn_after_a_reopen_runs_a_pick_a_cleanup_took(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """Prepare a saved session whose picked version a cleanup removed.

    Do resume it and send a turn at once.

    Assert the turn waits for the download and offers the skill to the model.
    """
    _serve_registry_skill(respx_mock, name="ask-data")
    session_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )
    shutil.rmtree(_store.skill_dir("cid", 2))
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("noted")
    )

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await reopened.host.resume_session(session_id)
        _ = [event async for event in session.act("again")]
    finally:
        await reopened.host.close()

    sent = backend_contract_mistral_api.calls.last.request.content.decode()
    assert "ask-data" in sent, "the first turn ran without the restored pick"


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_reopen_tells_the_client_when_a_pick_cannot_be_fetched_again(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepare a saved session whose picked version a cleanup removed, and a
    registry that keeps failing.

    Do resume it.

    Assert the session warns the client which pick it runs without, tries the
    download again, then warns once more when it gives up.
    """
    monkeypatch.setattr(
        _adapter_module, "_SKILL_RESTORE_RETRY_DELAYS_SECONDS", (0.0, 0.0)
    )
    _serve_registry_skill(respx_mock, name="ask-data")
    session_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )
    shutil.rmtree(_store.skill_dir("cid", 2))
    respx_mock.routes.clear()
    registry = respx_mock.get(f"{REGISTRY_SKILLS_URL}/cid").mock(
        return_value=httpx.Response(503)
    )

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await reopened.host.resume_session(session_id)
        async with asyncio.timeout(10):
            warnings = [
                event.params.warning.message
                async for event in _skill_warnings(session, until="Pick them again")
            ]
    finally:
        await reopened.host.close()

    assert registry.call_count == 3
    assert len(warnings) == 2
    assert all("ask-data" in warning for warning in warnings)
    assert "until a retry succeeds" in warnings[0]


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_reopen_tells_the_client_when_a_retry_brings_a_pick_back(
    backend_contract_persistent_connection: BackendContractConnection,
    backend_contract_persistent_session: AppServerSession,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepare a saved session whose picked version a cleanup removed, and a
    registry that fails once.

    Do resume it.

    Assert the client is warned the pick is missing, then told it is back, and the
    session offers it.
    """
    monkeypatch.setattr(_adapter_module, "_SKILL_RESTORE_RETRY_DELAYS_SECONDS", (0.0,))
    registry = _serve_registry_skill(respx_mock, name="ask-data")
    session_id = await _saved_session_with_a_pick(
        backend_contract_persistent_connection,
        backend_contract_persistent_session,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
    )
    shutil.rmtree(_store.skill_dir("cid", 2))
    served = registry.return_value
    assert served is not None
    registry.mock(side_effect=[httpx.Response(503), served])

    reopened = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await reopened.host.resume_session(session_id)
        async with asyncio.timeout(10):
            notices = [
                event.params.warning.message
                async for event in _skill_warnings(session, until="available again")
            ]
        await _restored()
        skills = await _skills_of(reopened, session_id)
    finally:
        await reopened.host.close()

    assert len(notices) == 2
    assert "until a retry succeeds" in notices[0]
    assert "ask-data" in notices[1]
    assert skills.get("ask-data") == "session"


@pytest.mark.parametrize(
    "skill_filters", [{"enabled_skills": ["code-review"]}], indirect=True
)
@pytest.mark.asyncio
async def test_a_pick_the_configuration_filters_out_is_refused_and_not_kept(
    skill_filters: None,
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    respx_mock: respx.MockRouter,
) -> None:
    _serve_registry_skill(respx_mock, name="ask-data")
    session_id = backend_contract_session.state.session.id

    with pytest.raises(AppServerResponseError) as refused:
        await _import_for_session(backend_contract_connection, session_id)
    skills = await _skills_of(backend_contract_connection, session_id)

    assert refused.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "ask-data" not in skills
