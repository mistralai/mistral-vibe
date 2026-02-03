from __future__ import annotations

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import PlanOfferMessage
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfig


def _make_app(gateway: FakeWhoAmIGateway, config: VibeConfig | None = None) -> VibeApp:
    config = config or VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )
    agent_loop = AgentLoop(config=config, backend=FakeBackend())
    return VibeApp(agent_loop=agent_loop, plan_offer_gateway=gateway)


@pytest.mark.asyncio
async def test_app_shows_upgrade_offer_in_plan_offer_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            is_pro_plan=False,
            advertise_pro_plan=True,
            prompt_switching_to_pro_plan=False,
        )
    )
    app = _make_app(gateway)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        offer = app.query_one(PlanOfferMessage)
        assert "Upgrade to" in offer.get_text()
        assert "Pro" in offer.get_text()
        assert gateway.calls == ["api-key"]


@pytest.mark.asyncio
async def test_app_shows_switch_to_pro_key_offer_in_plan_offer_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            is_pro_plan=False,
            advertise_pro_plan=False,
            prompt_switching_to_pro_plan=True,
        )
    )
    app = _make_app(gateway)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        offer = app.query_one(PlanOfferMessage)
        assert "Switch to your" in offer.get_text()
        assert "Pro API key" in offer.get_text()
        assert gateway.calls == ["api-key"]
