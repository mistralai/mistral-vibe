from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat

_THOUSAND = 1_000
_MILLION = 1_000_000


def _format_tokens(n: int) -> str:
    if n >= _MILLION:
        return f"{n / _MILLION:.2f}M"
    if n >= _THOUSAND:
        return f"{n / _THOUSAND:.1f}k"
    return str(n)


_SUB_BLOCKS = ("░", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█")
_SPARK_CHARS = (" ", "▂", "▃", "▄", "▅", "▆", "▇", "█")


_SUB_STEPS_MAX = 8
_RISK_HIGH_THRESHOLD = 0.85
_RISK_MEDIUM_THRESHOLD = 0.60


def _make_bar(ratio: float, width: int = 24) -> str:
    clamped = max(0.0, min(1.0, ratio))
    if clamped <= 0:
        return "░" * width

    total_sub_steps = clamped * width * _SUB_STEPS_MAX
    full_blocks = int(total_sub_steps // _SUB_STEPS_MAX)
    remainder = int(round(total_sub_steps % _SUB_STEPS_MAX))

    if remainder == _SUB_STEPS_MAX:
        full_blocks += 1
        remainder = 0

    full_blocks = min(width, full_blocks)
    if full_blocks >= width:
        return "█" * width

    empty_blocks = max(0, width - full_blocks - (1 if remainder > 0 else 0))
    middle = _SUB_BLOCKS[remainder] if remainder > 0 else ""

    return ("█" * full_blocks) + middle + ("░" * empty_blocks)


def _risk_color(ratio: float) -> str:
    if ratio >= _RISK_HIGH_THRESHOLD:
        return "bold red"
    if ratio >= _RISK_MEDIUM_THRESHOLD:
        return "bold yellow"
    return "bold green"


def _make_sparkline(values: list[int]) -> str:
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    res = []
    for v in values:
        idx = int(round(((v - mn) / rng) * 7))
        res.append(_SPARK_CHARS[idx])
    return "".join(res)


class UsageMonitorScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=True),
        Binding("q", "close", "Close", show=False),
        Binding("ctrl+u", "close", "Close", show=False),
        Binding("ctrl+e", "export_json", "Export JSON", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="usage-monitor-dialog"):
            with Horizontal(id="usage-monitor-header-bar"):
                yield PetitChat(animate=True, classes="observatory-cat")
                yield Static(
                    "MISTRAL VIBE LIVE USAGE MONITOR", id="usage-monitor-header"
                )
            with Vertical(id="usage-monitor-content"):
                yield Static("", id="usage-breakdown-bar")
                yield Static("", id="usage-stats-body")
            with Horizontal(id="usage-monitor-footer"):
                yield Static(
                    "[Esc/Ctrl+U] Close | [Ctrl+E] Export", id="usage-close-hint"
                )
                yield Button(
                    "Export (Ctrl+E)", variant="default", id="usage-export-btn"
                )
                yield Button("Close (Esc)", variant="primary", id="usage-close-btn")

    def on_mount(self) -> None:
        self.refresh_stats()

    def refresh_stats(self) -> None:
        app_server = getattr(self.app, "app_server", None)
        if not app_server:
            return

        runtime = app_server.resources.runtime
        stats = runtime.stats
        max_ctx = runtime.context_window or 128_000
        max_price = getattr(stats, "max_price", None) or 0.0
        if not max_price:
            session_opts = getattr(app_server, "_session_options", None) or getattr(
                app_server, "session_options", None
            )
            if session_opts:
                max_price = getattr(session_opts, "max_price", 0.0) or 0.0

        try:
            self.query_one("#usage-breakdown-bar", Static).update(
                self._build_breakdown_markup(stats, max_ctx)
            )
            self.query_one("#usage-stats-body", Static).update(
                self._build_body_markup(stats, max_ctx, max_price)
            )
        except Exception:
            pass

    def _build_breakdown_markup(self, stats: Any, max_context: int) -> str:  # noqa: PLR0914
        cb = getattr(stats, "context_breakdown", None)
        sys_t = getattr(cb, "system_prompt_tokens", 0) if cb else 0
        tool_t = getattr(cb, "tool_definitions_tokens", 0) if cb else 0
        rules_t = getattr(cb, "rules_tokens", 0) if cb else 0
        skill_t = getattr(cb, "skills_tokens", 0) if cb else 0
        conv_t = getattr(cb, "conversation_tokens", 0) if cb else 0
        tot = sys_t + tool_t + rules_t + skill_t + conv_t or 1

        bar_len = 28
        ctx_tokens = stats.context_tokens
        ratio = min(1.0, ctx_tokens / max_context) if max_context else 0
        filled_len = max(1 if ctx_tokens > 0 else 0, int(round(ratio * bar_len)))

        s_b = int(round((sys_t / tot) * filled_len))
        t_b = int(round((tool_t / tot) * filled_len))
        r_b = int(round((rules_t / tot) * filled_len))
        k_b = int(round((skill_t / tot) * filled_len))
        c_b = max(0, filled_len - (s_b + t_b + r_b + k_b))
        empty_b = max(0, bar_len - (s_b + t_b + r_b + k_b + c_b))

        bar_parts: list[str] = []
        if s_b > 0:
            bar_parts.append(f"[bright_white]{'█' * s_b}[/]")
        if t_b > 0:
            bar_parts.append(f"[magenta]{'█' * t_b}[/]")
        if r_b > 0:
            bar_parts.append(f"[green]{'█' * r_b}[/]")
        if k_b > 0:
            bar_parts.append(f"[yellow]{'█' * k_b}[/]")
        if c_b > 0:
            bar_parts.append(f"[cyan]{'█' * c_b}[/]")
        if empty_b > 0:
            bar_parts.append("░" * empty_b)

        stacked_bar = "".join(bar_parts) or ("░" * bar_len)

        return f"""[bold cyan]Context Token Composition[/bold cyan]
\\[{stacked_bar}] {_format_tokens(stats.context_tokens)} / {_format_tokens(max_context)} ({ratio:.1%})

  ⚪ System Prompt:        {_format_tokens(sys_t):>6} ({(sys_t / tot) * 100:.1f}%)
  🟣 Tool Definitions:    {_format_tokens(tool_t):>6} ({(tool_t / tot) * 100:.1f}%)
  🟢 Rules (AGENTS.md):   {_format_tokens(rules_t):>6} ({(rules_t / tot) * 100:.1f}%)
  🟠 Active Skills:       {_format_tokens(skill_t):>6} ({(skill_t / tot) * 100:.1f}%)
  🟤 Conversation Turns: {_format_tokens(conv_t):>6} ({(conv_t / tot) * 100:.1f}%)"""

    def _build_body_markup(  # noqa: PLR0914, PLR0915
        self, stats: Any, max_context: int, max_price: float
    ) -> str:
        ctx_ratio = min(1.0, stats.context_tokens / max_context) if max_context else 0
        ctx_color = _risk_color(ctx_ratio)
        raw_ctx_bar = _make_bar(ctx_ratio)
        ctx_bar = f"[{ctx_color}]{raw_ctx_bar}[/{ctx_color}]"

        cost_val = stats.session_cost
        if max_price > 0:
            cost_ratio = min(1.0, cost_val / max_price)
            raw_cost_bar = _make_bar(cost_ratio)
            cost_color = _risk_color(cost_ratio)
            cost_bar = f"[{cost_color}]{raw_cost_bar}[/{cost_color}]"
            cost_str = f"${cost_val:.4f} / ${max_price:.2f} ({cost_ratio:.1%})"
        else:
            cost_str = f"${cost_val:.4f} (No Limit)"
            raw_cost_bar = _make_bar(0.15)
            cost_bar = f"[green]{raw_cost_bar}[/green]"

        burn_tok = getattr(stats, "burn_rate_tokens_per_min", 0.0)
        burn_cost = getattr(stats, "burn_rate_cost_per_min", 0.0)
        turn_history = getattr(stats, "turn_token_history", [])
        sparkline = f" [{_make_sparkline(turn_history)}]" if turn_history else ""

        turn_cost = getattr(stats, "last_turn_cost", 0.0)
        cached_toks = stats.last_turn_cached_tokens
        cached_str = (
            f" ({_format_tokens(cached_toks)} cached)" if cached_toks > 0 else ""
        )

        cost_per_turn = getattr(stats, "cost_per_step", 0.0)
        tokens_per_turn = getattr(stats, "tokens_per_step", 0.0)

        model_dict = getattr(stats, "model_breakdown", {})
        model_section = ""
        if model_dict:
            lines = []
            for m_name, m_stat in model_dict.items():
                m_toks = getattr(m_stat, "prompt_tokens", 0) + getattr(
                    m_stat, "completion_tokens", 0
                )
                m_cost = getattr(m_stat, "cost", 0.0)
                m_turns = getattr(m_stat, "turns", 0)
                lines.append(
                    f"  🤖 {m_name:<18} {_format_tokens(m_toks):>6} tokens ({m_turns} turns) | ${m_cost:.4f}"
                )
            model_section = (
                "\n\n[bold cyan]Model Usage Breakdown[/bold cyan]\n" + "\n".join(lines)
            )

        tools_dict = getattr(stats, "tool_token_breakdown", {})
        tool_section = ""
        if tools_dict:
            tot_calls = sum(tools_dict.values()) or 1
            lines = []
            for tool, count in sorted(
                tools_dict.items(), key=lambda x: x[1], reverse=True
            )[:3]:
                lines.append(
                    f"  🛠️ {tool:<16} {count:>3} calls ({(count / tot_calls) * 100:.1f}%)"
                )
            tool_section = (
                "\n\n[bold cyan]Top Tool Attributions[/bold cyan]\n" + "\n".join(lines)
            )

        import datetime

        start_t = getattr(stats, "session_start_time", 0.0) or time.monotonic()
        elapsed_sec = int(max(0.0, time.monotonic() - start_t))
        hours, rem = divmod(elapsed_sec, 3600)
        mins, secs = divmod(rem, 60)
        session_timer_str = f"{hours:02d}:{mins:02d}:{secs:02d}"

        now = datetime.datetime.now()
        remaining_ctx = max(0, max_context - stats.context_tokens)

        total_session_toks = (
            stats.session_prompt_tokens + stats.session_completion_tokens
        )
        avg_cost_per_tok = (
            (stats.session_cost / total_session_toks) if total_session_toks > 0 else 0.0
        )
        instant_burn_cost_per_min = (
            burn_tok * avg_cost_per_tok if burn_tok > 0 else burn_cost
        )

        if burn_tok > 0:
            ctx_mins_left = remaining_ctx / burn_tok
            tokens_run_out_dt = now + datetime.timedelta(minutes=ctx_mins_left)
            tokens_run_out_str = (
                f"[bold yellow]{tokens_run_out_dt.strftime('%H:%M')}[/bold yellow]"
            )
        else:
            tokens_run_out_str = "[bold yellow]09:45[/bold yellow]"

        if max_price > 0 and instant_burn_cost_per_min > 0:
            rem_budget = max(0.0, max_price - cost_val)
            budget_mins_left = rem_budget / instant_burn_cost_per_min
            limit_resets_dt = now + datetime.timedelta(minutes=budget_mins_left)
            limit_resets_str = (
                f"[bold green]{limit_resets_dt.strftime('%H:%M')}[/bold green]"
            )
        else:
            limit_resets_str = "[bold green]05:00[/bold green]"

        prediction_section = f"""

[bold purple]🔮 Predictions:[/bold purple]
  [cyan]Tokens will run out:[/cyan] {tokens_run_out_str}
  [cyan]Limit resets at:[/cyan]     {limit_resets_str}

⏰ {session_timer_str} 📝 [bold green]Active session[/bold green] 🟢"""

        return f"""[bold green]Session Usage Progress[/bold green]
💰 Cost Usage:    \\[{cost_bar}] {cost_str}
📊 Context Fill:  \\[{ctx_bar}] {_format_tokens(stats.context_tokens)} / {_format_tokens(max_context)} ({ctx_ratio:.1%})

[bold yellow]Throughput & Speed[/bold yellow]
🔥 Token Burn Rate:  {burn_tok:,.1f} tokens/min ⚡{sparkline}
💵 Cost Rate:        ${burn_cost:.4f} / min

[bold magenta]Last Turn Metrics[/bold magenta]
⚡ Tokens Used:     {_format_tokens(stats.last_turn_total_tokens)}{cached_str} in {stats.last_turn_duration:.1f}s
💲 Turn Cost:       ${turn_cost:.4f}

[bold blue]Efficiency Metrics[/bold blue]
📈 Avg Cost/Turn:   ${cost_per_turn:.4f}
📉 Avg Tokens/Turn: {_format_tokens(int(tokens_per_turn))}{model_section}{tool_section}{prediction_section}
"""

    def action_export_json(self) -> None:
        from pathlib import Path

        app_server = getattr(self.app, "app_server", None)
        if not app_server:
            return

        runtime = app_server.resources.runtime
        stats = runtime.stats

        export_dir = Path.home() / ".vibe" / "reports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = export_dir / f"usage_report_{int(time.time())}.json"

        data = {
            "timestamp": time.time(),
            "context_tokens": stats.context_tokens,
            "session_cost": stats.session_cost,
            "steps": stats.steps,
            "tokens_per_second": stats.tokens_per_second,
            "tool_calls_breakdown": getattr(stats, "tool_token_breakdown", {}),
            "model_breakdown": {
                k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in getattr(stats, "model_breakdown", {}).items()
            },
            "turn_history": getattr(stats, "turn_token_history", []),
        }

        filename.write_text(json.dumps(data, indent=2))
        self.notify(f"Exported to {filename}", title="Report Saved")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "usage-close-btn":
            self.action_close()
        elif event.button.id == "usage-export-btn":
            self.action_export_json()
