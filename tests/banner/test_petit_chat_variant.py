from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from tests.stubs.app_config import build_test_app_config
from vibe.app_server.config import ConfigView
from vibe.cli.textual_ui.widgets.banner.banner import Banner
from vibe.cli.textual_ui.widgets.banner.petit_chat import (
    HEIGHT,
    LECHONK_HEIGHT,
    LECHONK_STARTING_DOTS,
    LECHONK_WIDTH,
    STARTING_DOTS,
    WIDTH,
    CatVariant,
    PetitChat,
)
from vibe.cli.textual_ui.widgets.braille_renderer import render_braille


def _make_config(display_name: str) -> ConfigView:
    config = build_test_app_config()
    model = config.active_model.model_copy(
        update={
            "name": display_name,
            "alias": display_name,
            "thinking": "off",
            "display_name": display_name,
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
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self, banner: Banner) -> None:
        super().__init__()
        self._banner = banner

    def compose(self) -> ComposeResult:
        yield self._banner


def _chat_text(banner: Banner) -> str:
    return str(banner.query_one(PetitChat).query_one(".petit-chat", Static).content)


def _lechonk_frame() -> str:
    dots = {1j * y + x for y, row in enumerate(LECHONK_STARTING_DOTS) for x in row}
    return render_braille(dots, LECHONK_WIDTH, LECHONK_HEIGHT)


def _lechat_frame() -> str:
    dots = {1j * y + x for y, row in enumerate(STARTING_DOTS) for x in row}
    return render_braille(dots, WIDTH, HEIGHT)


class TestCatVariantFromModel:
    @pytest.mark.parametrize(
        "model",
        [
            "ml4",
            "Mistral Large 4",
            "mistral-large-latest",
            "mistral-large-4",
            "mistral-large-4-0",
            "le-chaton-fat",
            "le-gros-chaton",
            "le-chonk",
        ],
    )
    def test_large_models_pick_lechonk(self, model: str) -> None:
        assert CatVariant.from_model(model) is CatVariant.LECHONK

    @pytest.mark.parametrize(
        "model", ["mistral-medium-latest", "magistral-medium-latest", "test-model", ""]
    )
    def test_other_models_pick_lechat(self, model: str) -> None:
        assert CatVariant.from_model(model) is CatVariant.LECHAT


class TestPetitChatVariant:
    @pytest.mark.asyncio
    async def test_lechonk_variant_renders_wider_grid(self) -> None:
        app = _BannerHostApp(
            Banner(config=_make_config("Mistral Large 4"), skills_count=0)
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one(PetitChat)
            assert chat._variant is CatVariant.LECHONK
            assert _chat_text(app.query_one(Banner)) == _lechonk_frame()
            # The pinned widget box must fit the 13x4 braille grid, otherwise
            # the bottom row and right column get clipped.
            assert chat.outer_size == (14, 4)

    @pytest.mark.asyncio
    async def test_default_model_renders_classic_grid(self) -> None:
        app = _BannerHostApp(
            Banner(config=_make_config("mistral-medium-latest"), skills_count=0)
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one(PetitChat)
            assert chat._variant is CatVariant.LECHAT
            assert _chat_text(app.query_one(Banner)) == _lechat_frame()
            assert chat.outer_size == (12, 3)

    @pytest.mark.asyncio
    async def test_model_switch_swaps_variant(self) -> None:
        banner = Banner(config=_make_config("mistral-medium-latest"), skills_count=0)
        app = _BannerHostApp(banner)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert _chat_text(banner) == _lechat_frame()

            banner.set_state(
                config=_make_config("mistral-large-latest"), skills_count=0
            )
            await pilot.pause()
            chat = banner.query_one(PetitChat)
            assert chat._variant is CatVariant.LECHONK
            assert _chat_text(banner) == _lechonk_frame()
            assert chat.outer_size == (14, 4)

            banner.set_state(
                config=_make_config("mistral-small-latest"), skills_count=0
            )
            await pilot.pause()
            assert banner.query_one(PetitChat)._variant is CatVariant.LECHAT
            assert _chat_text(banner) == _lechat_frame()
            assert banner.query_one(PetitChat).outer_size == (12, 3)
