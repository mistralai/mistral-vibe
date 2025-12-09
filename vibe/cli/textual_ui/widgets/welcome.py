from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from rich.align import Align
from rich.console import Group
from rich.text import Text
from textual.color import Color
from textual.widgets import Static

from vibe.core import __version__
from vibe.core.config import VibeConfig


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    normalized = hex_color.lstrip("#")
    r, g, b = (int(normalized[i : i + 2], 16) for i in (0, 2, 4))
    return (r, g, b)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def interpolate_color(
    start_rgb: tuple[int, int, int], end_rgb: tuple[int, int, int], progress: float
) -> str:
    progress = max(0.0, min(1.0, progress))
    r = int(start_rgb[0] + (end_rgb[0] - start_rgb[0]) * progress)
    g = int(start_rgb[1] + (end_rgb[1] - start_rgb[1]) * progress)
    b = int(start_rgb[2] + (end_rgb[2] - start_rgb[2]) * progress)
    return rgb_to_hex(r, g, b)


@dataclass
class LineAnimationState:
    progress: float = 0.0
    cached_color: str | None = None
    cached_progress: float = -1.0
    rendered_color: str | None = None


class WelcomeBanner(Static):
    FLASH_COLOR = "#FFFFFF"
    TARGET_COLORS = ("#FFD800", "#FFAF00", "#FF8205", "#FA500F", "#E10500")
    BORDER_TARGET_COLOR = "#b05800"

    LINE_ANIMATION_DURATION_MS = 200
    LINE_STAGGER_MS = 280
    FLASH_RESET_DURATION_MS = 400
    ANIMATION_TICK_INTERVAL = 0.1

    COLOR_FLASH_MIDPOINT = 0.5
    COLOR_PHASE_SCALE = 2.0
    COLOR_CACHE_THRESHOLD = 0.001
    BORDER_PROGRESS_THRESHOLD = 0.01

    BLOCK = "▇▇"
    SPACE = "  "
    LOGO_TEXT_GAP = "   "

    def __init__(self, config: VibeConfig) -> None:
        super().__init__(" ")
        self.config = config
        self.animation_timer = None
        self._animation_start_time: float | None = None

        self._cached_skeleton_color: str | None = None
        self._cached_skeleton_rgb: tuple[int, int, int] | None = None
        self._flash_rgb = hex_to_rgb(self.FLASH_COLOR)
        self._target_rgbs = [hex_to_rgb(c) for c in self.TARGET_COLORS]
        self._border_target_rgb = hex_to_rgb(self.BORDER_TARGET_COLOR)

        self._line_states = [LineAnimationState() for _ in self.TARGET_COLORS]
        self.border_progress = 0.0
        self._cached_border_color: str | None = None
        self._cached_border_progress = -1.0

        self._line_duration = self.LINE_ANIMATION_DURATION_MS / 1000
        self._line_stagger = self.LINE_STAGGER_MS / 1000
        self._border_duration = self.FLASH_RESET_DURATION_MS / 1000
        self._line_start_times = [
            idx * self._line_stagger for idx in range(len(self.TARGET_COLORS))
        ]
        self._all_lines_finish_time = (
            (len(self.TARGET_COLORS) - 1) * self.LINE_STAGGER_MS
            + self.LINE_ANIMATION_DURATION_MS
        ) / 1000

        self._cached_text_lines: list[Text | None] = [None] * 7
        self._initialize_static_line_suffixes()

    def _initialize_static_line_suffixes(self) -> None:
        self._static_line1_suffix = (
            f"{self.LOGO_TEXT_GAP}[b]Mistral Vibe v{__version__}[/]"
        )
        self._static_line2_suffix = (
            f"{self.LOGO_TEXT_GAP}[dim]{self.config.active_model}[/]"
        )
        mcp_count = len(self.config.mcp_servers)
        model_count = len(self.config.models)
        self._static_line3_suffix = f"{self.LOGO_TEXT_GAP}[dim]{model_count} models · {mcp_count} MCP servers[/]"
        self._static_line5_suffix = (
            f"{self.LOGO_TEXT_GAP}[dim]{self.config.effective_workdir}[/]"
        )
        block = (self.SPACE * 4) + self.LOGO_TEXT_GAP
        self._static_line7 = f"{block}[dim]Type[/] [{self.BORDER_TARGET_COLOR}]/help[/] [dim]for more information[/]"

    @property
    def skeleton_color(self) -> str:
        return self._cached_skeleton_color or "#1e1e1e"

    @property
    def skeleton_rgb(self) -> tuple[int, int, int]:
        return self._cached_skeleton_rgb or hex_to_rgb("#1e1e1e")

    def on_mount(self) -> None:
        if not self.config.disable_welcome_banner_animation:
            self.call_after_refresh(self._init_after_styles)

    def _init_after_styles(self) -> None:
        self._cache_skeleton_color()
        self._cached_text_lines[5] = Text("")
        self._cached_text_lines[6] = Text.from_markup(self._static_line7)
        self._update_display()
        self._start_animation()

    def _cache_skeleton_color(self) -> None:
        try:
            border = self.styles.border
            if (
                hasattr(border, "top")
                and isinstance(edge := border.top, tuple)
                and len(edge) >= 2  # noqa: PLR2004
                and isinstance(color := edge[1], Color)
            ):
                self._cached_skeleton_color = color.hex
                self._cached_skeleton_rgb = hex_to_rgb(color.hex)
                return
        except (AttributeError, TypeError):
            pass

        self._cached_skeleton_color = "#1e1e1e"
        self._cached_skeleton_rgb = hex_to_rgb("#1e1e1e")

    def _stop_timer(self) -> None:
        if self.animation_timer:
            try:
                self.animation_timer.stop()
            except Exception:
                pass
            self.animation_timer = None

    def on_unmount(self) -> None:
        self._stop_timer()

    def _start_animation(self) -> None:
        self._animation_start_time = monotonic()

        def tick() -> None:
            if self._is_animation_complete():
                self._stop_timer()
                return
            if self._animation_start_time is None:
                return

            elapsed = monotonic() - self._animation_start_time
            updated_lines = self._advance_line_progress(elapsed)
            border_updated = self._advance_border_progress(elapsed)

            if border_updated:
                self._update_border_color()
            if updated_lines or border_updated:
                self._update_display()

        self.animation_timer = self.set_interval(self.ANIMATION_TICK_INTERVAL, tick)

    def _advance_line_progress(self, elapsed: float) -> bool:
        any_updates = False
        for line_idx, state in enumerate(self._line_states):
            if state.progress >= 1.0:
                continue
            start_time = self._line_start_times[line_idx]
            if elapsed < start_time:
                continue
            progress = min(1.0, (elapsed - start_time) / self._line_duration)
            if progress > state.progress:
                state.progress = progress
                any_updates = True
        return any_updates

    def _advance_border_progress(self, elapsed: float) -> bool:
        if elapsed < self._all_lines_finish_time:
            return False

        new_progress = min(
            1.0, (elapsed - self._all_lines_finish_time) / self._border_duration
        )

        if abs(new_progress - self.border_progress) > self.BORDER_PROGRESS_THRESHOLD:
            self.border_progress = new_progress
            return True

        return False

    def _is_animation_complete(self) -> bool:
        return (
            all(state.progress >= 1.0 for state in self._line_states)
            and self.border_progress >= 1.0
        )

    def _update_border_color(self) -> None:
        progress = self.border_progress
        if abs(progress - self._cached_border_progress) < self.COLOR_CACHE_THRESHOLD:
            return

        border_color = self._compute_color_for_progress(
            progress, self._border_target_rgb
        )
        self._cached_border_color = border_color
        self._cached_border_progress = progress
        self.styles.border = ("round", border_color)

    def _compute_color_for_progress(
        self, progress: float, target_rgb: tuple[int, int, int]
    ) -> str:
        if progress <= 0:
            return self.skeleton_color

        if progress <= self.COLOR_FLASH_MIDPOINT:
            phase = progress * self.COLOR_PHASE_SCALE
            return interpolate_color(self.skeleton_rgb, self._flash_rgb, phase)

        phase = (progress - self.COLOR_FLASH_MIDPOINT) * self.COLOR_PHASE_SCALE
        return interpolate_color(self._flash_rgb, target_rgb, phase)

    def _update_display(self) -> None:
        for idx in range(5):
            self._update_colored_line(idx, idx)

        lines = [line if line else Text("") for line in self._cached_text_lines]
        self.update(Align.center(Group(*lines)))

    def _get_color(self, line_idx: int) -> str:
        state = self._line_states[line_idx]
        if (
            abs(state.progress - state.cached_progress) < self.COLOR_CACHE_THRESHOLD
            and state.cached_color
        ):
            return state.cached_color

        color = self._compute_color_for_progress(
            state.progress, self._target_rgbs[line_idx]
        )
        state.cached_color = color
        state.cached_progress = state.progress
        return color

    def _update_colored_line(self, slot_idx: int, line_idx: int) -> None:
        color = self._get_color(line_idx)
        state = self._line_states[line_idx]

        if color == state.rendered_color and self._cached_text_lines[slot_idx]:
            return

        state.rendered_color = color
        self._cached_text_lines[slot_idx] = Text.from_markup(
            self._build_line(slot_idx, color)
        )

    def _build_line(self, line_idx: int, color: str) -> str:
        B = self.BLOCK
        S = self.SPACE

        patterns = [
            f"{S}[{color}]{B}[/]{S}{S}{S}[{color}]{B}[/]{S}{self._static_line1_suffix}",
            f"{S}[{color}]{B}{B}[/]{S}[{color}]{B}{B}[/]{S}{self._static_line2_suffix}",
            f"{S}[{color}]{B}{B}{B}{B}{B}[/]{S}{self._static_line3_suffix}",
            f"{S}[{color}]{B}[/]{S}[{color}]{B}[/]{S}[{color}]{B}[/]{S}",
            f"[{color}]{B}{B}{B}[/]{S}[{color}]{B}{B}{B}[/]{self._static_line5_suffix}",
        ]
        return patterns[line_idx]
