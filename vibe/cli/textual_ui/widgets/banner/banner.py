from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static

from vibe import __version__
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import VibeConfig
from vibe.core.skills.manager import SkillManager
from vibe.core.tools.mcp.registry import MCPRegistry


def _pluralize(count: int, singular: str) -> str:
    return f"{count} {singular}{'s' if count != 1 else ''}"


@dataclass
class BannerState:
    active_model: str = ""
    models_count: int = 0
    mcp_servers_count: int = 0
    connectors_count: int = 0
    skills_count: int = 0
    plan_description: str | None = None


class Banner(Static):
    state = reactive(BannerState(), init=False)

    def __init__(
        self,
        config: VibeConfig,
        skill_manager: SkillManager,
        mcp_registry: MCPRegistry,
        connectors_count: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.can_focus = False
        self._initial_state = self._build_state(
            config=config,
            skill_manager=skill_manager,
            mcp_registry=mcp_registry,
            connectors_count=connectors_count,
            plan_description=None,
        )
        self._animated = not config.disable_welcome_banner_animation

    def compose(self) -> ComposeResult:
        with Horizontal(id="banner-container"):
            yield PetitChat(animate=self._animated)

            with Vertical(id="banner-info"):
                with Horizontal(classes="banner-line"):
                    yield NoMarkupStatic("Mistral Vibe", id="banner-brand")
                    yield NoMarkupStatic(" ", classes="banner-spacer")
                    yield NoMarkupStatic(f"v{__version__} · ", classes="banner-meta")
                    yield NoMarkupStatic("", id="banner-model")
                    yield NoMarkupStatic("", id="banner-user-plan")
                with Horizontal(classes="banner-line"):
                    yield NoMarkupStatic("", id="banner-meta-counts")
                with Horizontal(classes="banner-line"):
                    yield NoMarkupStatic("Type ", classes="banner-meta")
                    yield NoMarkupStatic("/help", classes="banner-cmd")
                    yield NoMarkupStatic(" for more information", classes="banner-meta")

    def on_mount(self) -> None:
        self.state = self._initial_state

    def watch_state(self) -> None:
        if not self.is_attached:
            return
        self.query_one("#banner-model", NoMarkupStatic).update(self.state.active_model)
        self.query_one("#banner-meta-counts", NoMarkupStatic).update(
            self._format_meta_counts()
        )
        self.query_one("#banner-user-plan", NoMarkupStatic).update(self._format_plan())

    def freeze_animation(self) -> None:
        if self._animated:
            self.query_one(PetitChat).freeze_animation()

    def set_state(
        self,
        config: VibeConfig,
        skill_manager: SkillManager,
        mcp_registry: MCPRegistry,
        connectors_count: int = 0,
        plan_description: str | None = None,
    ) -> None:
        self.state = self._build_state(
            config, skill_manager, mcp_registry, connectors_count, plan_description
        )

    @staticmethod
    def _build_state(
        config: VibeConfig,
        skill_manager: SkillManager,
        mcp_registry: MCPRegistry,
        connectors_count: int = 0,
        plan_description: str | None = None,
    ) -> BannerState:
        enabled_servers = [s for s in config.mcp_servers if not s.disabled]
        mcp_count = mcp_registry.count_loaded(enabled_servers)

        active_model = config.get_active_model()
        return BannerState(
            active_model=f"{active_model.alias}[{active_model.thinking}]",
            models_count=len(config.models),
            mcp_servers_count=mcp_count,
            connectors_count=connectors_count,
            skills_count=skill_manager.custom_skills_count,
            plan_description=plan_description,
        )

    def _format_meta_counts(self) -> str:
        parts = [_pluralize(self.state.models_count, "model")]
        if self.state.connectors_count > 0:
            parts.append(_pluralize(self.state.connectors_count, "connector"))
        parts.append(_pluralize(self.state.mcp_servers_count, "MCP server"))
        parts.append(_pluralize(self.state.skills_count, "skill"))
        return " · ".join(parts)

    def _format_plan(self) -> str:
        return (
            ""
            if self.state.plan_description is None
            else f" · {self.state.plan_description}"
        )
