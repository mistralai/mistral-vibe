from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalGroup
from textual.reactive import reactive
from textual.widgets import Static

from vibe import __version__
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import AnyVibeConfig
from vibe.core.skills.manager import SkillManager


def _pluralize(count: int, singular: str) -> str:
    return f"{count} {singular}{'s' if count != 1 else ''}"


@dataclass
class BannerState:
    active_model: str = ""
    models_count: int = 0
    mcp_servers_enabled: int = 0
    mcp_servers_total: int = 0
    connectors_connected: int = 0
    connectors_total: int = 0
    skills_count: int = 0
    hooks_count: int = 0
    plan_description: str | None = None


class Banner(Static):
    state = reactive(BannerState(), init=False)

    def __init__(
        self,
        config: AnyVibeConfig,
        skill_manager: SkillManager,
        connectors_connected: int = 0,
        connectors_total: int = 0,
        hooks_count: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.can_focus = False
        self._initial_state = self._build_state(
            config=config,
            skill_manager=skill_manager,
            connectors_connected=connectors_connected,
            connectors_total=connectors_total,
            hooks_count=hooks_count,
            plan_description=None,
        )
        self._animated = not config.disable_welcome_banner_animation

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="banner-container"):
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
        config: AnyVibeConfig,
        skill_manager: SkillManager,
        connectors_connected: int = 0,
        connectors_total: int = 0,
        hooks_count: int = 0,
        plan_description: str | None = None,
    ) -> None:
        self.state = self._build_state(
            config,
            skill_manager,
            connectors_connected,
            connectors_total,
            hooks_count,
            plan_description,
        )

    @staticmethod
    def _build_state(
        config: AnyVibeConfig,
        skill_manager: SkillManager,
        connectors_connected: int = 0,
        connectors_total: int = 0,
        hooks_count: int = 0,
        plan_description: str | None = None,
    ) -> BannerState:
        all_servers = config.mcp_servers
        enabled_servers = [s for s in all_servers if not s.disabled]

        active_model = config.get_active_model()
        return BannerState(
            active_model=f"{active_model.alias}[{active_model.thinking}]",
            models_count=len(config.models),
            mcp_servers_enabled=len(enabled_servers),
            mcp_servers_total=len(all_servers),
            connectors_connected=connectors_connected,
            connectors_total=connectors_total,
            skills_count=skill_manager.custom_skills_count,
            hooks_count=hooks_count,
            plan_description=plan_description,
        )

    def _format_meta_counts(self) -> str:
        parts = [_pluralize(self.state.models_count, "model")]
        # Format as x/y for MCP servers and connectors (only when enabled != total)
        if self.state.connectors_total > 0:
            if self.state.connectors_connected != self.state.connectors_total:
                connector_str = (
                    f"{self.state.connectors_connected}/{self.state.connectors_total} connector"
                    + ("s" if self.state.connectors_total != 1 else "")
                )
            else:
                connector_str = _pluralize(self.state.connectors_connected, "connector")
            parts.append(connector_str)
        # Always show MCP servers count (even if 0/0)
        if self.state.mcp_servers_enabled != self.state.mcp_servers_total:
            mcp_str = (
                f"{self.state.mcp_servers_enabled}/{self.state.mcp_servers_total} MCP server"
                + ("s" if self.state.mcp_servers_total != 1 else "")
            )
        else:
            mcp_str = _pluralize(self.state.mcp_servers_enabled, "MCP server")
        parts.append(mcp_str)
        parts.append(_pluralize(self.state.skills_count, "skill"))
        if self.state.hooks_count > 0:
            parts.append(_pluralize(self.state.hooks_count, "hook"))
        return " · ".join(parts)

    def _format_plan(self) -> str:
        return (
            ""
            if self.state.plan_description is None
            else f" · {self.state.plan_description}"
        )
