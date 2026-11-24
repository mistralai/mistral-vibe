from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.stubs.background_worker import FakeBackgroundWorker
from tests.stubs.process_services import (
    FakeModelCatalogServer,
    FakeOrgConfigServer,
    fake_process_services,
)
from vibe.app_server._process_services import ProcessServices


@pytest.fixture(autouse=True)
def _direct_path_tests_turn_the_session_api_off(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn the Session API switch off for the tests written for the direct path."""
    if request.path.name == "test_unified_harness_backend_direct.py":
        monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "false")


# Asserts what the skill store cleanup left behind through a session, so it
# keeps the worker process.
_KEEPS_THE_WORKER_PROCESS = frozenset({
    "test_a_session_s_sync_clears_the_store_of_versions_nobody_needs"
})


def _runs_background_jobs_in_process(request: pytest.FixtureRequest) -> bool:
    if request.node.name in _KEEPS_THE_WORKER_PROCESS:
        return False
    return (
        "backend_contract" in request.path.parts
        or request.path.name == "test_unified_harness_backend_adapter.py"
    )


@pytest_asyncio.fixture
async def process_services(
    request: pytest.FixtureRequest,
    org_server: FakeOrgConfigServer,
    model_catalog_server: FakeModelCatalogServer,
) -> AsyncIterator[ProcessServices]:
    """The shared fixture's services; the session tests run background jobs here.

    Every session those tests open starts the registry skill store cleanup, so
    a worker process would start for each test, and closing the services would
    wait for it to finish starting (≈0.7 s a test). They test the app server,
    not the worker: ``test_background_worker.py``, the skill store cleanup
    tests and the tests asserting ``process_services.background.pid`` cover
    the worker process.
    """
    services = fake_process_services(
        org_server,
        model_catalog_server=model_catalog_server,
        background=(
            FakeBackgroundWorker()
            if _runs_background_jobs_in_process(request)
            else None
        ),
    )
    yield services
    await services.close()
