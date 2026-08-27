from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from tests.stubs.app_config import build_test_app_config
from vibe.app_server.config import ConfigView, ThinkingLevel
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
)
from vibe.cli.textual_ui.widgets.banner.banner import Banner, BannerState, _pluralize
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.spinner import BrailleSpinner
from vibe.cli.textual_ui.widgets.spinner_text import SpinnerText


def _make_config(
    active_model: str = "test-model",
    thinking: ThinkingLevel = "off",
    display_name: str | None = None,
) -> ConfigView:
    config = build_test_app_config()
    model = config.active_model.model_copy(
        update={
            "name": active_model,
            "alias": active_model,
            "thinking": thinking,
            "display_name": display_name or active_model,
        }
    )
    return config.model_copy(
        update={
            "active_model": model,
            "models": [model],
            "disable_welcome_banner_animation": True,
        }
    )


class _BannerHostApp(App[None]):
    def __init__(self, banner: Banner) -> None:
        super().__init__()
        self._banner = banner

    def compose(self) -> ComposeResult:
        yield self._banner


def _banner_model_text(banner: Banner) -> str:
    return str(banner.query_one("#banner-model", NoMarkupStatic).content)


def _mcp_server(name: str, *, disabled: bool = False) -> MCPSourceSummary:
    return MCPSourceSummary(
        name=name,
        kind=MCPSourceKind.SERVER,
        transport="stdio",
        status=(MCPSourceStatus.DISABLED if disabled else MCPSourceStatus.ENABLED),
    )


def _mcp_state(*sources: MCPSourceSummary) -> MCPState:
    return MCPState(sources=list(sources))


class TestBannerInitialState:
    def test_pluralize(self) -> None:
        assert _pluralize(0, "model") == "0 models"
        assert _pluralize(1, "model") == "1 model"
        assert _pluralize(2, "model") == "2 models"
        assert _pluralize(0, "MCP server") == "0 MCP servers"
        assert _pluralize(1, "MCP server") == "1 MCP server"
        assert _pluralize(2, "connector") == "2 connectors"

    def test_banner_initial_state_includes_connectors(self) -> None:
        banner = Banner(
            config=_make_config(),
            skills_count=0,
            connectors_connected=5,
            connectors_total=5,
        )

        assert banner._initial_state.active_model == "test-model[off]"
        assert banner._initial_state.models_count == 1
        assert banner._initial_state.mcp_servers_enabled == 0
        assert banner._initial_state.mcp_servers_total == 0
        assert banner._initial_state.connectors_connected == 5
        assert banner._initial_state.connectors_total == 5
        assert banner._initial_state.skills_count == 0

    def test_banner_initial_state_with_no_connectors(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)

        assert banner._initial_state.connectors_connected == 0
        assert banner._initial_state.connectors_total is None

    def test_banner_shows_thinking_level(self) -> None:
        banner = Banner(config=_make_config(thinking="max"), skills_count=0)

        assert banner._initial_state.active_model == "test-model[max]"

    def test_banner_shows_display_name(self) -> None:
        banner = Banner(
            config=_make_config(
                active_model="glm-5-2", display_name="glm-5.2 (Mistral Hosted)"
            ),
            skills_count=0,
        )

        assert banner._initial_state.active_model == "glm-5.2 (Mistral Hosted)[off]"

    def test_format_meta_counts_includes_connectors(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)

        banner.state = BannerState(
            models_count=2,
            mcp_servers_enabled=1,
            mcp_servers_total=2,
            connectors_connected=3,
            connectors_total=3,
            skills_count=5,
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        assert "3 connectors" in result
        assert "1/2 MCP servers" in result
        assert "5 skills" in result

        banner.state = BannerState(
            models_count=2,
            mcp_servers_enabled=1,
            mcp_servers_total=2,
            connectors_connected=0,
            connectors_total=0,
            skills_count=5,
        )
        result = banner._format_meta_counts()
        assert "2 models" in result
        # A real zero-connector session shows "0 connectors", not the unknown
        # placeholder; only `None` renders as "0/? connector".
        assert "0 connectors" in result
        assert "0/?" not in result
        assert "1/2 MCP servers" in result
        assert "5 skills" in result


class TestBannerModelPending:
    def test_model_pending_defaults_to_false(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)

        assert banner._initial_state.model_pending is False

    def test_model_pending_propagates_to_initial_state(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, model_pending=True)

        assert banner._initial_state.model_pending is True
        # The resolved name is still computed, ready for when the spinner stops.
        assert banner._initial_state.active_model == "test-model[off]"

    def test_set_state_updates_model_pending(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, model_pending=True)

        banner.set_state(config=_make_config(), skills_count=0, model_pending=False)

        assert banner.state.model_pending is False

    @pytest.mark.asyncio
    async def test_spinner_replaces_model_name_until_resolved(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, model_pending=True)
        app = _BannerHostApp(banner)
        async with app.run_test() as pilot:
            await pilot.pause()

            # While pending, the model slot shows a spinner frame, not a name.
            pending_text = _banner_model_text(banner)
            assert pending_text in BrailleSpinner.FRAMES
            assert "test-model" not in pending_text

            banner.set_state(config=_make_config(), skills_count=0, model_pending=False)
            await pilot.pause()

            # Once resolved, the real model name is shown and the timer is stopped.
            assert _banner_model_text(banner) == "test-model[off]"
            assert banner.query_one("#banner-model", SpinnerText)._timer is None


class TestBannerMCPServersCount:
    def test_banner_counts_enabled_mcp_servers(self) -> None:
        banner = Banner(
            config=_make_config(),
            skills_count=0,
            mcp=_mcp_state(
                _mcp_server("server1"),
                _mcp_server("server2"),
                _mcp_server("server3", disabled=True),
            ),
        )

        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 3

    def test_banner_shows_zero_mcp_servers(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, mcp=MCPState())

        assert banner._initial_state.mcp_servers_enabled == 0
        assert banner._initial_state.mcp_servers_total == 0

    def test_banner_shows_disabled_count_in_xy_format(self) -> None:
        banner = Banner(
            config=_make_config(),
            skills_count=0,
            mcp=_mcp_state(
                _mcp_server("s1"), _mcp_server("s2"), _mcp_server("s3", disabled=True)
            ),
        )

        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 3
        banner.state = banner._initial_state
        assert "2/3 MCP servers" in banner._format_meta_counts()

    def test_banner_shows_simple_count_when_all_enabled(self) -> None:
        banner = Banner(
            config=_make_config(),
            skills_count=0,
            mcp=_mcp_state(_mcp_server("s1"), _mcp_server("s2")),
            connectors_total=None,
        )

        assert banner._initial_state.mcp_servers_enabled == 2
        assert banner._initial_state.mcp_servers_total == 2
        banner.state = banner._initial_state
        result = banner._format_meta_counts()
        assert "2 MCP servers" in result
        assert "1/2" not in result


class TestBannerConnectorsCount:
    def test_connectors_count_passed_through(self) -> None:
        banner = Banner(
            config=_make_config(),
            skills_count=0,
            connectors_connected=3,
            connectors_total=5,
        )

        assert banner._initial_state.connectors_connected == 3
        assert banner._initial_state.connectors_total == 5


class TestBannerHooksCount:
    def test_hooks_count_passed_through(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, hooks_count=4)

        assert banner._initial_state.hooks_count == 4

    def test_hooks_count_defaults_to_zero(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)

        assert banner._initial_state.hooks_count == 0

    def test_format_meta_counts_shows_hooks_when_present(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)
        banner.state = BannerState(models_count=1, skills_count=0, hooks_count=3)

        assert "3 hooks" in banner._format_meta_counts()

    def test_format_meta_counts_singular_hook(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)
        banner.state = BannerState(models_count=1, skills_count=0, hooks_count=1)

        result = banner._format_meta_counts()
        assert "1 hook" in result
        assert "1 hooks" not in result

    def test_format_meta_counts_hides_hooks_when_zero(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0)
        banner.state = BannerState(models_count=1, skills_count=0, hooks_count=0)

        assert "hook" not in banner._format_meta_counts()

    def test_set_state_updates_hooks_count(self) -> None:
        banner = Banner(config=_make_config(), skills_count=0, hooks_count=0)

        banner.set_state(config=_make_config(), skills_count=0, hooks_count=7)

        assert banner.state.hooks_count == 7
