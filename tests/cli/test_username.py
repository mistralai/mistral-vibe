from __future__ import annotations

import pytest

from tests.conftest import (
    build_test_vibe_app,
    build_test_vibe_config,
    stub_config_reload,
)
from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.app_server._identity import IdentityResult
from vibe.cli.textual_ui.widgets.messages import GreetingMessage
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


@pytest.mark.asyncio
async def test_username_returns_first_name_from_identity() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=False),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(
                id="user-1",
                email="ada@example.com",
                first_name="Ada",
                last_name="Lovelace",
            )
        ),
    )

    async with app.run_test():
        await app.app_server.resources.identity.read()
        username = app._username()

    assert username == "Ada"


@pytest.mark.asyncio
async def test_username_none_without_identity() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(unavailable=True),
    )

    async with app.run_test():
        await app.app_server.resources.identity.read()
        username = app._username()

    assert username is None


@pytest.mark.asyncio
async def test_username_none_when_first_name_empty() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="")
        ),
    )

    async with app.run_test():
        await app.app_server.resources.identity.read()
        username = app._username()

    assert username is None


@pytest.mark.asyncio
async def test_greeting_shown_when_flag_enabled_and_named() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )

    async with app.run_test() as pilot:
        await app.app_server.resources.identity.read()
        await app._show_greeting_message()
        await pilot.pause()
        assert app._greeting_message is not None
        greeting = app.query_one(GreetingMessage)
        assert greeting.display is True
        assert (
            greeting.query_one(NoMarkupStatic).content
            == "Hello Ada, how can I help you?"
        )


@pytest.mark.asyncio
async def test_greeting_shown_after_post_ready_startup() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )

    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
        assert app._greeting_message is not None
        greeting = app.query_one(GreetingMessage)
        assert greeting.display is True
        assert (
            greeting.query_one(NoMarkupStatic).content
            == "Hello Ada, how can I help you?"
        )


@pytest.mark.asyncio
async def test_greeting_hidden_when_flag_disabled() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=False),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )

    async with app.run_test() as pilot:
        await app.app_server.resources.identity.read()
        await app._show_greeting_message()
        await pilot.pause()
        assert app._greeting_message is None


@pytest.mark.asyncio
async def test_greeting_hidden_without_identity() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(unavailable=True),
    )

    async with app.run_test() as pilot:
        await app.app_server.resources.identity.read()
        await app._show_greeting_message()
        await pilot.pause()
        assert app._greeting_message is None


@pytest.mark.asyncio
async def test_greeting_shown_on_first_run() -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )
    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
        assert app._greeting_message is not None
        greeting = app.query_one(GreetingMessage)
        assert greeting.display is True
        assert (
            greeting.query_one(NoMarkupStatic).content
            == "Hello Ada, how can I help you?"
        )


@pytest.mark.asyncio
async def test_greeting_hidden_within_interval() -> None:
    import time

    from vibe.utils.cache_store import FileSystemCacheStore

    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )
    cache = FileSystemCacheStore()
    cache.write_section("greeting", {"last_shown_at": int(time.time())})
    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
        assert app._greeting_message is None


@pytest.mark.asyncio
async def test_greeting_shown_after_interval() -> None:
    import time

    from vibe.utils.cache_store import FileSystemCacheStore

    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )
    cache = FileSystemCacheStore()
    cache.write_section(
        "greeting", {"last_shown_at": int(time.time()) - (25 * 60 * 60)}
    )
    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
        assert app._greeting_message is not None


@pytest.mark.asyncio
async def test_greeting_not_stamped_when_identity_missing() -> None:
    from vibe.utils.cache_store import FileSystemCacheStore

    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(unavailable=True),
    )
    cache = FileSystemCacheStore()
    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
    assert cache.read_section("greeting").get("last_shown_at") is None


@pytest.mark.asyncio
async def test_greeting_removed_when_show_greeting_toggled_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(show_greeting=True),
        identity_gateway=FakeIdentityGateway(
            IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
        ),
    )

    async with app.run_test() as pilot:
        await app._startup_command_availability_ready.wait()
        await pilot.pause()
        assert app._greeting_message is not None

        reloaded = build_test_vibe_config(show_greeting=False)
        stub_config_reload(monkeypatch, reloaded)
        await app._reload_config()
        await pilot.pause()

        assert app._greeting_message is None
        assert not list(app.query(GreetingMessage))
