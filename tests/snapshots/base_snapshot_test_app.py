from __future__ import annotations

from rich.style import Style
from textual.widgets.text_area import TextAreaTheme

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    get_base_config,
)
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input import ChatTextArea
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema


def default_config(**kwargs) -> VibeConfigSchema:
    """Default configuration for snapshot testing.
    Remove as much interference as possible from the snapshot comparison, in order to get a clean pixel-to-pixel comparison.
    - Injects a fake backend to prevent (or stub) LLM calls.
    - Disables the banner animation.
    - Forces a value for the displayed workdir
    - Hides the chat input cursor (as the blinking animation is not deterministic).
    - Pins the model set to the shared on-disk test seed so the banner renders a
      stable single model regardless of the schema's evolving built-in defaults.
    """
    seed = get_base_config()
    kwargs.setdefault("active_model", seed["active_model"])
    kwargs.setdefault("models", [ModelConfig.model_validate(m) for m in seed["models"]])
    kwargs.setdefault(
        "providers", [ProviderConfig.model_validate(p) for p in seed["providers"]]
    )
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
        **kwargs,
    ):
        agent_loop_kwargs: dict = {}
        if "mcp_registry" in kwargs:
            agent_loop_kwargs["mcp_registry"] = kwargs.pop("mcp_registry")

        agent_loop = build_test_agent_loop(
            config=config or default_config(),
            agent_name=self._current_agent_name,
            enable_streaming=bool(kwargs.get("enable_streaming", False)),
            backend=backend or FakeBackend(),
            **agent_loop_kwargs,
        )

        plan_offer_gateway = kwargs.pop(
            "plan_offer_gateway",
            FakeWhoAmIGateway(
                WhoAmIResponse(
                    plan_type=WhoAmIPlanType.CHAT,
                    plan_name="INDIVIDUAL",
                    prompt_switching_to_pro_plan=False,
                )
            ),
        )

        super().__init__(
            agent_loop=agent_loop, plan_offer_gateway=plan_offer_gateway, **kwargs
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
