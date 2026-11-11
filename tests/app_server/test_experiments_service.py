from __future__ import annotations

import asyncio
from pathlib import Path
import time
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._experiments_service import (
    ExperimentsEvalFailedError,
    ExperimentsKey,
    ExperimentsService,
)
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.identity import IdentityResult
from vibe.core.telemetry.types import LaunchContext
from vibe.core.utils.remote_cache import RefreshPolicy
from vibe.setup.auth.whoami import WhoAmICache

_RESPONSE = EvalResponse.model_validate({
    "features": {"vibe_cli_smart_approve": {"defaultValue": True}}
})


class _Client(RemoteEvalClient):
    def __init__(self, answers: list[EvalResponse | None]) -> None:
        super().__init__(url="https://growthbook.test/api/eval/key")
        self._answers = answers
        self.calls: list[ExperimentAttributes] = []
        self.release: asyncio.Event | None = None

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        self.calls.append(attributes)
        if self.release is not None:
            await self.release.wait()
        return self._answers[min(len(self.calls), len(self._answers)) - 1]


def _launch(client_name: str = "vibe-desktop") -> LaunchContext:
    return LaunchContext(
        agent_entrypoint="cli",
        agent_version="0",
        client_name=client_name,
        client_version="0",
    )


async def _key(client_name: str = "vibe-desktop") -> ExperimentsKey:
    key = await ExperimentsKey.of(
        build_test_vibe_config(enable_telemetry=True), _launch(client_name)
    )
    assert key is not None
    return key


@pytest.fixture(autouse=True)
def _identity_and_whoami(
    monkeypatch: pytest.MonkeyPatch,
) -> list[IdentityResult | None]:
    """What ``/users/me`` answers next; ``/whoami`` answers nothing."""
    answers: list[IdentityResult | None] = [
        IdentityResult(id="user-1", organization=None, workspace=None)
    ]

    async def identity(*_args: object, **_kwargs: object) -> IdentityResult | None:
        return answers[0]

    monkeypatch.setattr("vibe.core.identity_cache.fetch_identity", identity)
    monkeypatch.setattr(WhoAmICache, "resolve", AsyncMock(return_value=None))
    return answers


def _service(client: _Client) -> ExperimentsService:
    return ExperimentsService(client_factory=lambda _url: client)


@pytest.mark.asyncio
async def test_sessions_of_one_account_and_client_share_one_eval(
    config_dir: Path,
) -> None:
    client = _Client([_RESPONSE])
    client.release = asyncio.Event()
    service = _service(client)

    pending = [asyncio.create_task(service.evaluate(await _key())) for _ in range(3)]
    await asyncio.sleep(0)
    client.release.set()
    held = await asyncio.gather(*pending)

    assert len(client.calls) == 1
    assert all(h.response == _RESPONSE for h in held)
    assert held[0].attributes is not None
    assert held[0].attributes.userId == "user-1"
    # Later sessions are built from the disk copy, as before.
    assert load_cached_eval_response((await _key()).config) == _RESPONSE
    await service.close()


@pytest.mark.asyncio
async def test_another_client_gets_its_own_eval(config_dir: Path) -> None:
    client = _Client([_RESPONSE])
    service = _service(client)

    await service.evaluate(await _key("vibe-desktop"))
    await service.evaluate(await _key("vibe-cli"))
    await service.evaluate(await _key("vibe-desktop"))

    assert [call.client_name for call in client.calls] == ["vibe-desktop", "vibe-cli"]
    await service.close()


@pytest.mark.asyncio
async def test_a_failed_eval_backs_off_and_stops_later_waits(config_dir: Path) -> None:
    client = _Client([None, _RESPONSE])
    service = _service(client)

    with pytest.raises(ExperimentsEvalFailedError) as first:
        await service.evaluate(await _key())
    with pytest.raises(ExperimentsEvalFailedError):
        await service.evaluate(await _key())

    # One request: the second open waits out the backoff, not the network.
    assert len(client.calls) == 1
    assert service.failures.failed
    # Telemetry still segments a failed eval by what was resolved for it.
    assert first.value.attributes is not None
    assert first.value.attributes.userId == "user-1"
    await service.close()


@pytest.mark.asyncio
async def test_an_eval_without_telemetry_is_not_sent(config_dir: Path) -> None:
    client = _Client([_RESPONSE])
    service = _service(client)
    key = await ExperimentsKey.of(
        build_test_vibe_config(enable_telemetry=False), _launch()
    )
    assert key is not None

    with pytest.raises(ExperimentsEvalFailedError):
        await service.evaluate(key)

    assert client.calls == []
    await service.close()


@pytest.mark.asyncio
async def test_an_eval_without_a_user_is_not_held_for_later_sessions(
    config_dir: Path, _identity_and_whoami: list[IdentityResult | None]
) -> None:
    # /users/me failed: the eval assigns nothing, so it must not stand for the
    # account until the next refresh, nor overwrite a good copy on disk.
    _identity_and_whoami[0] = None
    client = _Client([_RESPONSE])
    clock = [1_000_000.0]
    service = ExperimentsService(
        client_factory=lambda _url: client, policy=_POLICY, clock=lambda: clock[0]
    )

    with pytest.raises(ExperimentsEvalFailedError):
        await service.evaluate(await _key())
    assert load_cached_eval_response((await _key()).config) is None

    _identity_and_whoami[0] = IdentityResult(
        id="user-1", organization=None, workspace=None
    )
    clock[0] += _POLICY.retry_after_s
    held = await service.evaluate(await _key())

    assert len(client.calls) == 2
    assert held.attributes is not None
    assert held.attributes.userId == "user-1"
    await service.close()


@pytest.mark.asyncio
async def test_an_eval_for_an_account_signed_out_during_its_fetch_is_not_held(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CLI OIDC sign-in during the fetch makes its answer another account's:
    # sessions of the key it started under must not be served it.
    client = _Client([_RESPONSE])
    client.release = asyncio.Event()
    service = _service(client)
    key = await _key()
    signed_in = [True]
    monkeypatch.setattr(
        "vibe.app_server._experiments_service.credential_cache_key_is_current",
        lambda *_args: signed_in[0],
    )

    pending = asyncio.create_task(service.evaluate(key))
    await asyncio.sleep(0)
    signed_in[0] = False
    client.release.set()

    with pytest.raises(ExperimentsEvalFailedError):
        await pending
    signed_in[0] = True
    assert service._cache.cached(key) is None
    await service.close()


@pytest.mark.asyncio
async def test_the_key_keeps_no_session_config(config_dir: Path) -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    key = await ExperimentsKey.of(config, _launch())
    assert key is not None

    assert key.config is not config


_POLICY = RefreshPolicy(
    refresh_after_s=600,
    max_age_s=7 * 24 * 3600,
    retry_after_s=30,
    max_retry_after_s=600,
    fetch_timeout_s=5,
)


class _SlowStore:
    """A disk store whose reads take a while and find nothing."""

    def load(self, _key: ExperimentsKey) -> None:
        time.sleep(0.4)

    def save(self, _key: ExperimentsKey, _entry: object) -> None:
        pass


@pytest.mark.asyncio
async def test_a_fetch_that_lands_within_its_own_timeout_is_not_given_up_on(
    config_dir: Path,
) -> None:
    # The fetch reads the disk copy before its own timeout starts, so a join
    # bounded by that same timeout gave up on a fetch about to succeed.
    client = _Client([_RESPONSE])

    async def answer_late(attributes: ExperimentAttributes) -> EvalResponse | None:
        client.calls.append(attributes)
        await asyncio.sleep(0.3)
        return _RESPONSE

    client.evaluate = answer_late  # type: ignore[method-assign]
    service = ExperimentsService(
        client_factory=lambda _url: client,
        policy=RefreshPolicy(
            refresh_after_s=600,
            max_age_s=3600,
            retry_after_s=30,
            max_retry_after_s=600,
            fetch_timeout_s=0.5,
        ),
        store=_SlowStore(),  # type: ignore[arg-type]
    )

    held = await service.evaluate(await _key())

    assert held.response == _RESPONSE
    assert not service.failures.failed
    await service.close()


@pytest.mark.asyncio
async def test_a_fresh_process_serves_the_eval_from_disk_without_a_request(
    config_dir: Path,
) -> None:
    # The app server restarts all the time (Desktop relaunch, a crash, an
    # update): the eval an earlier process fetched answers at once.
    earlier = _service(_Client([_RESPONSE]))
    fetched = await earlier.evaluate(await _key())
    await earlier.close()
    client = _Client([None])
    fresh = _service(client)

    held = await fresh.evaluate(await _key())

    assert client.calls == []
    assert held == fetched
    assert held.attributes is not None
    assert held.attributes.userId == "user-1"
    await fresh.close()


@pytest.mark.asyncio
async def test_an_old_eval_on_disk_is_served_and_refreshed_in_the_background(
    config_dir: Path,
) -> None:
    # The copy on disk ages by the wall clock, as across real processes.
    clock = [time.time()]
    earlier = ExperimentsService(
        client_factory=lambda _url: _Client([_RESPONSE]),
        policy=_POLICY,
        clock=lambda: clock[0],
    )
    await earlier.evaluate(await _key())
    await earlier.close()
    clock[0] += _POLICY.refresh_after_s + 1
    refreshed = EvalResponse.model_validate({
        "features": {"vibe_cli_smart_approve": {"defaultValue": False}}
    })
    client = _Client([refreshed])
    client.release = asyncio.Event()
    fresh = ExperimentsService(
        client_factory=lambda _url: client, policy=_POLICY, clock=lambda: clock[0]
    )

    served = await asyncio.wait_for(fresh.evaluate(await _key()), timeout=1)

    assert served.response == _RESPONSE
    client.release.set()
    for _ in range(50):
        if client.calls and (await fresh.evaluate(await _key())).response == refreshed:
            break
        await asyncio.sleep(0.01)
    assert (await fresh.evaluate(await _key())).response == refreshed
    await fresh.close()


@pytest.mark.asyncio
async def test_a_failed_refresh_behind_a_held_eval_keeps_later_sessions_waiting(
    config_dir: Path,
) -> None:
    # The held eval still serves every session: a failed refresh is no reason
    # for an unpinned session to skip waiting for its own.
    clock = [time.time()]
    client = _Client([_RESPONSE, None])
    service = ExperimentsService(
        client_factory=lambda _url: client, policy=_POLICY, clock=lambda: clock[0]
    )
    await service.evaluate(await _key())
    clock[0] += _POLICY.refresh_after_s + 1

    served = await service.evaluate(await _key())
    for _ in range(50):
        if len(client.calls) == 2:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)

    assert served.response == _RESPONSE
    assert len(client.calls) == 2
    assert not service.failures.failed
    await service.close()


@pytest.mark.asyncio
async def test_another_client_does_not_read_this_clients_eval_from_disk(
    config_dir: Path,
) -> None:
    earlier = _service(_Client([_RESPONSE]))
    await earlier.evaluate(await _key("vibe-desktop"))
    await earlier.close()
    client = _Client([_RESPONSE])
    fresh = _service(client)

    await fresh.evaluate(await _key("vibe-cli"))

    assert [call.client_name for call in client.calls] == ["vibe-cli"]
    await fresh.close()


@pytest.mark.asyncio
async def test_opting_out_drops_the_shared_eval_from_disk(config_dir: Path) -> None:
    from vibe.core.experiments.cache import clear_cached_eval_responses

    earlier = _service(_Client([_RESPONSE]))
    await earlier.evaluate(await _key())
    await earlier.close()

    clear_cached_eval_responses()

    client = _Client([_RESPONSE])
    fresh = _service(client)
    await fresh.evaluate(await _key())
    assert len(client.calls) == 1
    await fresh.close()
