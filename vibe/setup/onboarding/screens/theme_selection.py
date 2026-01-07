from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Container, Horizontal, Vertical
from textual.events import Resize
from textual.theme import BUILTIN_THEMES
from textual.widgets import Markdown, Static

from vibe.cli.textual_ui.terminal_theme import TERMINAL_THEME_NAME
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import VibeConfig
from vibe.setup.onboarding.base import OnboardingScreen

if TYPE_CHECKING:
    from vibe.setup.onboarding import OnboardingApp

THEMES = [TERMINAL_THEME_NAME] + sorted(
    k for k in BUILTIN_THEMES if k != "textual-ansi"
)

VISIBLE_NEIGHBORS = 3
FADE_CLASSES = ["fade-1", "fade-2", "fade-3"]

PREVIEW_MARKDOWN = """
### Heading

**Bold**, *italic*, and `inline code`.

- Bullet point
- Another bullet point

1. First item
2. Second item

```python
def greet(name: str = "World") -> str:
    return f"Hello, {name}!"
```

> Blockquote

---

| Column 1 | Column 2 |
|----------|----------|
| Item 1   | Item 2   |
"""


class ThemeSelectionScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "next", "Next", show=False, priority=True),
        Binding("up", "prev_theme", "Previous", show=False),
        Binding("down", "next_theme", "Next Theme", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = "api_key"

    def __init__(self) -> None:
        super().__init__()
        self._theme_index = 0
        self._theme_widgets: list[Static] = []

    def _compose_theme_list(self) -> ComposeResult:
        for _ in range(VISIBLE_NEIGHBORS * 2 + 1):
            widget = NoMarkupStatic("", classes="theme-item")
            self._theme_widgets.append(widget)
            yield widget

    def compose(self) -> ComposeResult:
        with Center(id="theme-outer"):
            with Vertical(id="theme-content"):
                yield NoMarkupStatic("Select your preferred theme", id="theme-title")
                yield Center(
                    Horizontal(
                        NoMarkupStatic("Navigate ↑ ↓", id="nav-hint"),
                        Vertical(*self._compose_theme_list(), id="theme-list"),
                        NoMarkupStatic("Press Enter \u21b5", id="enter-hint"),
                        id="theme-row",
                    )
                )
                with Container(id="preview-center"):
                    preview = Container(id="preview")
                    preview.border_title = "Preview"
                    with preview:
                        yield Container(Markdown(PREVIEW_MARKDOWN), id="preview-inner")

    @property
    def _has_terminal_theme(self) -> bool:
        app: OnboardingApp = self.app  # type: ignore[assignment]
        return app._terminal_theme is not None

    @property
    def _available_themes(self) -> list[str]:
        if self._has_terminal_theme:
            return THEMES
        return [t for t in THEMES if t != TERMINAL_THEME_NAME]

    def on_mount(self) -> None:
        current_theme = self.app.theme
        themes = self._available_themes
        if current_theme == TERMINAL_THEME_NAME:
            self._theme_index = 0
        elif current_theme in themes:
            self._theme_index = themes.index(current_theme)
        self._update_display()
        self._update_preview_height()
        self.focus()

    def on_resize(self, _: Resize) -> None:
        self._update_preview_height()

    def _update_preview_height(self) -> None:
        # Height is dynamically set because css won't allow filling available space and page scroll on overflow.
        preview = self.query_one("#preview", Container)
        header_height = 17  # title + margins + theme row + padding + buffer
        available = self.app.size.height - header_height
        preview.styles.max_height = max(10, available)

    def _get_theme_at_offset(self, offset: int) -> str:
        themes = self._available_themes
        index = (self._theme_index + offset) % len(themes)
        return themes[index]

    def _update_display(self) -> None:
        for i, widget in enumerate(self._theme_widgets):
            offset = i - VISIBLE_NEIGHBORS
            theme = self._get_theme_at_offset(offset)

            widget.remove_class("selected", *FADE_CLASSES)

            if offset == 0:
                widget.update(f" {theme} ")
                widget.add_class("selected")
            else:
                distance = min(abs(offset) - 1, len(FADE_CLASSES) - 1)
                widget.update(theme)
                widget.add_class(FADE_CLASSES[distance])

    def _navigate(self, direction: int) -> None:
        themes = self._available_themes
        self._theme_index = (self._theme_index + direction) % len(themes)
        theme = themes[self._theme_index]
        self.app.theme = theme
        self._update_display()

    def action_next_theme(self) -> None:
        self._navigate(1)

    def action_prev_theme(self) -> None:
        self._navigate(-1)

    def action_next(self) -> None:
        theme = self._available_themes[self._theme_index]
        try:
            VibeConfig.save_updates({"textual_theme": theme})
        except OSError:
            pass
        super().action_next()
