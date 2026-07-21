from __future__ import annotations

import html as html_lib

from vibe.core.pawgress.events import IslandState, IslandStatus
from vibe.overlay.render import (
    _pct,
    _wrap_text,
    context_usage_html,
    context_usage_line,
    detail_nav_html,
    directory_row_html,
    render_island,
    render_island_html,
)


def make_state(**kwargs) -> IslandState:
    base: dict[str, object] = {"goal": "g", "state": IslandStatus.WORKING}
    base.update(kwargs)
    return IslandState.model_validate(base)


def test_pct_clamps_and_rounds():
    assert _pct(0, 0) == 0
    assert _pct(7, 100) == 7
    assert _pct(150, 100) == 100
    assert _pct(-5, 100) == 0
    assert _pct(1, 3) == 33


def test_line_without_any_data_shows_zero_context_and_usage():
    line = context_usage_line(make_state())
    assert line == "Context ░░░░░░ 0% │ Usage ░░░░░░ 0%"


def test_line_shows_usage_placeholder_without_rate_limit_data():
    line = context_usage_line(make_state(context_tokens=14_000, context_max=200_000))
    assert line.startswith("Context ")
    assert "7%" in line
    assert "Usage" in line  # always visible; 0% when no rate-limit data
    assert "0%" in line


def test_line_with_usage_and_reset():
    line = context_usage_line(
        make_state(
            context_tokens=14_000,
            context_max=200_000,
            usage_used=170_000,
            usage_limit=500_000,
            usage_reset_seconds=40,
        )
    )
    assert "Context" in line and "7%" in line
    assert "Usage" in line and "34%" in line
    assert "(resets in 40s)" in line


def test_reset_formats_hours_and_minutes():
    line = context_usage_line(
        make_state(usage_used=63, usage_limit=100, usage_reset_seconds=10_980)
    )
    assert "(resets in 3h 3m)" in line


def test_reset_countdown_ages_and_floors_at_zero():
    state = make_state(usage_used=1, usage_limit=10, usage_reset_seconds=40)
    assert "(resets in 25s)" in context_usage_line(state, age_seconds=15)
    assert "(resets in 0s)" in context_usage_line(state, age_seconds=999)


def test_directory_row_links_by_sid_and_shows_tags():
    html = directory_row_html(
        sid="s1",
        label="Fix the bug",
        status=IslandStatus.WORKING,
        model="mistral",
        terminal="Ghostty",
        elapsed="28m",
        active=True,
    )
    assert 'href="row:s1"' in html
    # spaces are nbsp-encoded in HTML, so match single tokens
    assert "Fix" in html and "bug" in html
    assert "mistral" in html and "Ghostty" in html
    assert "28m" in html


def test_directory_row_truncates_long_label():
    html = directory_row_html(
        sid="s1",
        label="x" * 50,
        status=None,
        model="",
        terminal="",
        elapsed=None,
        active=False,
    )
    assert "…" in html


def test_detail_nav_shows_position_and_back_to_directory():
    html = detail_nav_html(index=0, total=3)
    assert "1/3" in html
    assert 'href="open_directory"' in html


def test_preparing_state_renders_without_error():
    state = make_state(
        state=IslandStatus.PREPARING, detail="Drafting an acceptance test…"
    )
    # HTML renderer nbsp-encodes spaces, so match on a single word.
    html = render_island_html(state, "cat", tick=0)
    assert "planning" in html
    # plain-text renderer must also handle the new status
    text = render_island(state, "cat")
    assert "planning the goal" in text


def test_render_island_includes_context_line():
    text = render_island(make_state(context_tokens=14_000, context_max=200_000), "cat")
    assert "Context" in text
    assert "7%" in text


def test_wrap_text_short_passthrough():
    assert _wrap_text("bash: pytest -q") == ["bash: pytest -q"]


def test_wrap_text_wraps_and_ellipsizes():
    text = "x" * 300
    lines = _wrap_text(text, width=62, max_lines=3)
    assert len(lines) == 3
    assert all(len(line) <= 62 for line in lines)
    assert lines[-1].endswith("…")


def test_wrap_text_collapses_newlines():
    assert _wrap_text("a\nb\t c") == ["a b c"]


def test_long_approval_detail_is_wrapped_not_cut():
    command = (
        "bash: cd '/Users/yongkangzou/Desktop/Hackathons/Mistral Vibe 2026/"
        "mistral-vibe' && uv run pytest tests/pawgress -q"
    )
    html = render_island_html(
        make_state(state=IslandStatus.WAITING, detail=command), "cat"
    )
    # The meaningful tail of the command survives (nbsp-encoded spaces).
    assert "pytest" in html
    # No single rendered detail row exceeds the wrap width.
    for row in html.split("<br>"):
        if "yongkangzou" in row:
            raw = row.split(">", 1)[-1].split("<", 1)[0]
            visible = html_lib.unescape(raw)
            assert len(visible) <= 62


def test_html_variants():
    html_row = context_usage_html(
        make_state(
            context_tokens=14_000,
            context_max=200_000,
            usage_used=170_000,
            usage_limit=500_000,
            usage_reset_seconds=40,
        )
    )
    assert "Context" in html_row and "7%" in html_row
    assert "Usage" in html_row and "34%" in html_row
    full = render_island_html(
        make_state(context_tokens=14_000, context_max=200_000), "cat"
    )
    assert "Context" in full
