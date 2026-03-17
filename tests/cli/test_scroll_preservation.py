"""Test scroll position preservation during pruning."""
from __future__ import annotations

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_agent_loop
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.app import ChatScroll, VibeApp
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


def _pro_plan_gateway() -> FakeWhoAmIGateway:
    return FakeWhoAmIGateway(
        response=WhoAmIResponse(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        )
    )


@pytest.mark.asyncio
async def test_scroll_position_preserved_when_pruning(vibe_config: VibeConfig) -> None:
    """Test that scroll position is preserved when old messages are pruned.
    
    This addresses the bug where the chat would scroll to the start of the
    conversation when old messages were removed to keep memory usage bounded.
    """
    # Create enough messages to trigger pruning
    # PRUNE_HIGH_MARK is 1500, so we need to exceed that virtual height
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    
    # Add many messages with substantial content to exceed pruning threshold
    for idx in range(100):
        agent_loop.messages.append(
            LLMMessage(
                role=Role.user,
                content=f"Message {idx}\n" + "\n".join([f"Line {i}" for i in range(20)])
            )
        )

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test(size=(120, 40)) as pilot:
        # Wait for initial messages to load
        await pilot.pause(0.5)
        
        chat = app.query_one("#chat", ChatScroll)
        
        # Scroll to middle (not at bottom)
        chat.scroll_y = 500
        await pilot.pause(0.1)
        
        # Store position before any potential pruning
        was_at_bottom_before = chat.is_at_bottom
        
        # Trigger pruning by adding more messages
        for idx in range(100, 150):
            msg = UserMessage(
                content=f"Message {idx}\n" + "\n".join([f"Line {i}" for i in range(20)])
            )
            await app._mount_and_scroll(msg)
            await pilot.pause(0.05)
        
        await pilot.pause(0.5)
        
        # If we weren't at bottom, scroll position should be preserved
        # (approximately - may change due to content removal)
        if not was_at_bottom_before:
            # The scroll position should not jump to 0 (start of conversation)
            # It should maintain relative position or be adjusted for removed content
            assert chat.scroll_y > 100, (
                f"Scroll position jumped to {chat.scroll_y}, "
                "likely reset to start of conversation"
            )
