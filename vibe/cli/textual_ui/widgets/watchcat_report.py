from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import TabbedContent, TabPane, Tabs

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.watchdog.demo_report import DemoReport, DemoRunReport, render_demo_runs

CLASSIFICATIONS = ("protected", "mitigated", "blocked", "degraded")
CLASS_STYLES = {
    "protected": "cyan",
    "mitigated": "green",
    "blocked": "yellow",
    "degraded": "red",
}


class WatchcatReportApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False, priority=True)
    ]

    class Closed(Message):
        pass

    def __init__(self, report: DemoReport) -> None:
        super().__init__(id="watchcatreport-app")
        self._report = report

    def compose(self) -> ComposeResult:
        categories = {
            classification: [
                run for run in self._report.runs if run.classification == classification
            ]
            for classification in CLASSIFICATIONS
        }
        with Vertical(id="watchcat-report-content"):  # noqa: PLR1702
            yield NoMarkupStatic(self._header(), classes="watchcat-report-title")
            with TabbedContent(initial="watchcat-summary", id="watchcat-report-tabs"):
                with TabPane("Overview", id="watchcat-summary"):
                    yield from self._compose_overview(categories)
                with TabPane("Details", id="watchcat-details"):
                    with VerticalScroll(classes="watchcat-report-scroll"):
                        yield NoMarkupStatic(
                            self._details_header(), classes="watchcat-details-header"
                        )
                        yield NoMarkupStatic(render_demo_runs(self._report.runs))
                for classification, runs in categories.items():
                    title = f"{classification.title()} ({len(runs)})"
                    with TabPane(title, id=f"watchcat-{classification}"):
                        with VerticalScroll(classes="watchcat-report-scroll"):
                            if runs:
                                for run in runs:
                                    yield from self._compose_run_card(run)
                            else:
                                yield NoMarkupStatic(
                                    f"\n  No {classification} runs.",
                                    classes="watchcat-empty-state",
                                )
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('←→')} Change tab  {shortcut('↑↓')} Scroll  "
                    f"{shortcut('Esc')} Close"
                ),
                classes="watchcat-report-help",
            )

    def _compose_overview(
        self, categories: dict[str, list[DemoRunReport]]
    ) -> ComposeResult:
        total = len(self._report.runs)
        with VerticalScroll(classes="watchcat-report-scroll"):
            with Horizontal(classes="watchcat-kpi-row"):
                yield from self._metric("RUNS", total, "total")
                for classification in CLASSIFICATIONS:
                    yield from self._metric(
                        classification.upper(),
                        len(categories[classification]),
                        classification,
                    )
            with Horizontal(classes="watchcat-dashboard-row"):
                with Vertical(classes="watchcat-panel watchcat-distribution-panel"):
                    yield NoMarkupStatic(
                        "CLASSIFICATION", classes="watchcat-panel-title"
                    )
                    for classification in CLASSIFICATIONS:
                        yield NoMarkupStatic(
                            self._classification_bar(
                                classification, len(categories[classification]), total
                            ),
                            classes="watchcat-bar-row",
                        )
                with Vertical(classes="watchcat-panel watchcat-pipeline-panel"):
                    yield NoMarkupStatic(
                        "RECOVERY PIPELINE", classes="watchcat-panel-title"
                    )
                    yield NoMarkupStatic(
                        self._pipeline(), classes="watchcat-pipeline-visual"
                    )
                    recovered = sum(
                        run.incident_state == "closed" for run in self._report.runs
                    )
                    injections = sum(
                        run.context_injections for run in self._report.runs
                    )
                    yield NoMarkupStatic(
                        f"{recovered}/{total} closed   •   {injections} context injections",
                        classes="watchcat-panel-caption",
                    )
            with Vertical(classes="watchcat-panel watchcat-run-map"):
                yield NoMarkupStatic("RUN MAP", classes="watchcat-panel-title")
                for run in self._report.runs:
                    yield NoMarkupStatic(
                        self._run_map_row(run), classes="watchcat-run-map-row"
                    )

    def _metric(self, label: str, value: int, kind: str) -> ComposeResult:
        with Vertical(classes=f"watchcat-metric watchcat-metric-{kind}"):
            yield NoMarkupStatic(str(value), classes="watchcat-metric-value")
            yield NoMarkupStatic(label, classes="watchcat-metric-label")

    def _compose_run_card(self, run: DemoRunReport) -> ComposeResult:
        style = CLASS_STYLES.get(run.classification, "white")
        header = Text()
        header.append("● ", style=f"bold {style}")
        header.append(run.name.upper(), style="bold")
        header.append(f"   {run.outcome.upper()}", style=f"bold {style}")
        with Vertical(classes=f"watchcat-run-card watchcat-card-{run.classification}"):
            yield NoMarkupStatic(header, classes="watchcat-run-card-title")
            yield NoMarkupStatic(run.trigger, classes="watchcat-run-trigger")
            yield NoMarkupStatic(self._flow_visual(run), classes="watchcat-run-flow")
            with Horizontal(classes="watchcat-run-stats"):
                yield NoMarkupStatic(
                    f"SIGNAL\n{run.signal_quality.upper()}", classes="watchcat-run-stat"
                )
                yield NoMarkupStatic(
                    f"INCIDENT\n{run.incident_state.upper()}",
                    classes="watchcat-run-stat",
                )
                yield NoMarkupStatic(
                    f"INJECTION\n{run.context_injections}/{run.injection_attempts}",
                    classes="watchcat-run-stat",
                )
            yield NoMarkupStatic(
                self._trace_visual(run), classes="watchcat-trace-visual"
            )

    def _header(self) -> Text:
        text = Text()
        text.append("WATCHCAT  ", style="bold cyan")
        text.append("DEMO CONTROL CENTER", style="bold")
        text.append(f"   {self._report.report_id}", style="dim")
        text.append(f"   ● {self._report.status.upper()}", style="bold green")
        return text

    def _details_header(self) -> Text:
        text = Text()
        text.append("┌─ FULL TECHNICAL TRACE ", style="bold cyan")
        text.append("─" * 40, style="dim cyan")
        text.append("┐\n│ ", style="dim cyan")
        text.append(
            "trigger • evidence • mitigation • state transitions • artifacts",
            style="bold",
        )
        text.append("\n└", style="dim cyan")
        text.append("─" * 64, style="dim cyan")
        text.append("┘\n")
        return text

    @staticmethod
    def _classification_bar(
        classification: str, count: int, total: int, *, width: int = 18
    ) -> Text:
        filled = round(width * count / total) if total else 0
        style = CLASS_STYLES[classification]
        text = Text(f"{classification.upper():<10} ", style="bold")
        text.append("█" * filled, style=f"bold {style}")
        text.append("░" * (width - filled), style="dim")
        text.append(f"  {count:>2}", style=f"bold {style}")
        return text

    @staticmethod
    def _pipeline() -> Text:
        text = Text()
        stages = (
            ("OBSERVE", "cyan"),
            ("DETECT", "yellow"),
            ("RECOVER", "magenta"),
            ("VERIFY", "green"),
        )
        for index, (label, style) in enumerate(stages):
            if index:
                text.append(" ━━━▶ ", style="dim")
            text.append(f"● {label}", style=f"bold {style}")
        return text

    @staticmethod
    def _run_map_row(run: DemoRunReport) -> Text:
        style = CLASS_STYLES.get(run.classification, "white")
        events = {entry.event for entry in run.trace}
        stages = (
            ("O", "tool_started" in events),
            ("D", "incident_confirmed" in events),
            ("R", "recovery_finished" in events),
            ("V", "verification_finished" in events),
        )
        text = Text(f"{run.name[:20]:<20} ")
        for index, (label, active) in enumerate(stages):
            if index:
                text.append("──", style="dim")
            text.append(f"●{label}", style=style if active else "dim")
        text.append(f"  {run.classification.upper():<10}", style=f"bold {style}")
        text.append(f" {run.outcome}", style="dim")
        return text

    @staticmethod
    def _flow_visual(run: DemoRunReport) -> Text:
        style = CLASS_STYLES.get(run.classification, "white")
        text = Text()
        for index, stage in enumerate(run.flow):
            if index:
                text.append("  ━▶  ", style="dim")
            text.append(f"● {stage.upper()}", style=f"bold {style}")
        return text

    @staticmethod
    def _trace_visual(run: DemoRunReport) -> Text:
        text = Text("TRACE  ", style="bold dim")
        important = {
            "incident_suspected": ("?", "yellow"),
            "incident_confirmed": ("!", "yellow"),
            "recovery_started": ("R", "magenta"),
            "recovery_finished": ("↻", "magenta"),
            "verification_finished": ("✓", "green"),
            "recovery_failed": ("×", "red"),
            "tilt_evaluated": ("S", "cyan"),
            "tilt_evaluation_failed": ("×", "red"),
        }
        entries = [entry for entry in run.trace if entry.event in important]
        if not entries:
            text.append("● no incident", style="cyan")
            return text
        for index, entry in enumerate(entries):
            if index:
                text.append(" ─ ", style="dim")
            symbol, style = important[entry.event]
            text.append(f"{symbol} {entry.event.replace('_', ' ')}", style=style)
        return text

    def on_mount(self) -> None:
        self.query_one(Tabs).focus()

    def action_close(self) -> None:
        self.post_message(self.Closed())
