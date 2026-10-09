from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from textual.content import Content

from vibe.app_server.models import PublicNoticeEntry, ScheduledLoopFiredNoticeDetail
from vibe.user_content import UserDisplayContent

SCHEDULED_LOOP_DISPLAY_BLOCK = "vibe.scheduled_loop"


@dataclass(frozen=True, slots=True)
class FiredLoop:
    loop_id: str
    fired_at_ms: int | None = None

    @classmethod
    def from_display(cls, display: UserDisplayContent | None) -> FiredLoop | None:
        if display is None:
            return None
        marker = next(
            (
                block
                for block in display.content
                if block.get("type") == SCHEDULED_LOOP_DISPLAY_BLOCK
            ),
            None,
        )
        if marker is None or not isinstance(loop_id := marker.get("loopId"), str):
            return None
        fired_at = marker.get("firedAt")
        if isinstance(fired_at, bool) or not isinstance(fired_at, int):
            return cls(loop_id)
        return cls(loop_id, fired_at)

    @classmethod
    def from_notice(cls, entry: PublicNoticeEntry) -> FiredLoop | None:
        if not isinstance(entry.detail, ScheduledLoopFiredNoticeDetail):
            return None
        return cls(entry.detail.loop_id, entry.created_at)

    def label(self) -> Content:
        parts: list[str | tuple[str, str]] = ["└ loop ", (self.loop_id, "$primary")]
        if (fired_at := self._fired_at()) is not None:
            parts.append(f" - {fired_at}")
        return Content.assemble(*parts)

    def _fired_at(self) -> str | None:
        if self.fired_at_ms is None:
            return None
        try:
            fired_at = datetime.fromtimestamp(self.fired_at_ms / 1000).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
        return f"{fired_at:%Y-%m-%d %H:%M:%S %Z}"
