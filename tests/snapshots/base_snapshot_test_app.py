from __future__ import annotations

from pathlib import Path

from rich.style import Style
from textual.pilot import Pilot
from textual.widgets.text_area import TextAreaTheme

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    get_base_config,
)
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_account_gateway import FakeAccountGateway
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server._account import WhoAmIResult
from vibe.app_server.models import AccountPlanKind
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input import ChatTextArea
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema


async def wait_for_agent_switch(pilot: Pilot, agent_name: str) -> None:
    app = pilot.app
    for _ in range(100):
        if (
            isinstance(app, VibeApp)
            and not app._agent_switch_active
            and app.app_server.resources.agents.active.name == agent_name
        ):
            return
        await pilot.pause(0.01)
    raise AssertionError(f"Agent switch to {agent_name!r} did not complete")


def default_config(**kwargs) -> VibeConfigSchema:
    """Default configuration for snapshot testing.
    Remove as much interference as possible from the snapshot comparison, in order to get a clean pixel-to-pixel comparison.
    - Injects a fake backend to prevent (or stub) LLM calls.
    - Disables the banner animation.
    - Forces a value for the displayed workdir
    - Hides the chat input cursor (as the blinking animation is not deterministic).
    - Shows thinking nodes by default; pass show_thinking_nodes=False to hide them.
    - Pins the model set to the shared on-disk test seed so the banner renders a
      stable single model regardless of the schema's evolving built-in defaults.
    """
    seed = get_base_config()
    kwargs.setdefault("active_model", seed["active_model"])
    kwargs.setdefault("models", [ModelConfig.model_validate(m) for m in seed["models"]])
    kwargs.setdefault(
        "providers", [ProviderConfig.model_validate(p) for p in seed["providers"]]
    )
    kwargs.setdefault("show_thinking_nodes", True)
    return build_test_vibe_config(
        disable_welcome_banner_animation=True,
        displayed_workdir="/test/workdir",
        **kwargs,
    )


class BaseSnapshotTestApp(VibeApp):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"
    _current_agent_name: str = BuiltinAgentName.DEFAULT

    def __init__(
        self,
        config: VibeConfigSchema | None = None,
        backend: FakeBackend | None = None,
        agent_loop: AgentLoop | None = None,
        **kwargs,
    ):
        agent_loop_kwargs: dict = {}
        if "mcp_registry" in kwargs:
            agent_loop_kwargs["mcp_registry"] = kwargs.pop("mcp_registry")

        resolved_agent_loop = agent_loop or build_test_agent_loop(
            config=config or default_config(),
            agent_name=self._current_agent_name,
            enable_streaming=bool(kwargs.get("enable_streaming", False)),
            backend=backend or FakeBackend(),
            **agent_loop_kwargs,
        )

        account_gateway = kwargs.pop(
            "account_gateway",
            FakeAccountGateway(
                WhoAmIResult(
                    plan_type=AccountPlanKind.CHAT,
                    plan_name="INDIVIDUAL",
                    prompt_switching_to_pro_plan=False,
                )
            ),
        )

        super().__init__(
            history_file=kwargs.pop("history_file", Path(".vibehistory")),
            app_server=lambda: create_test_app_server_session(
                resolved_agent_loop, account_gateway=account_gateway
            ),
            **kwargs,
        )

    async def on_ready(self):
        # on_ready is called once all the on_mount in the MRO chain have been called
        # https://textual.textualize.io/api/events/#textual.events.Ready
        self._hide_chat_input_cursor()

    def _hide_chat_input_cursor(self) -> None:
        text_area = self.query_one(ChatTextArea)
        hidden_cursor_theme = TextAreaTheme(name="hidden_cursor", cursor_style=Style())
        text_area.register_theme(hidden_cursor_theme)
        text_area.theme = "hidden_cursor"
