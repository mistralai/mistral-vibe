from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import TabbedContent, TabPane, Tabs

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.watchdog.demo_report import (
    DemoReport,
    render_demo_runs,
    render_demo_summary,
)


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
            for classification in ("protected", "mitigated", "blocked", "degraded")
        }
        with Vertical(id="watchcat-report-content"):
            yield NoMarkupStatic("Watchcat Run Report", classes="watchcat-report-title")
            with TabbedContent(initial="watchcat-summary", id="watchcat-report-tabs"):
                with TabPane("Summary", id="watchcat-summary"):
                    with VerticalScroll(classes="watchcat-report-scroll"):
                        yield NoMarkupStatic(render_demo_summary(self._report))
                for classification, runs in categories.items():
                    title = f"{classification.title()} ({len(runs)})"
                    with TabPane(title, id=f"watchcat-{classification}"):
                        with VerticalScroll(classes="watchcat-report-scroll"):
                            yield NoMarkupStatic(render_demo_runs(runs))
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('←→')} Change tab  {shortcut('↑↓')} Scroll  "
                    f"{shortcut('Esc')} Close"
                ),
                classes="watchcat-report-help",
            )

    def on_mount(self) -> None:
        self.query_one(Tabs).focus()

    def action_close(self) -> None:
        self.post_message(self.Closed())
