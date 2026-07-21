from __future__ import annotations

import html
import textwrap

from vibe.core.pawgress.events import Criterion, IslandState, IslandStatus

ORANGE = "#FF8205"
GREEN = "#2ecc71"
RED = "#e74c3c"
AMBER = "#f1c40f"
BLUE = "#5aa9e6"
MUTED = "#8a8a8a"
FG = "#e6e6e6"


_STATE_STYLE: dict[IslandStatus, tuple[str, str, str]] = {
    IslandStatus.PREPARING: ("planning the goal", "…", BLUE),
    IslandStatus.WORKING: ("working", "", ORANGE),
    IslandStatus.VERIFYING: ("verifying", "[...]", BLUE),
    IslandStatus.WAITING: ("Vibe needs you", "!", AMBER),
    IslandStatus.BLOCKED: ("blocked", "✗", RED),
    IslandStatus.PAUSED: ("paused", "zZ", MUTED),
    IslandStatus.COMPLETED: ("complete", "✓", GREEN),
}


_WRAP_WIDTH = 62
_WRAP_MAX_LINES = 3


def _wrap_text(
    text: str, width: int = _WRAP_WIDTH, max_lines: int = _WRAP_MAX_LINES
) -> list[str]:
    """Wrap a single logical line into at most max_lines rows.

    The overlay renders every space as &nbsp; (Qt rich text collapses runs of
    spaces), so Qt can never soft-wrap these rows itself — an unbounded line
    would blow the window width and get cut mid-word at the screen edge.
    """
    normalized = " ".join(text.split())
    if len(normalized) <= width:
        return [normalized]
    lines = textwrap.wrap(
        normalized,
        width=width,
        max_lines=max_lines,
        placeholder="…",
        break_long_words=True,
        break_on_hyphens=False,
    )
    return lines or [normalized[: width - 1] + "…"]


def _parse_fraction(text: str | None) -> tuple[int, int] | None:
    if not text or "/" not in text:
        return None
    left, _, right = text.partition("/")
    if left.strip().isdigit() and right.strip().isdigit():
        return int(left), int(right)
    return None


def progress_bar(current: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, round(width * current / total))
    return "█" * filled + "░" * (width - filled)


def _pct(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(100 * current / total)))


def _format_duration(seconds: int) -> str:
    seconds = max(0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _reset_suffix(state: IslandState, age_seconds: int) -> str:
    if state.usage_reset_seconds is None:
        return ""
    remaining = max(0, state.usage_reset_seconds - age_seconds)
    return f" (resets in {_format_duration(remaining)})"


def context_usage_line(state: IslandState, age_seconds: int = 0) -> str:
    ctx = state.context_tokens or 0
    ctx_max = state.context_max or 0
    used = state.usage_used or 0
    usage_max = state.usage_limit or 0
    line = f"Context {progress_bar(ctx, ctx_max, 6)} {_pct(ctx, ctx_max)}%"
    line += f" │ Usage {progress_bar(used, usage_max, 6)} {_pct(used, usage_max)}%"
    if state.usage_limit:
        line += _reset_suffix(state, age_seconds)
    return line


def _verification_fraction(state: IslandState) -> tuple[int, int] | None:
    for criterion in state.criteria:
        frac = _parse_fraction(criterion.progress)
        if frac is not None:
            return frac
    return None


def _criterion_marker(criterion: Criterion) -> str:
    if criterion.done:
        return "✓"
    if criterion.progress:
        return "●"
    return "○"


def _meta_line(state: IslandState) -> str:
    parts: list[str] = []
    if state.iteration:
        parts.append(f"iter {state.iteration}")
    if state.cost is not None and state.budget is not None:
        parts.append(f"${state.cost:.2f}/${state.budget:.2f}")
    return " · ".join(parts)


def render_island(state: IslandState, cat_frame: str) -> str:
    label, symbol, _ = _STATE_STYLE[state.state]
    lines: list[str] = []
    head = f"\U0001f43e Pawgress · {label}"
    if symbol:
        head += f"  {symbol}"
    lines.append(head)
    lines.append(state.goal)

    frac = _verification_fraction(state)
    bar = f"   {progress_bar(*frac)} {frac[0]}/{frac[1]}" if frac is not None else ""
    cat_lines = cat_frame.splitlines() or [""]
    mid = len(cat_lines) // 2
    for i, cat_line in enumerate(cat_lines):
        lines.append(f" {cat_line}{bar if i == mid else ''}")

    for criterion in state.criteria:
        suffix = (
            f"  {criterion.progress}"
            if criterion.progress and not criterion.done
            else ""
        )
        lines.append(f"{_criterion_marker(criterion)} {criterion.label}{suffix}")

    lines.append(context_usage_line(state))

    meta = _meta_line(state)
    if meta:
        lines.append(meta)

    if state.state is IslandStatus.COMPLETED:
        for item in state.evidence:
            lines.append(f"✓ {item}")

    if state.state is IslandStatus.WAITING:
        lines.append("[Open Vibe]")
    else:
        lines.append("[Pause] [Stop] [Open Vibe]")
    return "\n".join(lines)


def render_collapsed(state: IslandState) -> str:
    label, symbol, _ = _STATE_STYLE[state.state]
    frac = _verification_fraction(state)
    tail = f" {frac[0]}/{frac[1]}" if frac is not None else ""
    sym = f" {symbol}" if symbol else ""
    return f"\U0001f43e Pawgress · {label}{tail}{sym}"


EMPTY_BAR = "#3d3d42"


def _span(text: str, color: str) -> str:
    escaped = html.escape(text).replace(" ", "&nbsp;")
    return f'<span style="color:{color}">{escaped}</span>'


def _bar_html(current: int, total: int, color: str, width: int = 10) -> str:
    filled = 0 if total <= 0 else min(width, round(width * current / total))
    return _span("█" * filled, color) + _span("█" * (width - filled), EMPTY_BAR)


def context_usage_html(state: IslandState, age_seconds: int = 0) -> str:
    ctx = state.context_tokens or 0
    ctx_max = state.context_max or 0
    used = state.usage_used or 0
    usage_max = state.usage_limit or 0
    row = (
        _span("Context ", MUTED)
        + _bar_html(ctx, ctx_max, GREEN, width=6)
        + _span(f" {_pct(ctx, ctx_max)}%", GREEN)
        + _span(" │ ", MUTED)
        + _span("Usage ", MUTED)
        + _bar_html(used, usage_max, BLUE, width=6)
        + _span(f" {_pct(used, usage_max)}%", BLUE)
    )
    if state.usage_limit:
        row += _span(_reset_suffix(state, age_seconds), MUTED)
    return row


_DECOR_FRAMES: dict[IslandStatus, tuple[str, ...]] = {
    IslandStatus.PREPARING: ("·  ", "·· ", "···"),
    IslandStatus.WORKING: ("✦", "✧", " "),
    IslandStatus.VERIFYING: ("[.  ]", "[.. ]", "[...]"),
    IslandStatus.WAITING: ("!", " "),
    IslandStatus.BLOCKED: ("✗",),
    IslandStatus.PAUSED: ("zZ", "z "),
    IslandStatus.COMPLETED: ("+ ✦ +", " ✦✦  "),
}
_RETRY_FRAMES = ("↻", "⟳")


def _is_retrying(state: IslandState) -> bool:
    frac = _verification_fraction(state)
    return (
        state.state is IslandStatus.WORKING
        and frac is not None
        and 0 < frac[0] < frac[1]
    )


def _decoration(state: IslandState, tick: int) -> str:
    if _is_retrying(state):
        return _RETRY_FRAMES[(tick // 3) % len(_RETRY_FRAMES)]
    frames = _DECOR_FRAMES[state.state]
    return frames[(tick // 3) % len(frames)]


def render_island_html(
    state: IslandState,
    cat_frame: str,
    tick: int = 0,
    with_buttons: bool = True,
    age_seconds: int = 0,
) -> str:
    label, symbol, color = _STATE_STYLE[state.state]
    if _is_retrying(state):
        label, color = "trying another fix", BLUE
    rows: list[str] = []
    head = f"\U0001f43e Pawgress · {label}"
    if symbol:
        head += f"  {symbol}"
    rows.append(_span(head, color) + _span("  ", FG) + _button("[×]", "quit", MUTED))
    rows.append(_span(" ", FG))
    for goal_line in _wrap_text(state.goal, max_lines=2):
        rows.append(_span(goal_line, FG))
    if state.detail:
        detail_color = color if state.state is IslandStatus.WAITING else MUTED
        for detail_line in _wrap_text(state.detail):
            rows.append(_span(detail_line, detail_color))
    rows.append(_span(" ", FG))

    cat_lines = cat_frame.splitlines() or [""]
    decor = _span("  ", FG) + _span(_decoration(state, tick), color)
    for i, cat_line in enumerate(cat_lines):
        suffix = decor if i == 0 else ""
        rows.append(_span(f" {cat_line}", ORANGE) + suffix)
    rows.append(_span(" ", FG))

    for criterion in state.criteria:
        marker = _criterion_marker(criterion)
        mark_color = (
            GREEN if criterion.done else (color if criterion.progress else MUTED)
        )
        suffix = (
            f"  {criterion.progress}"
            if criterion.progress and not criterion.done
            else ""
        )
        rows.append(
            _span(marker, mark_color) + " " + _span(criterion.label + suffix, FG)
        )

    rows.append(_span(" ", FG))
    rows.append(context_usage_html(state, age_seconds))

    meta = _meta_line(state)
    if meta:
        rows.append(_span(meta, MUTED))

    if state.state is IslandStatus.COMPLETED:
        for item in state.evidence:
            rows.append(_span(f"✓ {item}", GREEN))

    if with_buttons:
        rows.append(buttons_html(state))
    return "<br>".join(rows)


def _button(label: str, href: str, color: str) -> str:
    return f'<a href="{href}" style="color:{color};text-decoration:none">{html.escape(label)}</a>'


_BUTTON_GAP = "&nbsp;" * 4


_STATUS_DOT: dict[IslandStatus, str] = {
    IslandStatus.PREPARING: BLUE,
    IslandStatus.WORKING: ORANGE,
    IslandStatus.VERIFYING: BLUE,
    IslandStatus.WAITING: AMBER,
    IslandStatus.BLOCKED: RED,
    IslandStatus.PAUSED: MUTED,
    IslandStatus.COMPLETED: GREEN,
}


def _truncate(text: str, width: int) -> str:
    return f"{text[: width - 1]}…" if len(text) > width else text


def _row_tags(model: str, terminal: str) -> str:
    parts = [p for p in (model, terminal) if p]
    return " · ".join(parts)


def directory_row_html(
    sid: str,
    label: str,
    status: IslandStatus | None,
    model: str,
    terminal: str,
    elapsed: str | None,
    active: bool,
) -> str:
    dot_color = _STATUS_DOT.get(status, MUTED) if status is not None else MUTED
    name_color = FG if active else "#c8c8c8"
    tags = _row_tags(model, terminal)
    caret = _span("▸ " if active else "  ", ORANGE)
    row = (
        caret
        + _span("● ", dot_color)
        + _span(_truncate(label, 32), name_color)
        + _span("   ", FG)
    )
    if tags:
        row += _span(tags + "  ", MUTED)
    if elapsed:
        row += _span(elapsed + " ", MUTED)
    # Whole row is a click target that opens the session's detail slide.
    return f'<a href="row:{sid}" style="text-decoration:none">{row} ›</a>'


def detail_nav_html(index: int, total: int) -> str:
    """Detail header: a back-to-directory button + position (i/total)."""
    back = _button("‹ all", "open_directory", FG)
    pos = _span(f"   {index + 1}/{total}", MUTED)
    return back + pos


def buttons_html(state: IslandState) -> str:
    if state.state is IslandStatus.WAITING:
        return _BUTTON_GAP.join([
            _button("[Allow once]", "allow_once", GREEN),
            _button("[Session]", "allow_session", BLUE),
            _button("[Always]", "allow_always", AMBER),
            _button("[Deny]", "deny", RED),
        ])
    first = (
        _button("[Resume]", "resume", GREEN)
        if state.state is IslandStatus.PAUSED
        else _button("[Pause]", "pause", MUTED)
    )
    return _BUTTON_GAP.join([
        first,
        _button("[Stop]", "stop", RED),
        _button("[Open Vibe]", "focus_vibe", BLUE),
    ])
