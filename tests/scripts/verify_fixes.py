from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import MagicMock

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add clean path just in case
sys.path.append("/home/chef/chefchat/ChefChat")

try:
    from chefchat.interface.constants import MessageType
    from chefchat.interface.tui import BusAction, ChefChatApp, PayloadKey, WhiskLoader
    from chefchat.interface.widgets.the_plate import ThePlate
    from chefchat.interface.widgets.ticket_rail import TicketRail
    from chefchat.kitchen.bus import ChefMessage, KitchenBus
except ImportError as e:
    logger.critical(f"Import failed: {e}")
    sys.exit(1)


async def verify_bus_validation():
    print("--- Verifying Bus Validation ---")
    try:
        # Should work with dict
        msg = ChefMessage(
            sender="test", recipient="test", action="test", payload={"foo": "bar"}
        )
        print(f"Valid message created: {msg.payload}")

        # Should work with None -> converted to empty dict
        msg_none = ChefMessage(
            sender="test", recipient="test", action="test", payload=None
        )
        print(f"None payload converted: {msg_none.payload}")

        # Should fail with list
        try:
            ChefMessage(sender="test", recipient="test", action="test", payload=["bad"])
            print("❌ Failed: List payload accepted (should raise ValueError)")
        except ValueError as e:
            print(f"✅ Success: List payload rejected: {e}")

    except Exception as e:
        print(f"❌ unexpected error: {e}")


async def verify_whisk_loader():
    print("\n--- Verifying WhiskLoader Logic ---")
    loader = WhiskLoader()
    # Mock run_worker because we aren't in an App context
    loader.run_worker = MagicMock()
    loader.add_class = MagicMock()
    loader.remove_class = MagicMock()

    print("Starting loader...")
    print(f"loader.run_worker type: {type(loader.run_worker)}")
    # Use lambda to ensure it's callable and returns a mock
    loader.run_worker = MagicMock(return_value=MagicMock())

    loader.start("Test")

    if loader._worker:
        print("✅ Worker task created (mocked)")
    else:
        print("❌ Worker task NOT created")

    print("Stopping loader...")
    loader.stop()
    if loader._worker is None:
        print("✅ Worker cleared")
    else:
        print("❌ Worker NOT cleared")


async def verify_app_handler():
    print("\n--- Verifying App Bus Handler Error Handling ---")
    app = ChefChatApp()

    # Test valid message
    print("Testing valid message...")
    valid_msg = ChefMessage(
        sender="test",
        recipient="tui",
        action=BusAction.LOG_MESSAGE.value,
        payload={
            PayloadKey.TYPE: MessageType.SYSTEM.value,
            PayloadKey.CONTENT: "Hello",
        },
    )
    # Mock query_one to avoid errors
    app.query_one = MagicMock()
    await app._handle_bus_message(valid_msg)
    print("✅ Valid message handled without crash")

    # Test malformed payload handling logic (simulated by mocking)
    # We can't easily trigger the "not dict" warning since Pydantic validator prevents it upstream
    # But we can test unknown action
    print("Testing unknown action...")
    unknown_msg = ChefMessage(sender="test", recipient="tui", action="UNKNOWN_ACTION")
    await app._handle_bus_message(unknown_msg)
    print("✅ Unknown action handled without crash")

    # Test exception within handler
    print("Testing exception resilience...")
    # Make query_one raise exception
    app.query_one.side_effect = Exception("Simulated UI crash")
    await app._handle_bus_message(valid_msg)
    print("✅ Handler caught exception and survived")


async def main():
    await verify_bus_validation()
    await verify_whisk_loader()
    await verify_app_handler()
    print("\nVerification Complete!")


if __name__ == "__main__":
    asyncio.run(main())
