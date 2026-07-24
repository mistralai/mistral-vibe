from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Markdown, Static

FOCUSED_OPACITY = 1.0
DIMMED_OPACITY = 0.5
EASING = "out_cubic"

FADE_PRESETS: dict[str, float] = {
    "Zen": 0.9,
    "Calm": 0.55,
    "Normal": 0.3,
    "Swift": 0.15,
    "Instant": 0.0,
}
DEFAULT_PACE_LABEL = "Calm"
PACE_ORDER: list[str] = list(FADE_PRESETS)


def pace_duration(label: str) -> float:
    return FADE_PRESETS.get(label, FADE_PRESETS[DEFAULT_PACE_LABEL])


def next_pace_label(label: str) -> str:
    try:
        idx = PACE_ORDER.index(label)
    except ValueError:
        idx = PACE_ORDER.index(DEFAULT_PACE_LABEL)
    return PACE_ORDER[(idx + 1) % len(PACE_ORDER)]


def split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


class CalmBlock(Markdown):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.add_class("calm-block")
        self.styles.display = "none"
        self.styles.opacity = 0.0
        self._revealed = False

    def reveal(self, *, focused: bool, duration: float) -> None:
        self._revealed = True
        self.styles.display = "block"
        target = FOCUSED_OPACITY if focused else DIMMED_OPACITY
        if duration > 0:
            self.styles.opacity = 0.0
            self.styles.animate("opacity", target, duration=duration, easing=EASING)
        else:
            self.styles.opacity = target
        self.scroll_visible(animate=duration > 0, top=False)

    def set_focus(self, focused: bool, *, duration: float) -> None:
        if not self._revealed:
            return
        target = FOCUSED_OPACITY if focused else DIMMED_OPACITY
        if duration > 0:
            self.styles.animate("opacity", target, duration=duration, easing=EASING)
        else:
            self.styles.opacity = target
        if focused:
            self.scroll_visible(animate=duration > 0, top=False)


class CalmRevealContainer(Vertical):
    def __init__(
        self,
        blocks: list[str],
        *,
        duration: float | None = None,
        motion: bool | None = None,
    ) -> None:
        super().__init__()
        self.add_class("calm-reveal-container")
        self._block_texts = blocks
        self._blocks: list[CalmBlock] = []
        self._cursor = -1
        self._duration_override = duration
        self._motion_override = motion
        self._progress: Static | None = None

    def compose(self) -> ComposeResult:
        for text in self._block_texts:
            block = CalmBlock(text)
            self._blocks.append(block)
            yield block
        self._progress = Static("", classes="calm-progress")
        yield self._progress

    def _resolve_settings(self) -> tuple[float, bool]:
        app = self.app
        if self._duration_override is not None:
            duration = self._duration_override
        else:
            label = getattr(app, "calm_pace_label", DEFAULT_PACE_LABEL)
            duration = pace_duration(label)
        if self._motion_override is not None:
            motion = self._motion_override
        else:
            motion = getattr(app, "calm_motion_enabled", False)
        if not motion:
            duration = 0.0
        return duration, motion

    def on_mount(self) -> None:
        self.calm_advance(0)

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    @property
    def cursor(self) -> int:
        return self._cursor

    def has_unrevealed(self) -> bool:
        return any(not b._revealed for b in self._blocks)

    def calm_next(self) -> None:
        if self._cursor < len(self._blocks) - 1:
            self.calm_advance(self._cursor + 1)

    def calm_prev(self) -> None:
        if self._cursor > 0:
            self.calm_advance(self._cursor - 1)

    def calm_advance(self, target: int) -> None:
        if not self._blocks:
            return
        target = max(0, min(target, len(self._blocks) - 1))
        prev = self._cursor
        self._cursor = target
        duration, _ = self._resolve_settings()
        if 0 <= prev < len(self._blocks) and prev != target:
            self._blocks[prev].set_focus(False, duration=duration)
        self._blocks[target].reveal(focused=True, duration=duration)
        self._update_progress()

    def reveal_all(self) -> None:
        if not self._blocks:
            return
        duration, _ = self._resolve_settings()
        for block in self._blocks:
            block.reveal(focused=True, duration=duration)
        self._cursor = len(self._blocks) - 1
        self._update_progress()

    def _update_progress(self) -> None:
        if self._progress is None:
            return
        total = len(self._blocks)
        pos = min(self._cursor + 1, total) if total else 0
        self._progress.update(f"\u00b6 {pos}/{total}")
