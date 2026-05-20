from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.widgets.messages import WarningMessage
from vibe.cli.textual_ui.widgets.model_picker import ModelPickerApp
from vibe.core.config import ModelConfig
from vibe.core.types import LLMMessage, Role


def _make_config_with_mixed_thinking() -> tuple[ModelConfig, ModelConfig, ModelConfig]:
    """Create three models: one with thinking='off', one with 'low', one with 'high'."""
    model_off = ModelConfig(
        name="model-no-reasoning",
        provider="mistral",
        alias="no-thinking",
        thinking="off",
    )
    model_low = ModelConfig(
        name="model-low-reasoning",
        provider="mistral",
        alias="low-thinking",
        thinking="low",
    )
    model_high = ModelConfig(
        name="model-high-reasoning",
        provider="mistral",
        alias="high-thinking",
        thinking="high",
    )
    return model_off, model_low, model_high


@pytest.mark.asyncio
async def test_switch_from_reasoning_to_non_reasoning_clears_history() -> None:
    """Test that switching from a reasoning model to non-reasoning clears history."""
    model_off, model_low, _ = _make_config_with_mixed_thinking()
    config = build_test_vibe_config(
        models=[model_low, model_off],
        active_model="low-thinking",
    )

    app = build_test_vibe_app(config=config)

    # Add some messages to simulate history (more than 1, since index 0 is system message)
    app.agent_loop.messages.append(
        LLMMessage(role=Role.user, content="Hello there")
    )
    app.agent_loop.messages.append(
        LLMMessage(role=Role.assistant, content="Hi back!")
    )

    # Verify we have history
    assert len(app.agent_loop.messages) > 1

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates"):
            with patch.object(app, "_reload_config", new_callable=AsyncMock):
                with patch.object(app, "_switch_to_input_app", new_callable=AsyncMock):
                    with patch.object(app, "_clear_history", new_callable=AsyncMock) as mock_clear:
                        with patch.object(
                            app, "_mount_and_scroll", new_callable=AsyncMock
                        ) as mock_mount:
                            # Post message to switch to non-reasoning model
                            app.post_message(ModelPickerApp.ModelSelected("no-thinking"))
                            await pilot.pause(0.2)

                            # Verify _clear_history was called
                            mock_clear.assert_called_once()

                            # Verify warning message was mounted
                            mock_mount.assert_called_once()
                            call_args = mock_mount.call_args[0]
                            assert isinstance(call_args[0], WarningMessage)
                            assert "History cleared" in str(call_args[0]._message)


@pytest.mark.asyncio
async def test_switch_from_reasoning_to_non_reasoning_with_no_history_does_not_clear() -> None:
    """Test that switching to non-reasoning with no history doesn't clear."""
    model_off, model_low, _ = _make_config_with_mixed_thinking()
    config = build_test_vibe_config(
        models=[model_low, model_off],
        active_model="low-thinking",
    )

    app = build_test_vibe_app(config=config)

    # Only system message exists (no user messages)
    assert len(app.agent_loop.messages) == 1

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates"):
            with patch.object(app, "_reload_config", new_callable=AsyncMock):
                with patch.object(app, "_switch_to_input_app", new_callable=AsyncMock):
                    with patch.object(app, "_clear_history", new_callable=AsyncMock) as mock_clear:
                        with patch.object(
                            app, "_mount_and_scroll", new_callable=AsyncMock
                        ) as mock_mount:
                            # Post message to switch to non-reasoning model
                            app.post_message(ModelPickerApp.ModelSelected("no-thinking"))
                            await pilot.pause(0.2)

                            # Verify _clear_history was NOT called (only 1 message = system message)
                            mock_clear.assert_not_called()
                            mock_mount.assert_not_called()


@pytest.mark.asyncio
async def test_switch_from_non_reasoning_to_reasoning_does_not_clear() -> None:
    """Test that switching from non-reasoning to reasoning doesn't clear history."""
    model_off, model_low, _ = _make_config_with_mixed_thinking()
    config = build_test_vibe_config(
        models=[model_off, model_low],
        active_model="no-thinking",
    )

    app = build_test_vibe_app(config=config)

    # Add some messages
    app.agent_loop.messages.append(
        LLMMessage(role=Role.user, content="Hello there")
    )
    app.agent_loop.messages.append(
        LLMMessage(role=Role.assistant, content="Hi back!")
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates"):
            with patch.object(app, "_reload_config", new_callable=AsyncMock):
                with patch.object(app, "_switch_to_input_app", new_callable=AsyncMock):
                    with patch.object(app, "_clear_history", new_callable=AsyncMock) as mock_clear:
                        # Post message to switch to reasoning model
                        app.post_message(ModelPickerApp.ModelSelected("low-thinking"))
                        await pilot.pause(0.2)

                        # Verify _clear_history was NOT called (switching to reasoning, not from)
                        mock_clear.assert_not_called()


@pytest.mark.asyncio
async def test_switch_between_reasoning_models_does_not_clear() -> None:
    """Test that switching between two reasoning models doesn't clear history."""
    _, model_low, model_high = _make_config_with_mixed_thinking()
    config = build_test_vibe_config(
        models=[model_low, model_high],
        active_model="low-thinking",
    )

    app = build_test_vibe_app(config=config)

    # Add some messages
    app.agent_loop.messages.append(
        LLMMessage(role=Role.user, content="Hello there")
    )
    app.agent_loop.messages.append(
        LLMMessage(role=Role.assistant, content="Hi back!")
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates"):
            with patch.object(app, "_reload_config", new_callable=AsyncMock):
                with patch.object(app, "_switch_to_input_app", new_callable=AsyncMock):
                    with patch.object(app, "_clear_history", new_callable=AsyncMock) as mock_clear:
                        # Post message to switch between reasoning models
                        app.post_message(ModelPickerApp.ModelSelected("high-thinking"))
                        await pilot.pause(0.2)

                        # Verify _clear_history was NOT called (both have reasoning)
                        mock_clear.assert_not_called()
