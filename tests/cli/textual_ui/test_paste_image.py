from __future__ import annotations

import importlib
from pathlib import Path
import platform
import subprocess
from unittest.mock import patch

import pytest
from textual import events
from textual.app import App, ComposeResult

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input import paste_image
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.paste_image import (
    _read_macos,
    _read_macos_class,
    handle_clipboard_image_paste,
    read_clipboard_image,
    write_clipboard_image,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend

_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_FAKE_PNG = _PNG_HEADER + b"fake-image-payload"


def _build_vision_app(*, supports_images: bool = True) -> VibeApp:
    config = build_test_vibe_config(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="mistral-vibe-cli-latest",
                provider="mistral",
                alias="devstral-latest",
                supports_images=supports_images,
            )
        ],
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
            )
        ],
    )
    return build_test_vibe_app(config=config)


def _completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


@pytest.fixture
def _force_supported_platform(monkeypatch) -> None:
    monkeypatch.setattr(paste_image, "is_clipboard_image_paste_supported", lambda: True)


async def _press_ctrl_v_under_platform(monkeypatch, system: str) -> tuple[bool, list]:
    # The ctrl+v binding is registered at ChatTextArea class-definition time only
    # on macOS, so the result of pressing it depends on the OS. Reload the module
    # under a forced platform.system() to get a deterministically-defined class,
    # then exercise it in a minimal app (the full app shares binding maps across
    # instances via a shallow copy, which makes a host-dependent setup flaky).
    monkeypatch.setattr(platform, "system", lambda: system)
    text_area_module = importlib.import_module(
        "vibe.cli.textual_ui.widgets.chat_input.text_area"
    )
    importlib.reload(text_area_module)
    try:
        chat_text_area = text_area_module.ChatTextArea
        has_binding = any(b.key == "ctrl+v" for b in chat_text_area.BINDINGS)

        class _MiniApp(App):
            def compose(self) -> ComposeResult:
                yield chat_text_area(command_registry=CommandRegistry())

        posted: list = []
        async with _MiniApp().run_test() as pilot:
            text_area = pilot.app.query_one(chat_text_area)
            text_area.focus()
            await pilot.pause()
            original_post = text_area.post_message

            def wrapped(message):
                if isinstance(message, chat_text_area.ClipboardImagePasted):
                    posted.append(message)
                return original_post(message)

            text_area.post_message = wrapped
            await pilot.press("ctrl+v")
            await pilot.pause()
        return has_binding, posted
    finally:
        monkeypatch.undo()
        importlib.reload(text_area_module)


def test_read_clipboard_image_returns_none_when_no_readers(
    monkeypatch, _force_supported_platform
) -> None:
    monkeypatch.setattr(paste_image, "_readers_for_platform", lambda: [])
    assert read_clipboard_image() is None


def test_read_clipboard_image_skips_non_png_bytes(
    monkeypatch, _force_supported_platform
) -> None:
    monkeypatch.setattr(
        paste_image, "_readers_for_platform", lambda: [lambda: b"not-a-png"]
    )
    assert read_clipboard_image() is None


def test_read_clipboard_image_returns_first_png(
    monkeypatch, _force_supported_platform
) -> None:
    monkeypatch.setattr(
        paste_image,
        "_readers_for_platform",
        lambda: [lambda: None, lambda: _FAKE_PNG, lambda: b"unused"],
    )
    assert read_clipboard_image() == _FAKE_PNG


def test_read_clipboard_image_swallows_reader_exceptions(
    monkeypatch, _force_supported_platform
) -> None:
    def _raises() -> bytes | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        paste_image, "_readers_for_platform", lambda: [_raises, lambda: _FAKE_PNG]
    )
    assert read_clipboard_image() == _FAKE_PNG


def test_macos_class_reader_returns_bytes_when_osascript_succeeds(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        # osascript wrote the temp file before returning 0; mimic that.
        script_text = cmd[2]
        prefix = 'set targetFile to POSIX file "'
        start = script_text.index(prefix) + len(prefix)
        end = script_text.index('"', start)
        Path(script_text[start:end]).write_bytes(_FAKE_PNG)
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _read_macos_class("PNGf") == _FAKE_PNG


def test_macos_class_reader_returns_none_when_osascript_fails(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=1))
    assert _read_macos_class("PNGf") is None


def test_macos_reader_falls_back_to_tiff_when_png_missing(monkeypatch) -> None:
    fake_tiff = b"II*\x00fake-tiff"
    calls: list[str] = []
    # `sips` only exists on macOS; force the which() probe so the conversion
    # path runs (and hits the mocked subprocess.run) on Linux CI too.
    monkeypatch.setattr(paste_image.shutil, "which", lambda _name: "/usr/bin/sips")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "osascript":
            script_text = cmd[2]
            if "PNGf" in script_text:
                calls.append("PNGf")
                return _completed(returncode=1)
            calls.append("TIFF")
            prefix = 'set targetFile to POSIX file "'
            start = script_text.index(prefix) + len(prefix)
            end = script_text.index('"', start)
            Path(script_text[start:end]).write_bytes(fake_tiff)
            return _completed()
        # sips invocation
        calls.append("sips")
        src = Path(cmd[cmd.index("--out") - 1])
        out = Path(cmd[cmd.index("--out") + 1])
        # Mimic conversion: write a fake PNG to the --out path.
        assert src.read_bytes() == fake_tiff
        out.write_bytes(_FAKE_PNG)
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _read_macos() == _FAKE_PNG
    assert calls == ["PNGf", "TIFF", "sips"]


def test_reader_timeout_is_swallowed(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(paste_image, "_readers_for_platform", lambda: [_read_macos])
    monkeypatch.setattr(paste_image, "is_clipboard_image_paste_supported", lambda: True)
    assert read_clipboard_image() is None


def test_read_clipboard_image_returns_none_on_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(
        paste_image, "is_clipboard_image_paste_supported", lambda: False
    )
    # Even if a reader were defined, the platform gate short-circuits.
    monkeypatch.setattr(
        paste_image, "_readers_for_platform", lambda: [lambda: _FAKE_PNG]
    )
    assert read_clipboard_image() is None


def test_write_clipboard_image_uses_filename_without_whitespace(tmp_path) -> None:
    path = write_clipboard_image(_FAKE_PNG, session_dir=tmp_path)

    assert path.parent == tmp_path / "attachments"
    assert not any(char.isspace() for char in path.name)


@pytest.mark.asyncio
async def test_empty_paste_posts_clipboard_image_pasted_message(
    vibe_app: VibeApp,
) -> None:
    posted: list[ChatTextArea.ClipboardImagePasted] = []

    def capture(message) -> None:
        if isinstance(message, ChatTextArea.ClipboardImagePasted):
            posted.append(message)

    async with vibe_app.run_test() as pilot:
        text_area = vibe_app.query_one(ChatTextArea)
        text_area.focus()
        original_post = text_area.post_message

        def wrapped(message):
            capture(message)
            return original_post(message)

        text_area.post_message = wrapped  # type: ignore[method-assign]
        text_area.post_message(events.Paste(text=""))
        await pilot.pause()

    assert len(posted) == 1


@pytest.mark.asyncio
async def test_non_empty_paste_does_not_post_clipboard_image_pasted(
    vibe_app: VibeApp,
) -> None:
    posted: list[ChatTextArea.ClipboardImagePasted] = []

    async with vibe_app.run_test() as pilot:
        text_area = vibe_app.query_one(ChatTextArea)
        text_area.focus()
        original_post = text_area.post_message

        def wrapped(message):
            if isinstance(message, ChatTextArea.ClipboardImagePasted):
                posted.append(message)
            return original_post(message)

        text_area.post_message = wrapped  # type: ignore[method-assign]
        text_area.post_message(events.Paste(text="hello world"))
        await pilot.pause()

    assert posted == []


@pytest.mark.asyncio
async def test_handle_clipboard_image_paste_writes_file_and_inserts_token() -> None:
    app = _build_vision_app()
    notifications: list[str] = []
    with (
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.read_clipboard_image",
            return_value=_FAKE_PNG,
        ),
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
            return_value=True,
        ),
    ):
        async with app.run_test() as pilot:
            text_area = app.query_one(ChatTextArea)
            text_area.focus()
            with patch.object(
                app, "notify", side_effect=lambda msg, **_kw: notifications.append(msg)
            ):
                await handle_clipboard_image_paste(app, notify_when_empty=False)
            await pilot.pause()
            chat_input = app.query_one(ChatInputContainer)
            value = chat_input.value
            assert value.startswith("@")
            assert value.endswith(".png ")
            assert "clipboard-" in value
            assert len(notifications) == 1
            assert notifications[0].startswith("Image pasted as clipboard-")
            assert notifications[0].endswith(".png (26 Bytes)")


@pytest.mark.asyncio
async def test_handle_clipboard_image_paste_warns_when_token_insert_fails(
    tmp_path, monkeypatch
) -> None:
    app = _build_vision_app()
    path = tmp_path / "clipboard.png"
    notifications: list[tuple[str, str | None]] = []

    def fake_write_clipboard_image(data: bytes, *, session_dir: Path | None) -> Path:
        path.write_bytes(data)
        return path

    def fake_query_one(*_args, **_kwargs):
        raise RuntimeError("not mounted")

    monkeypatch.setattr(paste_image, "is_clipboard_image_paste_supported", lambda: True)
    monkeypatch.setattr(paste_image, "read_clipboard_image", lambda: _FAKE_PNG)
    monkeypatch.setattr(
        paste_image, "write_clipboard_image", fake_write_clipboard_image
    )
    monkeypatch.setattr(app, "query_one", fake_query_one)
    monkeypatch.setattr(
        app,
        "notify",
        lambda msg, **kwargs: notifications.append((msg, kwargs.get("severity"))),
    )

    await handle_clipboard_image_paste(app, notify_when_empty=False)

    assert not path.exists()
    assert notifications == [("Failed to paste image into prompt.", "warning")]


@pytest.mark.asyncio
async def test_handle_clipboard_image_paste_noop_when_clipboard_empty() -> None:
    app = _build_vision_app()
    with (
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.read_clipboard_image",
            return_value=None,
        ),
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
            return_value=True,
        ),
    ):
        async with app.run_test() as pilot:
            text_area = app.query_one(ChatTextArea)
            text_area.focus()
            await handle_clipboard_image_paste(app, notify_when_empty=False)
            await pilot.pause()
            assert app.query_one(ChatInputContainer).value == ""


@pytest.mark.asyncio
async def test_handle_clipboard_image_paste_blocks_when_model_no_vision() -> None:
    app = _build_vision_app(supports_images=False)
    with (
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.read_clipboard_image",
            return_value=_FAKE_PNG,
        ),
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
            return_value=True,
        ),
    ):
        async with app.run_test() as pilot:
            text_area = app.query_one(ChatTextArea)
            text_area.focus()
            await handle_clipboard_image_paste(app, notify_when_empty=False)
            await pilot.pause()
            assert app.query_one(ChatInputContainer).value == ""


@pytest.mark.asyncio
async def test_handle_clipboard_image_paste_rejects_oversize(monkeypatch) -> None:
    app = _build_vision_app()
    notifications: list[str] = []
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.chat_input.paste_image.MAX_IMAGE_BYTES", 10
    )
    with (
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.read_clipboard_image",
            return_value=_FAKE_PNG,
        ),
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
            return_value=True,
        ),
    ):
        async with app.run_test() as pilot:
            text_area = app.query_one(ChatTextArea)
            text_area.focus()
            with patch.object(
                app, "notify", side_effect=lambda msg, **_kw: notifications.append(msg)
            ):
                await handle_clipboard_image_paste(app, notify_when_empty=False)
            await pilot.pause()
            assert app.query_one(ChatInputContainer).value == ""
            assert notifications == ["Clipboard image is 26 Bytes; max is 10 Bytes."]


def test_paste_image_slash_command_hidden_on_unsupported_platform(monkeypatch) -> None:
    from vibe.cli.commands import CommandRegistry

    monkeypatch.setattr("platform.system", lambda: "Linux")
    registry = CommandRegistry()
    assert not registry.has_command("paste-image")


def test_paste_image_slash_command_available_on_darwin(monkeypatch) -> None:
    from vibe.cli.commands import CommandRegistry

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    registry = CommandRegistry()
    assert registry.has_command("paste-image")


def test_ctrl_v_binding_absent_on_unsupported_platform(monkeypatch) -> None:
    # Bindings are evaluated at class-definition time, so we have to
    # re-import the module after patching platform.system().
    import importlib

    from vibe.cli.textual_ui.widgets.chat_input import text_area as text_area_module

    monkeypatch.setattr("platform.system", lambda: "Linux")
    reloaded = importlib.reload(text_area_module)
    try:
        assert all(b.key != "ctrl+v" for b in reloaded.ChatTextArea.BINDINGS)
    finally:
        importlib.reload(text_area_module)


@pytest.mark.asyncio
@pytest.mark.parametrize("notify_when_empty", [True, False])
async def test_handle_clipboard_image_paste_is_silent_on_unsupported_platform(
    notify_when_empty: bool,
) -> None:
    app = _build_vision_app()
    notifications: list[str] = []
    with patch(
        "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
        return_value=False,
    ):
        async with app.run_test() as pilot:
            with patch.object(
                app, "notify", side_effect=lambda msg, **_kw: notifications.append(msg)
            ):
                await handle_clipboard_image_paste(
                    app, notify_when_empty=notify_when_empty
                )
                await pilot.pause()
            assert notifications == []


@pytest.mark.asyncio
async def test_ctrl_v_keybinding_triggers_image_paste_with_notify_on_darwin(
    monkeypatch,
) -> None:
    has_binding, posted = await _press_ctrl_v_under_platform(monkeypatch, "Darwin")
    assert has_binding
    assert len(posted) == 1
    assert posted[0].notify_when_empty is True


@pytest.mark.asyncio
async def test_ctrl_v_keybinding_does_not_paste_image_on_linux(monkeypatch) -> None:
    has_binding, posted = await _press_ctrl_v_under_platform(monkeypatch, "Linux")
    assert not has_binding
    assert posted == []


@pytest.mark.asyncio
async def test_insert_image_token_adds_leading_space_when_needed() -> None:
    app = _build_vision_app()
    with (
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.read_clipboard_image",
            return_value=_FAKE_PNG,
        ),
        patch(
            "vibe.cli.textual_ui.widgets.chat_input.paste_image.is_clipboard_image_paste_supported",
            return_value=True,
        ),
    ):
        async with app.run_test() as pilot:
            text_area = app.query_one(ChatTextArea)
            text_area.focus()
            text_area.text = "look at this:"
            text_area.move_cursor((0, len("look at this:")))
            await handle_clipboard_image_paste(app, notify_when_empty=False)
            await pilot.pause()
            value = app.query_one(ChatInputContainer).value
            assert " @" in value and value.startswith("look at this: @")
