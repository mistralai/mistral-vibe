from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalGroup
from textual.reactive import reactive
from textual.widgets import Static

from vibe import __version__
from vibe.app_server.config import ConfigView
from vibe.app_server.models import MCPSourceKind, MCPSourceStatus, MCPState
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.spinner_text import SpinnerText


def _pluralize(count: int, singular: str) -> str:
    return f"{count} {singular}{'s' if count != 1 else ''}"


@dataclass
class BannerState:
    active_model: str = ""
    model_pending: bool = False
    models_count: int = 0
    mcp_servers_enabled: int = 0
    mcp_servers_total: int = 0
    connectors_connected: int = 0
    connectors_total: int | None = None
    skills_count: int = 0
    hooks_count: int = 0
    plan_description: str | None = None


class Banner(Static):
    state = reactive(BannerState(), init=False)

    def __init__(
        self,
        config: ConfigView | None,
        skills_count: int,
        mcp: MCPState | None = None,
        *,
        mcp_servers_total: int = 0,
        mcp_servers_enabled: int = 0,
        connectors_connected: int = 0,
        connectors_total: int | None = None,
        hooks_count: int = 0,
        model_pending: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.can_focus = False
        self._initial_state = self._build_state(
            config=config,
            skills_count=skills_count,
            mcp=mcp,
            mcp_servers_total=mcp_servers_total,
            mcp_servers_enabled=mcp_servers_enabled,
            connectors_connected=connectors_connected,
            connectors_total=connectors_total,
            hooks_count=hooks_count,
            plan_description=None,
            model_pending=model_pending,
        )
        self._animated = not (config is None or config.disable_welcome_banner_animation)

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="banner-container"):
            yield PetitChat(animate=self._animated)

            with Vertical(id="banner-info"):
                with Horizontal(classes="banner-line"):
                    yield NoMarkupStatic("Mistral Vibe", id="banner-brand")
                    yield NoMarkupStatic(" ", classes="banner-spacer")
                    yield NoMarkupStatic(f"v{__version__} · ", classes="banner-meta")
                    yield SpinnerText(id="banner-model")
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
        widgets = {widget.id: widget for widget in self.query(NoMarkupStatic)}
        model = widgets.get("banner-model")
        counts = widgets.get("banner-meta-counts")
        plan = widgets.get("banner-user-plan")
        if not isinstance(model, SpinnerText) or counts is None or plan is None:
            return
        model.set_pending(self.state.model_pending, resolved=self.state.active_model)
        counts.update(self._format_meta_counts())
        plan.update(self._format_plan())

    def freeze_animation(self) -> None:
        if self._animated:
            self.query_one(PetitChat).freeze_animation()

    def set_state(
        self,
        config: ConfigView | None,
        skills_count: int,
        mcp: MCPState | None = None,
        *,
        mcp_servers_total: int = 0,
        mcp_servers_enabled: int = 0,
        connectors_connected: int = 0,
        connectors_total: int | None = None,
        hooks_count: int = 0,
        plan_description: str | None = None,
        model_pending: bool = False,
    ) -> None:
        self.state = self._build_state(
            config=config,
            skills_count=skills_count,
            mcp=mcp,
            mcp_servers_total=mcp_servers_total,
            mcp_servers_enabled=mcp_servers_enabled,
            connectors_connected=connectors_connected,
            connectors_total=connectors_total,
            hooks_count=hooks_count,
            plan_description=plan_description,
            model_pending=model_pending,
        )

    @staticmethod
    def _build_state(
        config: ConfigView | None,
        skills_count: int,
        mcp: MCPState | None = None,
        *,
        mcp_servers_total: int = 0,
        mcp_servers_enabled: int = 0,
        connectors_connected: int = 0,
        connectors_total: int | None = None,
        hooks_count: int = 0,
        plan_description: str | None = None,
        model_pending: bool = False,
    ) -> BannerState:
        if config is None:
            return BannerState()
        if mcp is not None:
            servers = [
                source for source in mcp.sources if source.kind is MCPSourceKind.SERVER
            ]
            enabled_servers = [
                source
                for source in servers
                if source.status is not MCPSourceStatus.DISABLED
            ]
            mcp_enabled = len(enabled_servers)
            mcp_total = len(servers)
        else:
            mcp_enabled = mcp_servers_enabled
            mcp_total = mcp_servers_total
        active_model = config.active_model
        return BannerState(
            active_model=f"{active_model.alias}[{active_model.thinking}]",
            model_pending=model_pending,
            models_count=len(config.models),
            mcp_servers_enabled=mcp_enabled,
            mcp_servers_total=mcp_total,
            connectors_connected=connectors_connected,
            connectors_total=connectors_total,
            skills_count=skills_count,
            hooks_count=hooks_count,
            plan_description=plan_description,
        )

    def _format_meta_counts(self) -> str:
        if self.state.models_count == 0:
            return ""
        parts = [_pluralize(self.state.models_count, "model")]
        # `None` means the total is unknown (pre-session cold path); `0` is a
        # real zero-connector session and must not be shown as unknown.
        if self.state.connectors_total is None:
            parts.append(f"{self.state.connectors_connected}/? connector")
        elif self.state.connectors_connected != self.state.connectors_total:
            connector_str = (
                f"{self.state.connectors_connected}/{self.state.connectors_total} connector"
                + ("s" if self.state.connectors_total != 1 else "")
            )
            parts.append(connector_str)
        else:
            parts.append(_pluralize(self.state.connectors_connected, "connector"))
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
