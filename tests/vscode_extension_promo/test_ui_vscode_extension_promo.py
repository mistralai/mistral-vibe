from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from tests.vscode_extension_promo.fake_repository import (
    FakeVscodeExtensionPromoRepository,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import (
    VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX,
    VscodeExtensionPromoMessage,
    WhatsNewMessage,
)
from vibe.cli.update_notifier import UpdateCache
from vibe.cli.vscode_extension_promo import (
    VscodeExtensionPromo,
    VscodeExtensionPromoState,
)
from vibe.core.config import VibeConfig


@pytest.fixture
def vibe_config() -> VibeConfig:
    return build_test_vibe_config(enable_update_checks=True)


def _build_app(
    *,
    config: VibeConfig,
    promo: VscodeExtensionPromo | None,
    update_cache_repository: FakeUpdateCacheRepository,
    current_version: str = "1.0.0",
) -> VibeApp:
    return build_test_vibe_app(
        config=config,
        update_notifier=FakeUpdateGateway(update=None),
        update_cache_repository=update_cache_repository,
        current_version=current_version,
        vscode_extension_promo=promo,
    )


async def _wait_for(predicate: Callable[[], bool], pilot, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await pilot.pause(0.05)
    pytest.fail("Condition not met within timeout")


@pytest.fixture(autouse=True)
def _bypass_promo_gates() -> object:
    with (
        patch("vibe.cli.textual_ui.app._is_vscode_family_terminal", return_value=True),
        patch("vibe.cli.textual_ui.app.should_show_promo") as mocked_should_show,
    ):
        mocked_should_show.side_effect = lambda state: (
            state is None or state.shown_count < 10
        )
        yield mocked_should_show


@pytest.mark.asyncio
async def test_promo_appears_as_standalone_chat_message_when_no_whats_new(
    vibe_config: VibeConfig, tmp_path: Path
) -> None:
    repository = FakeVscodeExtensionPromoRepository()
    promo = VscodeExtensionPromo(repository=repository, initial_state=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version="1.0.0",
    )
    app = _build_app(
        config=vibe_config,
        promo=promo,
        update_cache_repository=FakeUpdateCacheRepository(update_cache=cache),
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await _wait_for(lambda: bool(app.query(VscodeExtensionPromoMessage)), pilot)

    assert not app.query(WhatsNewMessage)
    assert repository.state == VscodeExtensionPromoState(shown_count=1)


@pytest.mark.asyncio
async def test_promo_is_merged_into_whats_new_message_when_both_shown(
    vibe_config: VibeConfig, tmp_path: Path
) -> None:
    repository = FakeVscodeExtensionPromoRepository()
    promo = VscodeExtensionPromo(repository=repository, initial_state=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    app = _build_app(
        config=vibe_config,
        promo=promo,
        update_cache_repository=FakeUpdateCacheRepository(update_cache=cache),
    )

    whats_new_content = "# What's New\n\n- Feature 1"
    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        (tmp_path / "whats_new.md").write_text(whats_new_content)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await _wait_for(lambda: bool(app.query(WhatsNewMessage)), pilot)
            message = app.query_one(WhatsNewMessage)
            await _wait_for(
                lambda: repository.state == VscodeExtensionPromoState(shown_count=1),
                pilot,
            )

    assert not app.query(VscodeExtensionPromoMessage)
    assert message._content.startswith(whats_new_content)
    assert message._content.endswith(VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX)


@pytest.mark.asyncio
async def test_promo_does_not_appear_when_no_promo_dependency_provided(
    vibe_config: VibeConfig, tmp_path: Path
) -> None:
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version="1.0.0",
    )
    app = _build_app(
        config=vibe_config,
        promo=None,
        update_cache_repository=FakeUpdateCacheRepository(update_cache=cache),
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)

    assert not app.query(VscodeExtensionPromoMessage)
    assert not app.query(WhatsNewMessage)
