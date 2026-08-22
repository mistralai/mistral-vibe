"""Cross-session cost and token usage aggregation from local session logs.

Reads ``meta.json`` files under the session save directory. Stateless and
UI-free so it is easy to unit test. Does not write telemetry or mutate logs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import re
from typing import Any

from vibe.utils.io import read_safe
from vibe.utils.pricing import session_token_cost

METADATA_FILENAME = "meta.json"
UNPINNED_MODEL_LABEL = "default (unpinned)"

_BAR_CHARS = "▁▂▃▄▅▆▇█"
_WINDOW_RE = re.compile(r"^(\d+)d$", re.IGNORECASE)
_USAGE_HELP = (
    "Usage: `/usage [7d|30d|all]` optional `--csv <path>` "
    "optional `--insight` / `--no-insight`"
)


@dataclass(frozen=True)
class UsageBucket:
    """Aggregated cost and token counts for one day or one model."""

    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    sessions: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(
        self,
        *,
        cost: float,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int,
    ) -> UsageBucket:
        return UsageBucket(
            cost=self.cost + cost,
            prompt_tokens=self.prompt_tokens + prompt_tokens,
            completion_tokens=self.completion_tokens + completion_tokens,
            cached_tokens=self.cached_tokens + cached_tokens,
            sessions=self.sessions + 1,
        )


@dataclass
class UsageReport:
    """Cross-session usage snapshot."""

    by_day: dict[str, UsageBucket] = field(default_factory=dict)
    by_model: dict[str, UsageBucket] = field(default_factory=dict)
    total: UsageBucket = field(default_factory=UsageBucket)
    session_count: int = 0
    skipped_files: int = 0
    since: date | None = None
    save_dir: Path | None = None


@dataclass(frozen=True)
class UsageCommandArgs:
    """Parsed `/usage` arguments."""

    since: date | None
    csv_path: Path | None
    insight: bool
    error: str | None = None


def parse_usage_window(raw: str, *, today: date | None = None) -> date | None:
    """Parse a window token into an inclusive lower-bound date.

    Accepted values: ``7d``, ``30d``, ``all`` (or empty → no lower bound).
    Raises ``ValueError`` for anything else.
    """
    token = raw.strip().lower()
    if not token or token == "all":
        return None
    match = _WINDOW_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"Unknown usage window {raw!r}. Use 7d, 30d, or all.")
    days = int(match.group(1))
    if days <= 0:
        raise ValueError(f"Usage window must be a positive day count, got {raw!r}.")
    ref = today or datetime.now(UTC).date()
    return ref - timedelta(days=days - 1)


def parse_usage_args(cmd_args: str, *, today: date | None = None) -> UsageCommandArgs:
    """Parse `/usage` args: optional window, optional ``--csv path``, insight flags."""
    tokens = cmd_args.split()
    since: date | None = None
    csv_path: Path | None = None
    insight = False
    window_seen = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"--csv", "-o"}:
            if i + 1 >= len(tokens):
                return UsageCommandArgs(
                    since=None,
                    csv_path=None,
                    insight=insight,
                    error=f"`{token}` requires a file path. {_USAGE_HELP}",
                )
            csv_path = Path(tokens[i + 1]).expanduser()
            i += 2
            continue
        if token == "--insight":
            insight = True
            i += 1
            continue
        if token == "--no-insight":
            insight = False
            i += 1
            continue
        if token.startswith("-"):
            return UsageCommandArgs(
                since=None,
                csv_path=None,
                insight=insight,
                error=f"Unknown flag {token!r}. {_USAGE_HELP}",
            )
        if window_seen:
            return UsageCommandArgs(
                since=None,
                csv_path=None,
                insight=insight,
                error=f"Unexpected argument {token!r}. {_USAGE_HELP}",
            )
        try:
            since = parse_usage_window(token, today=today)
        except ValueError as exc:
            return UsageCommandArgs(
                since=None, csv_path=None, insight=insight, error=f"{exc} {_USAGE_HELP}"
            )
        window_seen = True
        i += 1
    return UsageCommandArgs(since=since, csv_path=csv_path, insight=insight)


def _model_label(config: Any) -> str:
    if not isinstance(config, dict):
        return UNPINNED_MODEL_LABEL
    active = config.get("active_model")
    if not isinstance(active, str) or not active.strip():
        return UNPINNED_MODEL_LABEL
    return active.strip()


def _day_key(start_time: Any) -> str | None:
    if not isinstance(start_time, str) or not start_time:
        return None
    try:
        dt = datetime.fromisoformat(start_time)
    except ValueError:
        # Fall back to the YYYY-MM-DD prefix when present.
        prefix = start_time[:10]
        try:
            date.fromisoformat(prefix)
        except ValueError:
            return None
        return prefix
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).date().isoformat()


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _session_cost_from_stats(stats: dict[str, Any]) -> float:
    stored = _as_float(stats.get("session_cost"))
    if stored is not None and stored >= 0:
        return stored
    prompt = _as_int(stats.get("session_prompt_tokens"))
    completion = _as_int(stats.get("session_completion_tokens"))
    cached = _as_int(stats.get("session_cached_tokens"))
    input_price = _as_float(stats.get("input_price_per_million")) or 0.0
    output_price = _as_float(stats.get("output_price_per_million")) or 0.0
    cached_price = _as_float(stats.get("cached_input_price_per_million"))
    return session_token_cost(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        input_price_per_million=input_price,
        output_price_per_million=output_price,
        cached_input_price_per_million=cached_price,
    )


def build_usage_report(
    save_dir: Path, session_prefix: str = "session", *, since: date | None = None
) -> UsageReport:
    """Aggregate cost/tokens from every ``<prefix>_*/meta.json`` under *save_dir*."""
    report = UsageReport(since=since, save_dir=save_dir)
    if not save_dir.exists():
        return report

    for meta_path in sorted(save_dir.glob(f"{session_prefix}_*/{METADATA_FILENAME}")):
        try:
            meta = json.loads(read_safe(meta_path).text)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            report.skipped_files += 1
            continue
        if not isinstance(meta, dict):
            report.skipped_files += 1
            continue

        day = _day_key(meta.get("start_time"))
        if day is None:
            report.skipped_files += 1
            continue
        if since is not None and date.fromisoformat(day) < since:
            continue

        stats = meta.get("stats")
        if not isinstance(stats, dict):
            stats = {}

        cost = _session_cost_from_stats(stats)
        prompt = _as_int(stats.get("session_prompt_tokens"))
        completion = _as_int(stats.get("session_completion_tokens"))
        cached = _as_int(stats.get("session_cached_tokens"))
        model = _model_label(meta.get("config"))

        report.by_day[day] = report.by_day.get(day, UsageBucket()).add(
            cost=cost,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
        )
        report.by_model[model] = report.by_model.get(model, UsageBucket()).add(
            cost=cost,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
        )
        report.total = report.total.add(
            cost=cost,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
        )
        report.session_count += 1

    return report


def _bar(value: float, maximum: float, *, width: int = 8) -> str:
    if maximum <= 0 or value <= 0 or width <= 0:
        return _BAR_CHARS[0] * width
    ratio = min(value / maximum, 1.0)
    filled = max(1, int(round(ratio * width)))
    level = min(len(_BAR_CHARS) - 1, int(ratio * (len(_BAR_CHARS) - 1) + 1e-9))
    return (_BAR_CHARS[level] * filled).ljust(width, _BAR_CHARS[0])


def _horizontal_bar(value: float, maximum: float, *, width: int = 24) -> str:
    """Solid unicode bar for TUI-friendly charts (█ filled, ░ empty)."""
    if width <= 0:
        return ""
    if maximum <= 0 or value <= 0:
        return "░" * width
    filled = max(1, int(round(min(value / maximum, 1.0) * width)))
    return ("█" * filled) + ("░" * (width - filled))


def _chart_block(
    title: str, rows: list[tuple[str, float, UsageBucket]], *, label_width: int
) -> list[str]:
    """Render a fenced horizontal bar chart — displays reliably in the TUI."""
    if not rows:
        return []
    maximum = max((cost for _, cost, _ in rows), default=0.0)
    lines = [f"### {title}", "", "```"]
    for label, cost, bucket in rows:
        bar = _horizontal_bar(cost, maximum)
        padded = label[:label_width].ljust(label_width)
        lines.append(
            f"{padded}  ${cost:>8.4f}  {bar}  "
            f"({bucket.sessions} sess, {bucket.total_tokens:,} tok)"
        )
    lines.extend(["```", ""])
    return lines


def _window_label(since: date | None) -> str:
    if since is None:
        return "all time"
    return f"since {since.isoformat()}"


def format_usage_markdown(report: UsageReport, *, insight: str | None = None) -> str:
    """Render a camera-friendly markdown usage report with unicode bars."""
    lines = [
        "## Usage Report",
        "",
        f"_Local session logs · {_window_label(report.since)}_",
        "",
    ]
    if report.session_count == 0:
        lines.extend([
            "No saved sessions with usage data found.",
            "",
            "Run a few chats, then try `/usage` again. "
            "History is read-only from your local session logs.",
        ])
        return "\n".join(lines)

    total = report.total
    lines.extend([
        f"- **Sessions**: {report.session_count:,}",
        f"- **Total cost**: ${total.cost:.4f}",
        f"- **Prompt tokens**: {total.prompt_tokens:,}",
        f"- **Completion tokens**: {total.completion_tokens:,}",
        f"- **Total tokens**: {total.total_tokens:,}",
    ])
    if total.cached_tokens:
        lines.append(f"- **Cached tokens**: {total.cached_tokens:,}")
    if report.skipped_files:
        lines.append(f"- **Skipped unreadable logs**: {report.skipped_files:,}")
    lines.append("")

    day_rows = [
        (day, report.by_day[day].cost, report.by_day[day])
        for day in sorted(report.by_day)
    ]
    model_rows = [
        (model, bucket.cost, bucket)
        for model, bucket in sorted(
            report.by_model.items(), key=lambda item: (-item[1].cost, item[0])
        )
    ]
    # Charts first: fenced code blocks render more reliably than markdown tables
    # in the Textual TUI, and they give a clear visual for demos.
    lines.extend(_chart_block("Cost by day", day_rows, label_width=12))
    lines.extend(_chart_block("Cost by model", model_rows, label_width=22))

    max_day_cost = max((b.cost for b in report.by_day.values()), default=0.0)
    lines.extend([
        "### By day",
        "",
        "| Day | Cost | Sessions | Tokens | Trend |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for day in sorted(report.by_day):
        bucket = report.by_day[day]
        lines.append(
            f"| {day} | ${bucket.cost:.4f} | {bucket.sessions:,} | "
            f"{bucket.total_tokens:,} | `{_bar(bucket.cost, max_day_cost)}` |"
        )
    lines.append("")

    max_model_cost = max((b.cost for b in report.by_model.values()), default=0.0)
    lines.extend([
        "### By model",
        "",
        "| Model | Cost | Sessions | Tokens | Trend |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for model, bucket in sorted(
        report.by_model.items(), key=lambda item: (-item[1].cost, item[0])
    ):
        lines.append(
            f"| {model} | ${bucket.cost:.4f} | {bucket.sessions:,} | "
            f"{bucket.total_tokens:,} | `{_bar(bucket.cost, max_model_cost)}` |"
        )

    if insight:
        lines.extend(["", "### Insight", "", insight])

    lines.extend([
        "",
        "_Tip: `/usage 7d`, `/usage 30d`, `/usage all`, "
        "`/usage --csv ~/usage.csv`, `/usage --insight`._",
    ])
    return "\n".join(lines)


def format_usage_csv(report: UsageReport) -> str:
    """Serialize day and model buckets as CSV for spreadsheet export."""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "dimension",
        "key",
        "cost_usd",
        "sessions",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "total_tokens",
    ])
    for day in sorted(report.by_day):
        bucket = report.by_day[day]
        writer.writerow([
            "day",
            day,
            f"{bucket.cost:.6f}",
            bucket.sessions,
            bucket.prompt_tokens,
            bucket.completion_tokens,
            bucket.cached_tokens,
            bucket.total_tokens,
        ])
    for model, bucket in sorted(
        report.by_model.items(), key=lambda item: (-item[1].cost, item[0])
    ):
        writer.writerow([
            "model",
            model,
            f"{bucket.cost:.6f}",
            bucket.sessions,
            bucket.prompt_tokens,
            bucket.completion_tokens,
            bucket.cached_tokens,
            bucket.total_tokens,
        ])
    writer.writerow([
        "total",
        "all",
        f"{report.total.cost:.6f}",
        report.session_count,
        report.total.prompt_tokens,
        report.total.completion_tokens,
        report.total.cached_tokens,
        report.total.total_tokens,
    ])
    return buffer.getvalue()


def _insight_prompt(report: UsageReport) -> str:
    day_lines = [
        f"- {day}: ${bucket.cost:.4f} across {bucket.sessions} session(s), "
        f"{bucket.total_tokens:,} tokens"
        for day, bucket in sorted(report.by_day.items())
    ]
    model_lines = [
        f"- {model}: ${bucket.cost:.4f} across {bucket.sessions} session(s), "
        f"{bucket.total_tokens:,} tokens"
        for model, bucket in sorted(
            report.by_model.items(), key=lambda item: (-item[1].cost, item[0])
        )
    ]
    return (
        "You are a concise spend analyst for a coding CLI.\n"
        "Given local, already-aggregated session usage, write ONE plain-English "
        "insight sentence (max 40 words). Mention the standout day or model if "
        "useful. No markdown, no bullet points, no hedging.\n\n"
        f"Window: {_window_label(report.since)}\n"
        f"Sessions: {report.session_count}\n"
        f"Total cost: ${report.total.cost:.4f}\n"
        f"Total tokens: {report.total.total_tokens:,}\n\n"
        "By day:\n"
        + ("\n".join(day_lines) or "- (none)")
        + "\n\nBy model:\n"
        + ("\n".join(model_lines) or "- (none)")
    )


def generate_usage_insight(
    report: UsageReport,
    *,
    api_key: str | None = None,
    model: str = "mistral-small-latest",
) -> str | None:
    """Ask a Mistral model for a one-line spend takeaway. Returns None on skip/fail."""
    text: str | None = None
    if report.session_count > 0:
        key = api_key if api_key is not None else os.environ.get("MISTRAL_API_KEY")
        if key:
            try:
                from mistralai.client import Mistral

                client = Mistral(api_key=key)
                response = client.chat.complete(
                    model=model,
                    messages=[{"role": "user", "content": _insight_prompt(report)}],
                    temperature=0.2,
                    max_tokens=80,
                )
                choices = getattr(response, "choices", None) or []
                if choices:
                    message = getattr(choices[0], "message", None)
                    content = getattr(message, "content", None)
                    if isinstance(content, str):
                        text = " ".join(content.strip().split()) or None
            except Exception:
                text = None
    return text
