"""Cloud plugins on this machine: the user's AI Registry plugins, through le-chat.

le-chat (``/api/v1/code/plugins``) lists every plugin the user can read with
whether it is on for them, the same choice Vibe Work reads. The plugins that
are on are downloaded into a cache under ``~/.vibe/remote-plugins`` and resolve
as the ``remote`` scope. A toggle is written back to le-chat, so a plugin is on
in Work and on this machine, or in neither.

The last list is kept on disk, so plugins keep loading offline. It is refreshed
when older than ``REMOTE_PLUGINS_TTL_S``, at a session start and when the
Plugins page lists plugins (the page waits for the TTL like a session does; a
toggle refreshes at once). After a failed refresh, the next one waits a delay
that doubles with each failure, so starting sessions offline does not wait on
le-chat each time.

The list belongs to the account it was fetched for (``principal``), and is
only read while the credential in use acts for that account. A credential that
is positively absent (no API key, no stored CLI sign-in) deletes it and its
packages. When the account cannot be told (a sign-in to renew, a credential
that cannot be read), nothing is served but the packages stay, so the same
account signing back in reuses them. Desktop's sign-out is of that kind: its
broker answers ``reauthentication_required``, so the list stops being served
at once and its packages go with the next account's list.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
import io
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import threading
import time
from typing import TYPE_CHECKING, Literal
import uuid
import weakref
import zipfile

import httpx2
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.core.paths import VIBE_HOME, GlobalPath
from vibe.core.platform_auth import (
    CachedPrincipal,
    PlatformCredentialFactory,
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialUnavailable,
)
from vibe.core.platform_auth._mistral import (
    api_key_credentials,
    credential_request,
    platform_target,
    resolve_snapshot,
)
from vibe.core.plugins._content import digest_plugin_tree
from vibe.core.utils.file_lock import async_file_lock
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

__all__ = [
    "REMOTE_PLUGINS_DIR",
    "REMOTE_PLUGINS_TTL_S",
    "SYNC_WAIT_S",
    "UNAVAILABLE_ERROR",
    "RemotePlugin",
    "RemotePluginsError",
    "RemotePluginsState",
    "current_remote_plugins_state",
    "load_remote_plugins_state",
    "refresh_remote_plugins",
    "remote_plugin_dirs",
    "remote_plugin_for_root",
    "remote_plugin_names",
    "set_remote_plugin_enabled",
    "sync_remote_plugins",
]

REMOTE_PLUGINS_DIR = GlobalPath(lambda: VIBE_HOME.path / "remote-plugins")
# What library plugins write while they run (``_native``'s data root for the
# ``remote`` scope). Keyed by plugin name, so it belongs to one account.
REMOTE_PLUGIN_DATA_DIR = GlobalPath(lambda: VIBE_HOME.path / "plugin-data" / "remote")
# The account the data under ``REMOTE_PLUGIN_DATA_DIR`` was written for.
_DATA_OWNER_FILE = ".owner"
# Served by le-chat, appended to ``vibe_base_url``.
REMOTE_PLUGINS_PATH = "/api/v1/code/plugins"
REMOTE_PLUGINS_TTL_S = 15 * 60
_LIST_TIMEOUT_S = 10.0
_DOWNLOAD_TIMEOUT_S = 60.0
# A package larger than this is refused rather than unpacked.
_MAX_UNPACKED_BYTES = 200 * 1024 * 1024
# The most a package download may carry: le-chat's own ceiling on what it
# passes on (``MAX_PLUGIN_DOWNLOAD_BYTES``: AI Registry's 100 MiB archive limit
# plus 5% for its re-zip). Beyond it, the download is cut and refused.
_MAX_DOWNLOAD_BYTES = 105 * 1024 * 1024
_STATE_FILE = "state.json"
# Beside a version's package: the digest of the package as unpacked. A package
# that no longer matches it was changed on disk and is downloaded again.
_DIGEST_FILE = ".content-sha256"
# Written into a package by running it or browsing it, not by editing it.
_UNTRACKED_NAMES = frozenset({"__pycache__", ".DS_Store"})
# Held across processes while a sync writes the store and the state.
_LOCK_FILE = ".sync.lock"
# A sync waiting longer than this for another process's reads the list it left.
_LOCK_TIMEOUT_S = 2 * _DOWNLOAD_TIMEOUT_S
# How long the account a credential acts for may take to resolve.
_PRINCIPAL_TIMEOUT_S = 3.0
# After a failed refresh, the next waits this long, doubled per further failure.
_RETRY_AFTER_S = 60.0
# What le-chat answers when it does not offer plugins to this user at all.
UNAVAILABLE_ERROR = "code_plugins_unavailable"
_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class RemotePluginsError(Exception):
    def __init__(
        self, reason: str, *, status: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        # le-chat's ``error``, when it gave one.
        self.code = code


class RemotePlugin(BaseModel):
    """A plugin as le-chat lists it."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    title: str | None = None
    description: str | None = None
    version: int
    owned_by_user: bool = Field(default=False, alias="ownedByUser")
    sharing_scope: str | None = Field(default=None, alias="sharingScope")
    enabled: bool
    can_toggle: bool = Field(default=True, alias="canToggle")
    # le-chat's copy of ``ai.mistral.vibe/icon.json``, for a plugin not
    # downloaded; the page validates it before drawing it.
    icon: JsonValue = None


class _ListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugins: list[RemotePlugin]
    incomplete: bool = False
    # Set here, never by le-chat: it answered ``UNAVAILABLE_ERROR``.
    unavailable: bool = Field(default=False, exclude=True)


class RemotePluginsState(BaseModel):
    """The last list, and when it was read; what loads offline."""

    model_config = ConfigDict(extra="ignore")

    synced_at: float = 0.0
    base_url: str = ""
    plugins: list[RemotePlugin] = Field(default_factory=list)
    # Download failures by plugin id, shown on the Plugins page.
    errors: dict[str, str] = Field(default_factory=dict)
    # Library plugins another one of their name hides; listed, never loaded.
    shadowed: list[RemotePlugin] = Field(default_factory=list)
    # The account the list was fetched for (``PlatformCredentialSnapshot.
    # principal``, a non-secret hash); None when the credential names none.
    principal: str | None = None
    # Refreshes failed in a row, and when the next one may be tried.
    failures: int = 0
    retry_at: float = 0.0

    def fresh(
        self, now: float, base_url: str, principal: str | None, *, ttl_s: float
    ) -> bool:
        """Whether this list may be served without asking le-chat again."""
        return (
            self.base_url == base_url
            and self.principal == principal
            and now - self.synced_at < ttl_s
        )


@dataclass(frozen=True, slots=True)
class _Endpoint:
    base_url: str
    credentials: PlatformCredentialProvider


# No Mistral credential at all: nobody is signed in.
_SIGNED_OUT: Literal["signed_out"] = "signed_out"
# Whether anyone is signed in cannot be told: no Mistral provider in this
# config, or a credential that could not be read.
_UNKNOWN: Literal["unknown"] = "unknown"


def _endpoint(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None
) -> _Endpoint | Literal["signed_out", "unknown"]:
    """Where the list is read, with which credential.

    As ``mistral_credentials``, but tells a credential that is positively
    absent (``has_credential`` False: no API key, no stored sign-in) from one
    that could not be read, which that function folds into the same None.
    Only the first is a sign-out.
    """
    try:
        provider = config.get_mistral_provider()
        if provider is None:
            return _UNKNOWN
        selected = (credentials or api_key_credentials)(provider.api_key_env_var)
        if isinstance(selected, CachedPrincipal) and not selected.has_credential():
            return _SIGNED_OUT
    except Exception:
        logger.warning("Could not read the Mistral credential", exc_info=True)
        return _UNKNOWN
    return _Endpoint(config.vibe_base_url.rstrip("/"), selected)


def _current_principal(endpoint: _Endpoint) -> str | None:
    """The account the credential acts for, known without network or refresh."""
    if not isinstance(endpoint.credentials, CachedPrincipal):
        return None
    try:
        return endpoint.credentials.cached_principal()
    except Exception:
        return None


def current_remote_plugins_state(
    config: VibeConfigSchema | None,
    credentials: PlatformCredentialFactory | None = None,
) -> RemotePluginsState:
    """The last list, when it is the current account's; else an empty one.

    Read from disk and the credential's cached principal only, never the
    network. A credential whose account is not known yet (Desktop before its
    first resolve after a sign-in, a provider naming no account) serves
    nothing: on a shared machine, plugins left by whoever used it last must
    not load until the account is known. A session start syncs first, and the
    sync resolves the credential, so it is known by the time a session reads.
    """
    if config is None or not config.experimental_enable_remote_plugins:
        return RemotePluginsState()
    endpoint = _endpoint(config, credentials)
    if not isinstance(endpoint, _Endpoint):
        return RemotePluginsState()
    state = load_remote_plugins_state()
    principal = _current_principal(endpoint)
    if (
        principal is None
        or state.principal != principal
        or state.base_url != endpoint.base_url
    ):
        return RemotePluginsState()
    return state


def _state_path() -> Path:
    return REMOTE_PLUGINS_DIR.path / _STATE_FILE


def _package_dir(plugin_id: str, version: int) -> Path:
    if not _SAFE_ID.fullmatch(plugin_id):
        raise ValueError(f"unsafe remote plugin id: {plugin_id!r}")
    return REMOTE_PLUGINS_DIR.path / "store" / plugin_id / str(version)


def load_remote_plugins_state() -> RemotePluginsState:
    try:
        raw = _state_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return RemotePluginsState()
    except OSError as exc:
        logger.warning("Could not read the remote plugins state: %s", exc)
        return RemotePluginsState()
    try:
        return RemotePluginsState.model_validate_json(raw)
    except ValidationError:
        logger.warning("Ignoring an unreadable remote plugins state")
        return RemotePluginsState()


# Held by every write of the state file in this process: a toggle stores its
# choice without the sync locks (``_apply_choice``), and must not load the
# list, then store it back over what a sync stored in between.
_state_write_lock = threading.Lock()


def _store_state(state: RemotePluginsState) -> None:
    with _state_write_lock:
        _write_state(state)


def _write_state(state: RemotePluginsState) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    temp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temp, path)


def remote_plugin_names(
    config: VibeConfigSchema | None,
    credentials: PlatformCredentialFactory | None = None,
) -> frozenset[str]:
    """Every name the current account's library lists, on or off.

    Each belongs to the library whether its copy is on or not, so a local
    plugin of one of these names is not loaded and a name always means one
    plugin: the Plugins page keys rows and routes toggles by it.
    """
    state = current_remote_plugins_state(config, credentials)
    return frozenset(plugin.name for plugin in state.plugins)


def remote_plugin_dirs(
    config: VibeConfigSchema | None,
    credentials: PlatformCredentialFactory | None = None,
) -> list[Path]:
    """The package folders of the current account's plugins that are on.

    Read from disk only, so resolving never waits on the network. A plugin
    whose package is missing (its download failed) or was changed on disk is
    left out until the next sync downloads it again: a synced plugin is the
    published version, read-only. None while
    ``experimental_enable_remote_plugins`` is off: turning it off unloads them.
    """
    state = current_remote_plugins_state(config, credentials)
    dirs: list[Path] = []
    for plugin in state.plugins:
        if not plugin.enabled:
            continue
        try:
            root = _package_dir(plugin.id, plugin.version)
        except ValueError:
            continue
        package = _intact_package(root)
        if package is not None:
            dirs.append(package)
    return dirs


def _intact_package(version_dir: Path) -> Path | None:
    """The package folder, when its files are still the ones unpacked."""
    package = _package_root(version_dir)
    if package is None:
        return None
    try:
        recorded = (version_dir / _DIGEST_FILE).read_text(encoding="utf-8").strip()
        if _digest(package) == recorded:
            return package
    except OSError:
        pass
    logger.warning("Remote plugin package changed on disk: %s", package)
    return None


def _digest(package: Path) -> str:
    return digest_plugin_tree(package, ignored_names=_UNTRACKED_NAMES)


def _all_intact(state: RemotePluginsState) -> bool:
    """Whether every plugin that is on still has its package as unpacked.

    A package missing because its download failed does not count: the next
    refresh retries it on its own schedule.
    """
    for plugin in state.plugins:
        if not plugin.enabled:
            continue
        try:
            version_dir = _package_dir(plugin.id, plugin.version)
        except ValueError:
            continue
        if _package_root(version_dir) is None:
            continue
        if _intact_package(version_dir) is None:
            return False
    return True


def _package_root(version_dir: Path) -> Path | None:
    """The plugin folder inside an unpacked package: the zip's one top folder."""
    try:
        children = [child for child in version_dir.iterdir() if child.is_dir()]
    except OSError:
        return None
    return children[0] if len(children) == 1 else None


async def _get(endpoint: _Endpoint, path: str, *, timeout: float) -> httpx2.Response:
    url = f"{endpoint.base_url}{REMOTE_PLUGINS_PATH}{path}"
    try:
        response = await credential_request(
            endpoint.credentials, "GET", url, timeout=timeout
        )
    except PlatformCredentialUnavailable as exc:
        raise RemotePluginsError(
            f"no platform credential ({exc.required.reason})"
        ) from exc
    except httpx2.RequestError as exc:
        raise RemotePluginsError(f"request failed: {exc}") from exc
    if not response.is_success:
        raise RemotePluginsError(
            f"unexpected status {response.status_code}",
            status=response.status_code,
            code=_error_code(response),
        )
    return response


# How long a caller waits on a sync; past it, it reads the last list and the
# sync finishes in the background.
SYNC_WAIT_S = 5.0
_background_syncs: set[asyncio.Task[RemotePluginsState]] = set()

type Clock = Callable[[], float]
# What asked for a sync, as the ``vibe.remote_plugins.sync.done`` event says.
type SyncTrigger = Literal["session_start", "plugins_page", "toggle"]


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one sync did, for its telemetry event."""

    status: Literal["success", "error", "skipped"]
    error_type: str | None = None
    skip_reason: str | None = None
    downloaded: int = 0
    failed: int = 0


async def refresh_remote_plugins(
    config: VibeConfigSchema,
    credentials: PlatformCredentialFactory | None = None,
    *,
    force: bool = False,
    wait_s: float | None = None,
    ttl_s: float | None = None,
    clock: Clock = time.time,
    trigger: SyncTrigger | None = None,
    token: SyncToken | None = None,
) -> RemotePluginsState:
    """Sync, waiting at most ``wait_s`` (``SYNC_WAIT_S``): a session start or a
    page load never hangs on AI Registry. Never raises.
    """
    task = asyncio.create_task(
        sync_remote_plugins(
            config,
            credentials,
            force=force,
            ttl_s=ttl_s,
            clock=clock,
            trigger=trigger,
            token=token,
        )
    )
    _background_syncs.add(task)
    task.add_done_callback(_background_syncs.discard)
    try:
        return await asyncio.wait_for(
            asyncio.shield(task), SYNC_WAIT_S if wait_s is None else wait_s
        )
    except TimeoutError:
        logger.info("Remote plugins still syncing; using the last list")
    except Exception:
        logger.warning("Remote plugins sync failed", exc_info=True)
    return current_remote_plugins_state(config, credentials)


# One sync at a time per process: session starts and the Plugins page each ask
# for one, and two downloading the same version would replace each other's.
# Across processes, ``_LOCK_FILE`` does the same.
# Per event loop: an ``asyncio.Lock`` binds to the loop that first waits on it.
_sync_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _sync_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _sync_locks.get(loop)
    if lock is None:
        lock = _sync_locks[loop] = asyncio.Lock()
    return lock


_syncs_in_line: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, int] = (
    weakref.WeakKeyDictionary()
)


@asynccontextmanager
async def _sync_turn() -> AsyncIterator[None]:
    """Hold ``_sync_lock``, counted as syncing from the wait for it on: as one
    sync hands the lock to the next, the lock reads free for a moment, and a
    page read then must not take the process as idle.
    """
    loop = asyncio.get_running_loop()
    _syncs_in_line[loop] = _syncs_in_line.get(loop, 0) + 1
    try:
        async with _sync_lock():
            yield
    finally:
        _syncs_in_line[loop] -= 1


def remote_plugins_syncing() -> bool:
    """Whether a sync of this process runs or waits its turn now (or waits on
    another process's).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return _syncs_in_line.get(loop, 0) > 0


type PendingWork = Literal["downloading", "removing"]


class SyncToken:
    """Names one sync: the pending work it owns is cleared when it ends.

    A caller that says what the sync it starts will do passes its own.
    """


# What a sync of this process will do to each plugin's package, by plugin id,
# with the sync that owns it: said by a toggle le-chat accepted (owned by the
# sync it starts) and by a sync once it has read the list; dropped when the
# owning sync ends, however it ends. A new process starts with none; a sync of
# another process (the CLI) is not reported.
_pending_work: dict[str, tuple[PendingWork, SyncToken]] = {}


def remote_plugin_pending(plugin_id: str) -> PendingWork | None:
    """Whether a sync of this process is downloading or deleting the package."""
    entry = _pending_work.get(plugin_id)
    return None if entry is None else entry[0]


def _clear_pending_work(token: SyncToken) -> None:
    """Drop what ``token``'s sync owns."""
    for key, (_, owner) in list(_pending_work.items()):
        if owner is token:
            del _pending_work[key]


def _claim_pending_work(work: dict[str, PendingWork], token: SyncToken) -> None:
    """Say ``work`` as ``token``'s, but for plugins another sync owns already."""
    for plugin_id, kind in work.items():
        owned = _pending_work.get(plugin_id)
        if owned is None or owned[1] is token:
            _pending_work[plugin_id] = (kind, token)


def _work_ahead(
    plugins: list[RemotePlugin], previous: dict[str, RemotePlugin]
) -> dict[str, PendingWork]:
    """The packages this sync will download (on, not unpacked at that version)
    and delete (off, or dropped by the list, with a version still on disk).
    """
    work: dict[str, PendingWork] = {}
    listed = {plugin.id for plugin in plugins}
    for plugin in plugins:
        try:
            version_dir = _package_dir(plugin.id, plugin.version)
        except ValueError:
            continue
        if plugin.enabled:
            # Unpacked or not; whether it is intact is the download's to check.
            if _package_root(version_dir) is None:
                work[plugin.id] = "downloading"
        elif version_dir.parent.is_dir():
            work[plugin.id] = "removing"
    for plugin_id in previous.keys() - listed:
        with suppress(ValueError):
            if _package_dir(plugin_id, 0).parent.is_dir():
                work[plugin_id] = "removing"
    return work


async def sync_remote_plugins(
    config: VibeConfigSchema,
    credentials: PlatformCredentialFactory | None = None,
    *,
    force: bool = False,
    ttl_s: float | None = None,
    clock: Clock = time.time,
    lock_timeout_s: float | None = None,
    principal_timeout_s: float | None = None,
    trigger: SyncTrigger | None = None,
    token: SyncToken | None = None,
) -> RemotePluginsState:
    """Refresh the list when older than ``ttl_s`` (``REMOTE_PLUGINS_TTL_S``) or
    on ``force``, and download what is on.

    Never raises: on a failure the last list stays in force, so a plugin that
    loaded yesterday still loads offline, and the next refresh waits a backoff
    (unless ``force``). With no credential at all (no API key, no stored CLI
    sign-in), the last list and its packages are deleted; signed in as another
    account, or as one that cannot be told (Desktop after a sign-out), the list
    is not served and its packages wait for the next list. A sync that
    waited for another one reads the list it left, unless ``force``; one that
    waited ``lock_timeout_s`` for another process reads the list on disk.

    With a ``trigger``, reports what it did as ``vibe.remote_plugins.sync.done``.
    """
    token = token or SyncToken()
    try:
        if not config.experimental_enable_remote_plugins:
            return RemotePluginsState()
        started = time.monotonic()
        state, outcome = await _sync_locked(
            config,
            credentials,
            force=force,
            ttl_s=ttl_s,
            clock=clock,
            lock_timeout_s=lock_timeout_s,
            principal_timeout_s=principal_timeout_s,
            token=token,
        )
        if trigger is not None:
            _report_sync(config, credentials, trigger, state, outcome, started)
        return state
    finally:
        # However it ends (off, skipped, failed, cancelled, even before it
        # held the lock), what this sync owns is no longer pending; another
        # sync's, a waiting toggle's included, stays.
        _clear_pending_work(token)


_telemetry_tasks: set[asyncio.Task[None]] = set()


def _report_sync(
    config: VibeConfigSchema,
    credentials: PlatformCredentialFactory | None,
    trigger: SyncTrigger,
    state: RemotePluginsState,
    outcome: _Outcome,
    started: float,
) -> None:
    """Send ``vibe.remote_plugins.sync.done``; never raises, never waits.

    Counts and codes only: no plugin names, ids or error messages.
    """
    try:
        if not config.enable_telemetry:
            return
        from vibe.core.telemetry.send import TelemetryClient

        telemetry = TelemetryClient(
            config_getter=lambda: config, credentials=credentials
        )
        telemetry.send_remote_plugins_sync_done(
            status=outcome.status,
            error_type=outcome.error_type,
            skip_reason=outcome.skip_reason,
            trigger=trigger,
            listed_count=len(state.plugins),
            enabled_count=sum(1 for plugin in state.plugins if plugin.enabled),
            downloaded_count=outcome.downloaded,
            failed_count=outcome.failed,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        # Its HTTP client is closed once the event is sent.
        task = asyncio.create_task(telemetry.aclose())
        _telemetry_tasks.add(task)
        task.add_done_callback(_telemetry_tasks.discard)
    except Exception:
        logger.debug("Could not report the remote plugins sync", exc_info=True)


async def _sync_locked(
    config: VibeConfigSchema,
    credentials: PlatformCredentialFactory | None,
    *,
    force: bool,
    ttl_s: float | None,
    clock: Clock,
    lock_timeout_s: float | None,
    principal_timeout_s: float | None,
    token: SyncToken,
) -> tuple[RemotePluginsState, _Outcome]:
    async with _sync_turn(), AsyncExitStack() as stack:
        if not await _hold_store_lock(
            stack, _LOCK_TIMEOUT_S if lock_timeout_s is None else lock_timeout_s
        ):
            logger.info("Another process is syncing remote plugins; using its list")
            return current_remote_plugins_state(config, credentials), _Outcome(
                "skipped", skip_reason="lock_busy"
            )
        # Who is signed in is read once the locks are held: waiting on another
        # sync can outlast a sign-out or an account switch.
        endpoint = _endpoint(config, credentials)
        # No endpoint, as the reason a provider would give: a credential that
        # is positively absent is ``missing``; one that could not be read is
        # ``transient``, an account unknown.
        account = (
            await _account(
                endpoint,
                _PRINCIPAL_TIMEOUT_S
                if principal_timeout_s is None
                else principal_timeout_s,
            )
            if isinstance(endpoint, _Endpoint)
            else PlatformCredentialRequired(
                reason="missing" if endpoint == _SIGNED_OUT else "transient"
            )
        )
        return await _sync(
            endpoint,
            account,
            force=force,
            ttl_s=REMOTE_PLUGINS_TTL_S if ttl_s is None else ttl_s,
            now=clock(),
            token=token,
        )


async def _hold_store_lock(stack: AsyncExitStack, timeout: float) -> bool:
    """Hold the cross-process lock until ``stack`` closes; False past ``timeout``."""
    try:
        await stack.enter_async_context(
            async_file_lock(REMOTE_PLUGINS_DIR.path / _LOCK_FILE, timeout=timeout)
        )
    except TimeoutError:
        return False
    return True


# Why no credential can be sent that says nobody is signed in: the last
# account's list and packages are deleted. ``reauthentication_required`` is
# not one: it is a session to renew (and what Desktop answers for any
# failure but a transient one), which says nothing of who signs in next.
_SIGNED_OUT_REASONS = frozenset({"missing"})


async def _account(
    endpoint: _Endpoint, timeout: float
) -> str | PlatformCredentialRequired:
    """Who the credential acts for: the principal the list is stored under.

    The cached principal first, else the one a resolve reports (as
    ``vibe.core.platform_auth.resolve_principal``), within ``timeout``. A credential that names no account is an account
    unknown (``transient``): the readers check the principal without network,
    so a list stored under anything else could never be served.
    """
    if (cached := _current_principal(endpoint)) is not None:
        return cached
    try:
        async with asyncio.timeout(timeout):
            snapshot = await resolve_snapshot(
                endpoint.credentials, f"{endpoint.base_url}{REMOTE_PLUGINS_PATH}"
            )
    except Exception:
        return PlatformCredentialRequired(reason="transient")
    if isinstance(snapshot, PlatformCredentialRequired):
        return snapshot
    if snapshot.principal is None:
        return PlatformCredentialRequired(reason="transient")
    return snapshot.principal


async def _sync(  # noqa: PLR0911 - one return per sync outcome
    endpoint: _Endpoint | Literal["signed_out", "unknown"],
    account: str | PlatformCredentialRequired,
    *,
    force: bool,
    ttl_s: float,
    now: float,
    token: SyncToken,
) -> tuple[RemotePluginsState, _Outcome]:
    state = load_remote_plugins_state()
    if (
        isinstance(account, PlatformCredentialRequired)
        and account.reason in _SIGNED_OUT_REASONS
    ):
        # Signed out: what was synced for the last account must not load for
        # whoever uses the machine next.
        base_url = endpoint.base_url if isinstance(endpoint, _Endpoint) else ""
        return await _drop(state, base_url), _Outcome(
            "skipped", skip_reason="signed_out"
        )
    if isinstance(account, PlatformCredentialRequired) or not isinstance(
        endpoint, _Endpoint
    ):
        state = await _unknown_account(state, endpoint, account, now=now, ttl_s=ttl_s)
        return state, _Outcome("skipped", skip_reason="account_unknown")
    switched = state.principal != account or state.base_url != endpoint.base_url
    if switched:
        # Another account's list, another server's, or one stored while the
        # account was unknown: none of it loads for this account (readers
        # check the principal). Held in memory only: the file keeps it, and
        # its packages, until this account's list is read, so the previous
        # account coming back offline still finds its plugins.
        state = RemotePluginsState(
            base_url=endpoint.base_url,
            principal=account,
            retry_at=_switch_retry_at.get((endpoint.base_url, account), 0.0),
        )
    if (
        not force
        and state.fresh(now, endpoint.base_url, account, ttl_s=ttl_s)
        # A package changed on disk is downloaded again at once, not after
        # the TTL: until then, the plugin is not loaded.
        and await asyncio.to_thread(_all_intact, state)
    ):
        return state, _Outcome("skipped", skip_reason="fresh")
    if not force and now < state.retry_at:
        return state, _Outcome("skipped", skip_reason="backoff")
    read_at = time.monotonic()
    listed = await _read_list(endpoint)
    if _account_changed(endpoint, account):
        # A sign-out or another sign-in while the list was read: it may be the
        # next account's, read with its token, and must not be stored as this
        # one's, nor served. The next sync reads it again.
        return RemotePluginsState(), _Outcome("skipped", skip_reason="account_changed")
    if isinstance(listed, str):
        # After an account switch nothing is written: the file still holds
        # the previous account's list, which must stay for its return.
        if switched:
            _switch_retry_at[endpoint.base_url, account] = now + _RETRY_AFTER_S
        else:
            state = await _failed(state, now, ttl_s)
        _read_errors[endpoint.base_url, account] = listed
        return state, _Outcome("error", error_type=listed)
    _read_errors.pop((endpoint.base_url, account), None)
    # A switch made while the list was read (le-chat may answer with what it
    # had before) is the user's latest word: downloaded for, and stored.
    listed = listed.model_copy(
        update={
            "plugins": _with_choices(
                RemotePluginsState(plugins=listed.plugins), read_at=read_at
            ).plugins
        }
    )
    listed = listed.model_copy(
        update={"plugins": _with_known_icons(listed.plugins, state.plugins)}
    )
    unique, shadowed, errors = _candidates(listed, state)
    previous = {plugin.id: plugin for plugin in state.plugins}
    # Saved at once, before any download: a caller that stops waiting (a
    # session start, the Plugins page) reads this list rather than none. A
    # plugin whose package is not on disk yet is listed and not loaded, as
    # readers load only what is on disk; one being upgraded stays at the
    # version on disk until the new one is unpacked. Stored as never synced:
    # a sync stopped before its downloads end (Desktop quit, a crash) must not
    # leave a list the next one takes as fresh, with packages never fetched.
    pending = await asyncio.to_thread(
        lambda: [_on_disk_or_listed(plugin, previous) for plugin in unique]
    )
    await asyncio.to_thread(
        _store_state,
        _with_choices(
            RemotePluginsState(
                synced_at=0.0,
                base_url=endpoint.base_url,
                principal=account,
                plugins=pending,
                shadowed=shadowed,
                errors=errors,
            ),
            read_at=read_at,
        ),
    )
    # Said once the list is read, owned by this sync. A plugin another sync
    # (a toggle's, waiting) already owns stays that one's.
    _claim_pending_work(await asyncio.to_thread(_work_ahead, unique, previous), token)
    plugins, errors, downloaded, failed = await _download(
        endpoint, unique, errors, previous
    )
    if _account_changed(endpoint, account):
        # The rest was downloaded with the next account's token: the pending
        # list (never synced) stays, so this account's next sync downloads
        # again.
        return RemotePluginsState(), _Outcome("skipped", skip_reason="account_changed")
    # A plugin the list dropped, one turned off, or another version is no
    # longer loaded: prune what no plugin that is on uses. Turning a plugin
    # off removes its copy from this machine (DSK-1191); on again downloads it.
    keep = {(plugin.id, plugin.version) for plugin in plugins if plugin.enabled}
    await asyncio.to_thread(_prune, keep)
    await asyncio.to_thread(_claim_plugin_data, account)
    state = RemotePluginsState(
        synced_at=now,
        base_url=endpoint.base_url,
        principal=account,
        plugins=plugins,
        shadowed=shadowed,
        errors=errors,
    )
    # A switch made during the downloads was stored already; this list must
    # not undo it. Its package comes with the sync that switch started.
    state = _with_choices(state, read_at=read_at)
    await asyncio.to_thread(_store_state, state)
    return state, _Outcome(
        "success",
        skip_reason="unavailable" if listed.unavailable else None,
        downloaded=downloaded,
        failed=failed,
    )


def _account_changed(endpoint: _Endpoint, account: str) -> bool:
    """Whether the credential no longer acts for ``account``, as far as it can tell."""
    return (
        isinstance(endpoint.credentials, CachedPrincipal)
        and _current_principal(endpoint) != account
    )


# When an account whose list was never read may ask le-chat again, after its
# first read failed: kept in memory, as the file still holds the previous
# account's list (see ``_sync``).
_switch_retry_at: dict[tuple[str, str], float] = {}
# Why the last read of an account's list failed (an error code, never a
# message), until a read succeeds: the Plugins page says so rather than
# showing an empty library. In memory: a new process reads again first.
_read_errors: dict[tuple[str, str], str] = {}


def remote_plugins_read_error(
    config: VibeConfigSchema | None,
    credentials: PlatformCredentialFactory | None = None,
) -> str | None:
    """Why the current account's library could not be read last time, if so."""
    if config is None or not config.experimental_enable_remote_plugins:
        return None
    endpoint = _endpoint(config, credentials)
    if not isinstance(endpoint, _Endpoint):
        return None
    principal = _current_principal(endpoint)
    if principal is None:
        return None
    return _read_errors.get((endpoint.base_url, principal))


async def _unknown_account(
    state: RemotePluginsState,
    endpoint: _Endpoint | Literal["signed_out", "unknown"],
    account: str | PlatformCredentialRequired,
    *,
    now: float,
    ttl_s: float,
) -> RemotePluginsState:
    """Who is signed in cannot be told now; the packages stay either way.

    A session to renew stops the list for good: it is stored without an
    account, so it is read again, and its packages reused, at the next
    sign-in. Otherwise (a credential that failed to resolve in time, or could
    not be read), the list stays only when the credential's cached principal
    says it is still this account's, as when offline.
    """
    if (
        isinstance(account, PlatformCredentialRequired)
        and account.reason == "reauthentication_required"
    ):
        return await _drop(state, state.base_url, prune=False)
    if (
        isinstance(endpoint, _Endpoint)
        and state.principal is not None
        and _current_principal(endpoint) == state.principal
        and state.base_url == endpoint.base_url
    ):
        return state if now < state.retry_at else await _failed(state, now, ttl_s)
    return RemotePluginsState()


async def _read_list(endpoint: _Endpoint) -> _ListResponse | str:
    """le-chat's list; else why it could not be read (an error code, never a
    message), and the last list stays.
    """
    try:
        response = await _get(endpoint, "", timeout=_LIST_TIMEOUT_S)
        return _ListResponse.model_validate(response.json())
    except RemotePluginsError as exc:
        if exc.code != UNAVAILABLE_ERROR:
            # Any other refusal, such as a credential that failed once or
            # Vibe Code access turned off for the user (``code_access_*``),
            # keeps the last list.
            logger.info("Remote plugins not refreshed: %s", exc)
            return _error_type(exc)
        # le-chat no longer offers plugins to this user (the rollout flag is
        # off): nothing synced stays loaded.
        logger.info("Remote plugins are not available to this user")
        return _ListResponse(plugins=[], unavailable=True)
    except (ValueError, ValidationError) as exc:
        logger.info("Remote plugins not refreshed: %s", exc)
        return "invalid_response"


def _error_type(exc: RemotePluginsError) -> str:
    """A code for telemetry: le-chat's ``error``, else the HTTP status, else
    the kind of failure. Never the message, which can carry a URL.
    """
    if exc.code is not None and re.fullmatch(r"[a-z0-9_]{1,64}", exc.code):
        return exc.code
    if exc.status is not None:
        return f"http_{exc.status}"
    if isinstance(exc.__cause__, PlatformCredentialUnavailable):
        return "no_credential"
    return "request_failed"


def _with_known_icons(
    plugins: list[RemotePlugin], previous: list[RemotePlugin]
) -> list[RemotePlugin]:
    """Each plugin's icon, kept from the last list when this one has none.

    le-chat reads icons from the packages within a time budget, so a list can
    miss some it gave before; a version's icon does not change.
    """
    known = {
        (plugin.id, plugin.version): plugin.icon
        for plugin in previous
        if plugin.icon is not None
    }
    return [
        plugin.model_copy(update={"icon": known[plugin.id, plugin.version]})
        if plugin.icon is None and (plugin.id, plugin.version) in known
        else plugin
        for plugin in plugins
    ]


def _candidates(
    listed: _ListResponse, state: RemotePluginsState
) -> tuple[list[RemotePlugin], list[RemotePlugin], dict[str, str]]:
    """The plugins to keep, those their name hides, and the errors carried
    over for plugins an incomplete list did not mention.
    """
    candidates = list(listed.plugins)
    errors: dict[str, str] = {}
    if listed.incomplete:
        # le-chat could not read every page: a plugin it did not mention may
        # still be the user's, so it stays as last synced.
        mentioned = {plugin.id for plugin in candidates}
        for plugin in [*state.plugins, *state.shadowed]:
            if plugin.id not in mentioned:
                candidates.append(plugin)
                if plugin.id in state.errors:
                    errors[plugin.id] = state.errors[plugin.id]
    unique, shadowed = _unique_names(candidates)
    return unique, shadowed, errors


def _on_disk_or_listed(
    plugin: RemotePlugin, previous: dict[str, RemotePlugin]
) -> RemotePlugin:
    """As listed when its package is on disk (or it is off); else the version
    on disk while the listed one downloads, if there is one.
    """
    if not plugin.enabled:
        return plugin
    try:
        if _intact_package(_package_dir(plugin.id, plugin.version)) is not None:
            return plugin
    except ValueError:
        return plugin
    return _keep_previous(plugin, previous)


async def _download(
    endpoint: _Endpoint,
    plugins: list[RemotePlugin],
    errors: dict[str, str],
    previous: dict[str, RemotePlugin],
) -> tuple[list[RemotePlugin], dict[str, str], int, int]:
    """Download what is on; the plugins as they now load, the errors the page
    shows, and how many packages were downloaded and failed this time.
    """
    errors = dict(errors)
    kept: list[RemotePlugin] = []
    downloaded = failed = 0
    for plugin in plugins:
        if plugin.enabled:
            try:
                downloaded += await _ensure_downloaded(endpoint, plugin)
            except Exception as exc:
                # One package must not cost the others: whatever it raised
                # (a refused entry, an encrypted or unsupported one, a cut
                # download), it is logged, and reported on the page when the
                # plugin is not in sessions because of it.
                logger.warning(
                    "Could not download remote plugin %r: %s", plugin.name, exc
                )
                failed += 1
                kept_version = await asyncio.to_thread(_keep_previous, plugin, previous)
                if kept_version is plugin:
                    errors[plugin.id] = str(exc) or type(exc).__name__
                else:
                    # The update failed, the version before it still loads:
                    # the page would say "not in sessions", which is wrong.
                    errors.pop(plugin.id, None)
                plugin = kept_version
            else:
                errors.pop(plugin.id, None)
                # On disk, so it loads: the page says so at once. A failure
                # stays pending until its error is stored, with the list.
                # Whichever sync said so: it is downloaded now.
                if remote_plugin_pending(plugin.id) == "downloading":
                    del _pending_work[plugin.id]
        kept.append(plugin)
    return kept, errors, downloaded, failed


def _keep_previous(
    plugin: RemotePlugin, previous: dict[str, RemotePlugin]
) -> RemotePlugin:
    """The version that loaded until now, while a newer one fails to download.

    Kept as it was listed then, name and title included: a version can rename
    the plugin, and the name in the list must stay the one in the package on
    disk. Only the switch is le-chat's latest.
    """
    old = previous.get(plugin.id)
    if old is None or old.version == plugin.version:
        return plugin
    try:
        downloaded = _intact_package(_package_dir(old.id, old.version)) is not None
    except ValueError:
        return plugin
    if not downloaded:
        return plugin
    return old.model_copy(
        update={"enabled": plugin.enabled, "can_toggle": plugin.can_toggle}
    )


async def _drop(
    state: RemotePluginsState,
    base_url: str,
    *,
    principal: str | None = None,
    prune: bool = True,
) -> RemotePluginsState:
    """Forget the list (and, with ``prune``, every package on disk).

    Pruned whatever the list held: another account's list, dropped before this
    one, leaves its packages behind for the next list to reuse or prune.
    """
    dropped = RemotePluginsState(base_url=base_url, principal=principal)
    if state != dropped:
        await asyncio.to_thread(_store_state, dropped)
    if prune:
        await asyncio.to_thread(_prune, set())
        await asyncio.to_thread(_clear_plugin_data)
    return dropped


def _clear_plugin_data() -> None:
    """Delete what library plugins wrote: nobody's account owns it any more."""
    shutil.rmtree(REMOTE_PLUGIN_DATA_DIR.path, ignore_errors=True)


def _claim_plugin_data(account: str) -> None:
    """Keep the plugins' data for ``account``; another account's is deleted.

    A plugin's data is keyed by its name, which another account's plugin can
    share: it must not read what the previous account's wrote.
    """
    root = REMOTE_PLUGIN_DATA_DIR.path
    owner_file = root / _DATA_OWNER_FILE
    try:
        owner = owner_file.read_text(encoding="utf-8")
    except OSError:
        owner = None
    if owner == account:
        return
    if root.exists():
        _clear_plugin_data()
    root.mkdir(parents=True, exist_ok=True)
    owner_file.write_text(account, encoding="utf-8")


async def _failed(
    state: RemotePluginsState, now: float, ttl_s: float
) -> RemotePluginsState:
    """Keep the last list; the next refresh waits a delay doubling per failure."""
    failures = state.failures + 1
    delay = min(_RETRY_AFTER_S * 2 ** (failures - 1), ttl_s)
    state = state.model_copy(update={"failures": failures, "retry_at": now + delay})
    await asyncio.to_thread(_store_state, state)
    return state


async def set_remote_plugin_enabled(
    config: VibeConfigSchema,
    plugin_id: str,
    enabled: bool,
    credentials: PlatformCredentialFactory | None = None,
) -> RemotePluginsState:
    """Write the choice to le-chat, then resync so the package is (un)loaded."""
    if not config.experimental_enable_remote_plugins:
        raise RemotePluginsError("remote plugins are off")
    endpoint = _endpoint(config, credentials)
    if not isinstance(endpoint, _Endpoint):
        raise RemotePluginsError("no Mistral credential")
    url = f"{endpoint.base_url}{REMOTE_PLUGINS_PATH}/{plugin_id}/enabled"
    try:
        response = await credential_request(
            endpoint.credentials,
            "PUT",
            url,
            timeout=_LIST_TIMEOUT_S,
            json={"enabled": enabled},
        )
    except PlatformCredentialUnavailable as exc:
        raise RemotePluginsError(
            f"no platform credential ({exc.required.reason})"
        ) from exc
    except httpx2.RequestError as exc:
        raise RemotePluginsError(f"request failed: {exc}") from exc
    if not response.is_success:
        raise RemotePluginsError(_error_message(response), status=response.status_code)
    # le-chat took it: the list says so at once, whatever the sync below does
    # in the time it is given (a download can take longer, and finishes in the
    # background).
    await asyncio.to_thread(_apply_choice, plugin_id, enabled)
    # Said at once, owned by the sync this starts: it stays until that sync
    # ends, whatever sync runs before it.
    token = SyncToken()
    work = await asyncio.to_thread(_choice_work, plugin_id, enabled)
    if work is not None:
        _pending_work[plugin_id] = (work, token)
    else:
        _pending_work.pop(plugin_id, None)
    return await refresh_remote_plugins(
        config, credentials, force=True, trigger="toggle", token=token
    )


# Choices le-chat accepted, by plugin id: the switch and when it was made
# (``time.monotonic``). A sync whose list was read before a choice, and so
# may not have it, stores the choice over what it read.
_choices: dict[str, tuple[bool, float]] = {}
# Longer than any one sync takes (its list read and downloads).
_CHOICE_KEEP_S = 30 * 60.0


def _choice_work(plugin_id: str, enabled: bool) -> PendingWork | None:
    """What turning the plugin on or off leaves a sync to do to its package."""
    state = load_remote_plugins_state()
    plugin = next((p for p in state.plugins if p.id == plugin_id), None)
    if plugin is None:
        return None
    return _work_ahead([plugin.model_copy(update={"enabled": enabled})], {}).get(
        plugin_id
    )


def _apply_choice(plugin_id: str, enabled: bool) -> None:
    """Store the switch le-chat just accepted into the last list.

    Not under the sync locks: a sync downloading for minutes holds them, and
    the switch must show now. A sync of this process stores the choice over
    the list it read before it (``_with_choices``); one of another process
    might not, until its next sync reads le-chat again.
    """
    now = time.monotonic()
    # Only choices a sync still in flight may have missed are worth keeping.
    for stale in [
        key for key, (_, made_at) in _choices.items() if now - made_at > _CHOICE_KEEP_S
    ]:
        del _choices[stale]
    _choices[plugin_id] = (enabled, now)
    with _state_write_lock:
        state = load_remote_plugins_state()
        if not any(plugin.id == plugin_id for plugin in state.plugins):
            return
        # Its package comes (on) or goes (off) with the next sync that reads
        # le-chat: one that fails now must not leave the list fresh, the
        # plugin on but not downloaded, or its copy on disk, until the TTL.
        state = _with_choices(state, read_at=now).model_copy(update={"synced_at": 0.0})
        _write_state(state)


def _with_choices(state: RemotePluginsState, *, read_at: float) -> RemotePluginsState:
    """``state`` with every choice made after ``read_at`` applied."""
    newer = {
        plugin_id: enabled
        for plugin_id, (enabled, made_at) in _choices.items()
        if made_at >= read_at
    }
    if not newer:
        return state
    return state.model_copy(
        update={
            "plugins": [
                plugin.model_copy(update={"enabled": newer[plugin.id]})
                if plugin.id in newer and plugin.can_toggle
                else plugin
                for plugin in state.plugins
            ]
        }
    )


def _unique_names(
    plugins: list[RemotePlugin],
) -> tuple[list[RemotePlugin], list[RemotePlugin]]:
    """One plugin per name: a name is what loads, and what the page toggles.

    AI Registry can list a private and a workspace plugin of one name. As in a
    Work turn, one that is on wins over one that is off, then the user's own;
    the others are reported, not loaded.
    """
    kept: dict[str, RemotePlugin] = {}
    for plugin in sorted(plugins, key=lambda p: (not p.enabled, not p.owned_by_user)):
        kept.setdefault(plugin.name, plugin)
    winners = {plugin.id for plugin in kept.values()}
    return (
        [plugin for plugin in plugins if plugin.id in winners],
        [plugin for plugin in plugins if plugin.id not in winners],
    )


def _error_code(response: httpx2.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _error_message(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"unexpected status {response.status_code}"
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if isinstance(message, str):
            return message
    return f"unexpected status {response.status_code}"


async def _ensure_downloaded(endpoint: _Endpoint, plugin: RemotePlugin) -> bool:
    """Download and unpack the package unless already there as unpacked;
    whether it was downloaded.
    """
    target = _package_dir(plugin.id, plugin.version)
    if await asyncio.to_thread(_intact_package, target) is not None:
        return False
    archive = await _get_package(
        endpoint, f"/{plugin.id}/versions/{plugin.version}/content"
    )
    await asyncio.to_thread(_unpack, archive, target)
    return True


async def _get_package(endpoint: _Endpoint, path: str) -> bytes:
    """A package's bytes, streamed and refused past ``_MAX_DOWNLOAD_BYTES``.

    As ``credential_request`` sends it (the credential for its URL, no
    redirect for a user token, a 401 reported), but never buffered whole
    before its size is known.
    """
    url = f"{endpoint.base_url}{REMOTE_PLUGINS_PATH}{path}"
    target = platform_target(url)
    snapshot = await resolve_snapshot(endpoint.credentials, url)
    if isinstance(snapshot, PlatformCredentialRequired) or target is None:
        reason = (
            snapshot.reason
            if isinstance(snapshot, PlatformCredentialRequired)
            else "invalid"
        )
        raise RemotePluginsError(f"no platform credential ({reason})")
    try:
        async with (
            VibeAsyncHTTPClient(verify=build_ssl_context()) as client,
            client.stream(
                "GET",
                url,
                headers=dict(snapshot.headers),
                follow_redirects=snapshot.follows_redirects,
                timeout=_DOWNLOAD_TIMEOUT_S,
            ) as response,
        ):
            if response.status_code == httpx2.codes.UNAUTHORIZED:
                await endpoint.credentials.reject(
                    target, observed=snapshot.identity, reason="http_unauthorized"
                )
            if not response.is_success:
                await response.aread()
                raise RemotePluginsError(
                    f"unexpected status {response.status_code}",
                    status=response.status_code,
                    code=_error_code(response),
                )
            return await _read_capped(response)
    except httpx2.RequestError as exc:
        raise RemotePluginsError(f"request failed: {exc}") from exc


async def _read_capped(response: httpx2.Response) -> bytes:
    too_large = RemotePluginsError(
        f"package larger than {_MAX_DOWNLOAD_BYTES} bytes", code="package_too_large"
    )
    declared = response.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > _MAX_DOWNLOAD_BYTES
    ):
        raise too_large
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > _MAX_DOWNLOAD_BYTES:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_parts(name: str) -> tuple[str, ...] | None:
    """The entry's path parts, or None when it could land outside the package.

    Judged the same on every OS: a part Windows reads as a drive (``C:``) or a
    stream (``x:y``), an absolute or anchored path under either flavour, a
    backslash, or ``.``/``..`` would make ``Path.joinpath`` leave the staging
    folder somewhere.
    """
    if "\\" in name or ":" in name:
        return None
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if posix.is_absolute() or windows.anchor or windows.drive:
        return None
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _unpack(archive: bytes, target: Path) -> None:
    """Unpack into a temporary folder, then move it in whole.

    Refuses an entry that would land outside the package, a link, or a package
    larger than ``_MAX_UNPACKED_BYTES`` once unpacked.
    """
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            total = 0
            for info in zipped.infolist():
                total += info.file_size
                if total > _MAX_UNPACKED_BYTES:
                    raise RemotePluginsError("package too large once unpacked")
                parts = _safe_parts(info.filename)
                if parts is None:
                    raise RemotePluginsError(
                        f"unsafe path in package: {info.filename!r}"
                    )
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RemotePluginsError(f"link in package: {info.filename!r}")
                dest = staging.joinpath(*parts)
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(info) as source, dest.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                if mode & 0o111:
                    dest.chmod(0o755)
        package = _package_root(staging)
        if package is None:
            raise RemotePluginsError("package has no single top folder")
        (staging / _DIGEST_FILE).write_text(_digest(package), encoding="utf-8")
        # The store lock keeps any other sync out, and ``_ensure_downloaded``
        # skipped a version already unpacked as it was: what is left here is a
        # broken unpack or a changed package, replaced.
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _prune(keep: set[tuple[str, int]]) -> None:
    """Delete every version not in ``keep``, and staging folders left behind.

    Only called with the store lock held, so no unpack is in progress: a
    ``.<version>.<uuid>`` folder is one a process killed mid-unpack left.
    """
    store = REMOTE_PLUGINS_DIR.path / "store"
    try:
        plugin_dirs = list(store.iterdir())
    except OSError:
        return
    for plugin_dir in plugin_dirs:
        try:
            versions = list(plugin_dir.iterdir())
        except OSError:
            continue
        for version_dir in versions:
            if version_dir.name.startswith("."):
                shutil.rmtree(version_dir, ignore_errors=True)
                continue
            try:
                version = int(version_dir.name)
            except ValueError:
                continue
            if (plugin_dir.name, version) not in keep:
                shutil.rmtree(version_dir, ignore_errors=True)
        with suppress(OSError):
            if not any(plugin_dir.iterdir()):
                plugin_dir.rmdir()


def remote_plugin_for_root(
    root: Path, state: RemotePluginsState
) -> RemotePlugin | None:
    """The listed plugin a resolved package folder belongs to."""
    resolved = root.resolve()
    for plugin in state.plugins:
        try:
            version_dir = _package_dir(plugin.id, plugin.version).resolve()
        except ValueError:
            continue
        if resolved.parent == version_dir:
            return plugin
    return None
