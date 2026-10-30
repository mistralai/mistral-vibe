from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from mistralai_vibe_local_harness.protocol import (
    RustImageContentBlock,
    RustSystemMessage,
    RustTextContentBlock,
    RustUserMessage,
)
from mistralai_vibe_local_harness.vibe import UtilityCompletionError
from mistralai_vibe_local_harness.vibe.completion_profile import WireFormat
from tests.constants import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MESSAGES_PATH,
    CHAT_COMPLETIONS_PATH,
    MISTRAL_BASE_URL,
    OPENAI_BASE_URL,
)
from tests.stubs.fake_utility_completion import (
    FakeUtilityCompletion,
    UtilityCompletionRequest,
)
from tests.stubs.mistral_responses import mistral_completion
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.completion import (
    CompletionTransport,
    ImageReadError,
    _encode_file,
    complete_once,
)
from vibe.core.types import (
    Backend,
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    Role,
)

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import UtilityReply

_PROVIDER = ProviderConfig(
    name="mistral",
    api_base=f"{MISTRAL_BASE_URL}/v1",
    api_key_env_var="MISTRAL_API_KEY",
    backend=Backend.MISTRAL,
    extra_headers={"x-tenant": "acme"},
)
_MODEL = ModelConfig(name="small-model", provider="mistral", alias="small")
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _clear_image_cache() -> None:
    _encode_file.cache_clear()


async def _complete(
    messages: list[LLMMessage],
    *,
    provider: ProviderConfig = _PROVIDER,
    model: ModelConfig = _MODEL,
    temperature: float | None = 0.0,
) -> UtilityReply:
    return await complete_once(
        provider=provider,
        model=model,
        messages=messages,
        max_tokens=64,
        metadata={"call_type": "secondary_call"},
        transport=CompletionTransport(
            timeout_s=3.0,
            retry_budget_s=0,
            stream_idle_timeout_s=30.0,
            enable_system_trust_store=True,
        ),
        temperature=temperature,
    )


def _png(tmp_path: Path, name: str = "shot.png") -> ImageAttachment:
    path = tmp_path / name
    path.write_bytes(_PNG_BYTES)
    return ImageAttachment(
        source=FileImageSource(path=path), alias=name, mime_type="image/png"
    )


def _sent_image(request: UtilityCompletionRequest) -> str:
    [message] = request.messages
    assert isinstance(message, RustUserMessage)
    [block] = message.content
    assert isinstance(block, RustImageContentBlock)
    return block.data


@pytest.mark.asyncio
async def test_messages_and_images_reach_the_harness_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completion = FakeUtilityCompletion("described").install(monkeypatch)
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"png-bytes")

    await _complete([
        LLMMessage(role=Role.system, content="describe"),
        LLMMessage(
            role=Role.user,
            content="what is this?",
            images=[
                ImageAttachment(
                    source=FileImageSource(path=image_path),
                    alias="shot.png",
                    mime_type="image/png",
                ),
                ImageAttachment(
                    source=InlineImageSource(data="aW5saW5l"),
                    alias="inline.png",
                    mime_type="image/png",
                ),
            ],
        ),
    ])

    [request] = completion.requests
    assert request.messages == [
        RustSystemMessage(content=[RustTextContentBlock(text="describe")]),
        RustUserMessage(
            content=[
                RustTextContentBlock(text="what is this?"),
                RustImageContentBlock(
                    data=base64.b64encode(b"png-bytes").decode(), mime_type="image/png"
                ),
                RustImageContentBlock(data="aW5saW5l", mime_type="image/png"),
            ]
        ),
    ]


@pytest.mark.asyncio
async def test_the_provider_settings_and_budgets_reach_the_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion("ok").install(monkeypatch)

    await _complete([LLMMessage(role=Role.user, content="hi")])

    [request] = completion.requests
    assert request.config.base_url == _PROVIDER.api_base
    assert request.config.extra_headers["x-tenant"] == "acme"
    assert request.config.max_tokens == 64
    assert request.config.timeout_s == 3.0
    assert request.config.stream_idle_timeout_s == 30.0
    assert request.config.retry_max_elapsed_time_s == 0
    assert request.config.enable_system_trust_store is True
    assert request.route.temperature == 0.0
    assert request.metadata == {"call_type": "secondary_call"}


@pytest.mark.asyncio
async def test_the_model_output_cap_reaches_the_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    capped = _MODEL.model_copy(update={"max_output_tokens": 8192})

    await _complete([LLMMessage(role=Role.user, content="hi")], model=capped)

    [request] = completion.requests
    assert request.route.max_output_tokens == 8192


@pytest.mark.asyncio
async def test_top_p_is_sent_only_with_the_model_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mistral rejects greedy sampling unless top_p is 1.
    completion = FakeUtilityCompletion().install(monkeypatch)
    nucleus = _MODEL.model_copy(update={"top_p": 0.9})

    await _complete([LLMMessage(role=Role.user, content="hi")], model=nucleus)
    await _complete(
        [LLMMessage(role=Role.user, content="hi")], model=nucleus, temperature=None
    )

    greedy_route, own_route = (r.route for r in completion.requests)
    assert greedy_route.top_p is None
    assert own_route.top_p == 0.9


@pytest.mark.asyncio
async def test_an_unset_level_asks_an_always_reasoning_model_for_its_lowest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    always_reasons = ModelConfig(
        name="small-model",
        provider="mistral",
        alias="small",
        thinking_levels=["low", "high"],
    )

    await _complete([LLMMessage(role=Role.user, content="hi")], model=always_reasons)

    [request] = completion.requests
    assert request.route.thinking == "low"


@pytest.mark.asyncio
async def test_off_is_sent_only_to_a_model_whose_levels_offer_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some hosted models reject an explicit off; the default level set says
    # nothing about the model, so it leaves the server default in charge.
    completion = FakeUtilityCompletion().install(monkeypatch)
    curated = _MODEL.model_copy(update={"thinking_levels": ["off", "high"]})

    await _complete([LLMMessage(role=Role.user, content="hi")])
    await _complete([LLMMessage(role=Role.user, content="hi")], model=curated)

    uncurated_route, curated_route = (r.route for r in completion.requests)
    assert uncurated_route.reasoning_setting(WireFormat.MISTRAL) is None
    assert curated_route.reasoning_setting(WireFormat.MISTRAL) == "off"


@pytest.mark.asyncio
async def test_listed_levels_in_any_order_compile_as_the_session_compiles_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    unsorted = _MODEL.model_copy(update={"thinking_levels": ["high", "low", "off"]})

    await _complete([LLMMessage(role=Role.user, content="hi")], model=unsorted)

    [request] = completion.requests
    assert request.route.reasoning is not None
    assert request.route.reasoning.levels == ("low", "high")
    assert request.route.reasoning.can_turn_off


@pytest.mark.asyncio
async def test_only_off_listed_still_sends_off_to_a_known_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    known = ModelConfig(
        name="mistral-vibe-cli-latest",
        provider="mistral",
        alias="devstral",
        thinking_levels=["off"],
    )

    await _complete([LLMMessage(role=Role.user, content="hi")], model=known)

    [request] = completion.requests
    assert request.route.reasoning_setting(WireFormat.MISTRAL) == "off"


@pytest.mark.asyncio
async def test_a_format_alias_is_sent_as_the_standard_setting_turns_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    openai = ProviderConfig(
        name="openai",
        api_base=f"{OPENAI_BASE_URL}/v1",
        api_key_env_var="OPENAI_API_KEY",
        api_style="openai",
        backend=Backend.GENERIC,
    )
    minimal = ModelConfig(
        name="gpt-model", provider="openai", alias="gpt", thinking="minimal"
    )
    none = _MODEL.model_copy(update={"thinking": "none"})

    await _complete(
        [LLMMessage(role=Role.user, content="hi")], provider=openai, model=minimal
    )
    await _complete([LLMMessage(role=Role.user, content="hi")], model=none)

    openai_route, mistral_route = (r.route for r in completion.requests)
    assert openai_route.thinking == "low"
    assert mistral_route.reasoning_setting(WireFormat.MISTRAL) is None


@pytest.mark.asyncio
async def test_an_image_file_is_read_once_while_it_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    image = _png(tmp_path)

    await _complete([LLMMessage(role=Role.user, images=[image])])
    await _complete([LLMMessage(role=Role.user, images=[image])])

    first, second = (_sent_image(r) for r in completion.requests)
    assert first == second == base64.b64encode(_PNG_BYTES).decode("ascii")
    assert _encode_file.cache_info().misses == 1


@pytest.mark.asyncio
async def test_an_image_file_is_read_again_once_it_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    image = _png(tmp_path)
    await _complete([LLMMessage(role=Role.user, images=[image])])
    assert isinstance(image.source, FileImageSource)
    path = image.source.path
    changed = _PNG_BYTES + b"\x01"
    path.write_bytes(changed)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    await _complete([LLMMessage(role=Role.user, images=[image])])

    assert _sent_image(completion.requests[-1]) == base64.b64encode(changed).decode(
        "ascii"
    )


@pytest.mark.asyncio
async def test_a_missing_image_file_fails_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    missing = ImageAttachment(
        source=FileImageSource(path=tmp_path / "nope.png"),
        alias="nope.png",
        mime_type="image/png",
    )

    with pytest.raises(ImageReadError):
        await _complete([LLMMessage(role=Role.user, images=[missing])])

    assert completion.requests == []


@pytest.mark.asyncio
async def test_inline_image_data_is_sent_without_reading_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = FakeUtilityCompletion().install(monkeypatch)
    encoded = base64.b64encode(_PNG_BYTES).decode("ascii")
    inline = ImageAttachment(
        source=InlineImageSource(data=encoded),
        alias="pasted.png",
        mime_type="image/png",
    )

    await _complete([LLMMessage(role=Role.user, images=[inline])])

    [request] = completion.requests
    assert _sent_image(request) == encoded
    assert _encode_file.cache_info().misses == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_name", "thinking_type", "output_config"),
    [
        ("claude-opus-4-7", "disabled", None),
        ("claude-opus-5-5", "adaptive", {"effort": "low"}),
    ],
)
async def test_an_uncurated_claude_model_gets_its_known_thinking_fields(
    monkeypatch: pytest.MonkeyPatch,
    model_name: str,
    thinking_type: str,
    output_config: dict[str, str] | None,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    provider = ProviderConfig(
        name="anthropic",
        api_base=ANTHROPIC_BASE_URL,
        api_key_env_var="ANTHROPIC_API_KEY",
        backend=Backend.GENERIC,
        api_style="anthropic",
    )
    model = ModelConfig(name=model_name, provider="anthropic", alias=model_name)

    with respx.mock() as router:
        route = router.post(f"{ANTHROPIC_BASE_URL}{ANTHROPIC_MESSAGES_PATH}").mock(
            return_value=httpx.Response(400, json={"error": {"message": "stop"}})
        )
        with pytest.raises(UtilityCompletionError):
            await complete_once(
                provider=provider,
                model=model,
                messages=[LLMMessage(role=Role.user, content="hi")],
                max_tokens=2048,
                metadata={},
                transport=CompletionTransport(
                    timeout_s=3.0,
                    retry_budget_s=0,
                    stream_idle_timeout_s=None,
                    enable_system_trust_store=True,
                ),
            )

    body = json.loads(route.calls.last.request.content)
    assert body["thinking"]["type"] == thinking_type
    assert body.get("output_config") == output_config


@pytest.mark.asyncio
async def test_an_empty_answer_reads_as_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeUtilityCompletion(" ").install(monkeypatch)

    result = await _complete([LLMMessage(role=Role.user, content="hi")])

    assert result.text == ""
    assert result.usage.input_tokens == 10
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_the_mistral_request_carries_metadata_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "secret")
    chunk = mistral_completion("pong")
    chunk["object"] = "chat.completion.chunk"
    choice = chunk["choices"][0]
    choice["delta"] = choice.pop("message")
    stream = b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"

    with respx.mock(assert_all_called=True) as router:
        route = router.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
            return_value=httpx.Response(
                200, content=stream, headers={"Content-Type": "text/event-stream"}
            )
        )
        result = await _complete([LLMMessage(role=Role.user, content="ping")])

    assert result.text == "pong"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer secret"
    assert sent.headers["x-tenant"] == "acme"
    body = json.loads(sent.content)
    assert body["metadata"] == {"call_type": "secondary_call"}
    assert body["max_tokens"] == 64


@pytest.mark.asyncio
async def test_a_provider_refusal_carries_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "secret")

    with respx.mock() as router:
        router.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
            return_value=httpx.Response(404, json={"message": "Invalid model"})
        )
        with pytest.raises(UtilityCompletionError) as raised:
            await _complete([LLMMessage(role=Role.user, content="ping")])

    assert raised.value.status == 404
