"""ChefChat The Pass Widget - The Agent Status Panel.

'The Pass' in a professional kitchen is where finished dishes are placed
for quality check before going to the customer. Here, it shows the status
of each kitchen station (agent) with progress bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Label, ProgressBar, Static

if TYPE_CHECKING:
    pass


class StationStatus(Enum):
    """Status of a kitchen station."""

    IDLE = auto()  # "At ease" - waiting for orders
    WORKING = auto()  # "Firing" - actively cooking
    COMPLETE = auto()  # "Plated" - finished the order
    ERROR = auto()  # "86'd" - something went wrong


@dataclass
class StationState:
    """Current state of a station."""

    name: str
    display_name: str
    status: StationStatus = StationStatus.IDLE
    progress: float = 0.0
    message: str = ""

    @property
    def status_emoji(self) -> str:
        """Get emoji for current status."""
        return {
            StationStatus.IDLE: "⚪",
            StationStatus.WORKING: "🔥",
            StationStatus.COMPLETE: "✅",
            StationStatus.ERROR: "❌",
        }.get(self.status, "⚪")


class StationRow(Horizontal):
    """A single station's status row."""

    DEFAULT_CSS = """
    StationRow {
        height: 3;
        margin: 0 0 1 0;
        padding: 0 1;
        align: center middle;
    }

    StationRow .station-name {
        width: 16;
        text-style: bold;
    }

    StationRow .station-progress {
        width: 1fr;
        margin: 0 1;
    }

    StationRow .station-status {
        width: 24;
        text-align: right;
    }

    StationRow.idle .station-progress Bar > .bar--bar {
        color: $text-muted;
    }

    StationRow.working .station-progress Bar > .bar--bar {
        color: $accent;
    }

    StationRow.complete .station-progress Bar > .bar--bar {
        color: $success;
    }

    StationRow.error .station-progress Bar > .bar--bar {
        color: $error;
    }
    """

    def __init__(
        self,
        station_id: str,
        display_name: str,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a station row.

        Args:
            station_id: Internal station identifier
            display_name: Human-readable station name
            name: Widget name
            id: Widget ID
            classes: CSS classes
        """
        super().__init__(name=name, id=id, classes=classes)
        self.station_id = station_id
        self.display_name = display_name
        self._status = StationStatus.IDLE
        self._progress = 0.0
        self._message = ""

    def compose(self) -> ComposeResult:
        """Compose the station row."""
        yield Label(f"⚪ {self.display_name}", classes="station-name")
        yield ProgressBar(total=100, show_eta=False, classes="station-progress")
        yield Static("Ready", classes="station-status muted")

    def update_status(
        self, status: StationStatus, progress: float = 0.0, message: str = ""
    ) -> None:
        """Update the station's status.

        Args:
            status: New status
            progress: Progress percentage (0-100)
            message: Status message to display
        """
        self._status = status
        self._progress = progress
        self._message = message

        # Update CSS class
        self.remove_class("idle", "working", "complete", "error")
        self.add_class(status.name.lower())

        # Update name label with emoji
        name_label = self.query_one(".station-name", Label)
        emoji = StationState(
            name=self.station_id, display_name=self.display_name, status=status
        ).status_emoji
        name_label.update(f"{emoji} {self.display_name}")

        # Update progress bar
        progress_bar = self.query_one(".station-progress", ProgressBar)
        progress_bar.update(progress=progress)

        # Update status text
        status_label = self.query_one(".station-status", Static)
        status_label.update(message or status.name.capitalize())


class ThePass(Container):
    """The agent status panel showing all kitchen stations.

    Displays progress bars and status for each station:
    - Sous Chef (planning)
    - Line Cook (implementation)
    - Sommelier (dependencies)
    """

    DEFAULT_CSS = """
    ThePass {
        background: $secondary-bg;
        border: solid $panel-border;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
        overflow-y: auto;
    }

    ThePass:focus {
        border: solid $accent;
    }

    ThePass #pass-header {
        height: 2;
        margin-bottom: 1;
    }

    ThePass #pass-header Static {
        text-style: bold;
        color: $accent;
    }
    """

    BORDER_TITLE = "🍳 The Pass"

    # Default stations
    DEFAULT_STATIONS = [
        ("sous_chef", "Sous Chef"),
        ("line_cook", "Line Cook"),
        ("sommelier", "Sommelier"),
    ]

    def compose(self) -> ComposeResult:
        """Compose the pass with station rows."""
        yield Static("[bold]Brigade Status[/]", id="pass-header")

        for station_id, display_name in self.DEFAULT_STATIONS:
            yield StationRow(
                station_id=station_id,
                display_name=display_name,
                id=f"station-{station_id}",
            )

    def update_station(
        self,
        station_id: str,
        status: StationStatus,
        progress: float = 0.0,
        message: str = "",
    ) -> None:
        """Update a station's status.

        Args:
            station_id: The station to update
            status: New status
            progress: Progress percentage
            message: Status message
        """
        try:
            row = self.query_one(f"#station-{station_id}", StationRow)
            row.update_status(status, progress, message)
        except Exception:
            # Station not found - silently ignore
            pass

    def set_idle(self, station_id: str) -> None:
        """Set a station to idle."""
        self.update_station(station_id, StationStatus.IDLE, 0.0, "Ready")

    def set_working(self, station_id: str, message: str = "Working...") -> None:
        """Set a station to working with optional message."""
        self.update_station(station_id, StationStatus.WORKING, 50.0, message)

    def set_complete(self, station_id: str) -> None:
        """Set a station to complete."""
        self.update_station(station_id, StationStatus.COMPLETE, 100.0, "Done")

    def set_error(self, station_id: str, message: str = "Error") -> None:
        """Set a station to error state."""
        self.update_station(station_id, StationStatus.ERROR, 0.0, message)

    def reset_all(self) -> None:
        """Reset all stations to idle."""
        for station_id, _ in self.DEFAULT_STATIONS:
            self.set_idle(station_id)
