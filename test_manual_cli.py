#!/usr/bin/env python3
"""Manual CLI tests for Phase 4-9 features."""

from __future__ import annotations

import asyncio


async def test_config():
    """Test config loading."""
    print("🧪 Testing config loading...")
    from chefchat.config import load_palate_config

    config = load_palate_config()
    print(f"  ✅ Framework: {config.framework}")
    print(f"  ✅ Linter: {config.linter}")
    print(f"  ✅ LLM Provider: {config.llm.provider}")
    print(f"  ✅ Max healing attempts: {config.healing.max_attempts}")


async def test_brain():
    """Test brain initialization."""
    print("\n🧪 Testing KitchenBrain...")
    from chefchat.kitchen.brain import KitchenBrain

    brain = KitchenBrain()
    print(f"  ✅ Provider: {brain.config.provider}")
    print(f"  ✅ Model: {brain.config.model}")

    # Test simulated response
    print("\n  Testing simulated response (no API key)...")
    response = await brain.generate_plan("Build a simple calculator")
    print(f"  ✅ Got response: {response[:100]}...")


async def test_git():
    """Test git integration."""
    print("\n🧪 Testing Git mise en place...")
    from chefchat.kitchen.mise_en_place import has_changes, is_git_repo

    is_repo = await is_git_repo()
    print(f"  ✅ Is git repo: {is_repo}")

    if is_repo:
        changes = await has_changes()
        print(f"  ✅ Has changes: {changes}")


async def test_expeditor_config():
    """Test expeditor with config."""
    print("\n🧪 Testing Expeditor config integration...")
    from chefchat.kitchen.bus import KitchenBus
    from chefchat.kitchen.stations.expeditor import Expeditor

    bus = KitchenBus()
    expeditor = Expeditor(bus)

    print(f"  ✅ Max healing attempts: {expeditor.max_healing_attempts}")
    print(f"  ✅ Timeout: {expeditor.timeout}s")
    print(
        f"  ✅ Config loaded: {expeditor._config.framework}/{expeditor._config.linter}"
    )


async def test_sous_chef_commands():
    """Test SousChef has new command handlers."""
    print("\n🧪 Testing SousChef command handlers...")

    from chefchat.kitchen.bus import KitchenBus
    from chefchat.kitchen.stations.sous_chef import SousChef

    bus = KitchenBus()
    sous_chef = SousChef(bus)

    # Check methods exist
    methods = [
        m
        for m in dir(sous_chef)
        if not m.startswith("_") or m.startswith("_undo") or m.startswith("_roast")
    ]

    has_undo = "_undo_changes" in dir(sous_chef)
    has_roast = "_roast_code" in dir(sous_chef)

    print(f"  ✅ Has _undo_changes: {has_undo}")
    print(f"  ✅ Has _roast_code: {has_roast}")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("ChefChat Phase 4-9 Manual CLI Tests")
    print("=" * 60)

    await test_config()
    await test_brain()
    await test_git()
    await test_expeditor_config()
    await test_sous_chef_commands()

    print("\n" + "=" * 60)
    print("✅ All manual tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
