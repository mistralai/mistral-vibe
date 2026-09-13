from __future__ import annotations

from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState


def test_context_progress_shows_percentage_when_empty() -> None:
    widget = ContextProgress()

    widget.watch_tokens(TokenState(max_tokens=200_000, current_tokens=0))

    assert str(widget.render()) == "0/200k tokens (0%)"


def test_context_progress_uses_compact_integer_format_for_used_tokens() -> None:
    widget = ContextProgress()

    widget.watch_tokens(TokenState(max_tokens=200_000, current_tokens=12_500))

    assert str(widget.render()) == "12k/200k tokens (6%)"


def test_context_progress_uses_compact_integer_k_format() -> None:
    widget = ContextProgress()

    widget.watch_tokens(TokenState(max_tokens=568_000, current_tokens=170_000))

    assert str(widget.render()) == "170k/568k tokens (30%)"


def test_context_progress_uses_compact_integer_m_format() -> None:
    widget = ContextProgress()

    widget.watch_tokens(TokenState(max_tokens=40_000_000, current_tokens=35_900_000))

    assert str(widget.render()) == "35.9M/40.0M tokens (90%)"


def test_context_progress_renders_model_name() -> None:
    widget = ContextProgress()

    widget.watch_tokens(
        TokenState(
            max_tokens=200_000, current_tokens=10_000, model_name="mistral-large-latest"
        )
    )

    assert str(widget.render()) == "10k/200k tokens (5%) | [mistral-large-latest]"


def test_context_progress_renders_cost_and_memory() -> None:
    widget = ContextProgress()

    widget.watch_tokens(
        TokenState(
            max_tokens=200_000,
            current_tokens=20_000,
            model_name="dev",
            session_cost=0.035,
            memory_bytes=100 * 1024 * 1024,
            total_memory_bytes=250 * 1024 * 1024,
        )
    )

    dollar = chr(36)
    expected = f"20k/200k tokens (10%) | [dev] | {dollar}0.04 | 100.0M"
    assert str(widget.render()) == expected
