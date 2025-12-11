"""ChefChat The Pass Widget - The Agent Status Panel.

'The Pass' in a professional kitchen is where finished dishes are placed
for quality check before going to the customer. Here, it shows the status
of each kitchen station (agent) with progress bars.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
from textual.widgets import Label, ProgressBar, Static

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


from chefchat.interface.constants import DEFAULT_STATIONS, StationStatus

# Status emoji mapping - static to avoid creating StationState objects
STATUS_EMOJI: dict[StationStatus, str] = {
    StationStatus.IDLE: "⚪",
    StationStatus.WORKING: "🔥",
    StationStatus.COMPLETE: "✅",
    StationStatus.ERROR: "❌",
}


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
        emoji = STATUS_EMOJI.get(StationStatus.IDLE, "⚪")
        yield Label(f"{emoji} {self.display_name}", classes="station-name")
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

        # Update name label with emoji using static mapping
        try:
            name_label = self.query_one(".station-name", Label)
            emoji = STATUS_EMOJI.get(status, "⚪")
            name_label.update(f"{emoji} {self.display_name}")
        except NoMatches:
            logger.warning("Name label not found for station %s", self.station_id)

        # Update progress bar
        try:
            progress_bar = self.query_one(".station-progress", ProgressBar)
            progress_bar.update(progress=progress)
        except NoMatches:
            logger.warning("Progress bar not found for station %s", self.station_id)

        # Update status text
        try:
            status_label = self.query_one(".station-status", Static)
            status_label.update(message or status.name.capitalize())
        except NoMatches:
            logger.warning("Status label not found for station %s", self.station_id)


class ThePass(Container):
    """The agent status panel showing all kitchen stations.

    Displays progress bars and status for each station.
    Stations can be configured via constructor or use defaults.
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

    def __init__(
        self,
        stations: list[tuple[str, str]] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize The Pass.

        Args:
            stations: Optional list of (station_id, display_name) tuples.
                     Defaults to DEFAULT_STATIONS if not provided.
            name: Widget name
            id: Widget ID
            classes: CSS classes
        """
        super().__init__(name=name, id=id, classes=classes)
        self._stations = stations if stations is not None else DEFAULT_STATIONS

    def compose(self) -> ComposeResult:
        """Compose the pass with station rows."""
        yield Static("[bold]Brigade Status[/]", id="pass-header")

        for station_id, display_name in self._stations:
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
        except NoMatches:
            logger.warning(
                "Station '%s' not found in The Pass. Available stations: %s",
                station_id,
                [s[0] for s in self._stations],
            )

    def set_idle(self, station_id: str) -> None:
        """Set a station to idle.

        Args:
            station_id: The station to update
        """
        self.update_station(station_id, StationStatus.IDLE, 0.0, "Ready")

    def set_working(self, station_id: str, message: str = "Working...") -> None:
        """Set a station to working with optional message.

        Args:
            station_id: The station to update
            message: Status message to display
        """
        self.update_station(station_id, StationStatus.WORKING, 50.0, message)

    def set_complete(self, station_id: str) -> None:
        """Set a station to complete.

        Args:
            station_id: The station to update
        """
        self.update_station(station_id, StationStatus.COMPLETE, 100.0, "Done")

    def set_error(self, station_id: str, message: str = "Error") -> None:
        """Set a station to error state.

        Args:
            station_id: The station to update
            message: Error message
        """
        self.update_station(station_id, StationStatus.ERROR, 0.0, message)

    def reset_all(self) -> None:
        """Reset all stations to idle."""
        for station_id, _ in self._stations:
            self.set_idle(station_id)

    def get_station_ids(self) -> list[str]:
        """Get list of configured station IDs.

        Returns:
            List of station identifiers
        """
        return [s[0] for s in self._stations]
