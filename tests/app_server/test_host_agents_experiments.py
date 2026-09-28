"""The passive Host's ``agents/list`` must reflect GrowthBook experiment
variants, exactly like a session build does.

Regression: ``_load_orchestrator`` built a plain orchestrator without applying
the cached experiment variants, so rollout flags such as
``smart_approve_available`` were absent and smart-approve was hidden from the
desktop mode picker even though the session path offered it.
"""

from __future__ import annotations

from typing import cast

import pytest

from vibe.app_server._host import HostRequestHandler
import vibe.app_server._runtime as runtime_module
from vibe.app_server.protocol import AgentsListParams, AgentsListResponse
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.models import EvalResponse


def _smart_approve_available_response() -> EvalResponse:
    # A rollout served purely via defaultValue (no force rules), as GrowthBook
    # returns it for smart approve.
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SMART_APPROVE.value: {"defaultValue": True, "rules": []}
        }
    })


@pytest.mark.asyncio
async def test_agents_list_offers_smart_approve_from_cached_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "load_cached_eval_response",
        lambda _config: _smart_approve_available_response(),
    )
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "agents/list", AgentsListParams().model_dump(mode="json", by_alias=True)
    )

    response = cast(AgentsListResponse, result.response)
    names = [agent.name for agent in response.agents]
    assert "smart-approve" in names
