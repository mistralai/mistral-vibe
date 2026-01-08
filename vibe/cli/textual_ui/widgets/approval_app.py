from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Static

from vibe.cli.textual_ui.renderers import get_renderer
from vibe.core.config import VibeConfig
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permission_tracker import PermissionExpirationReason


class ApprovalApp(Container):
    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_1", "Yes", show=False),
        Binding("y", "select_1", "Yes", show=False),
        Binding("2", "select_2", "Always Tool Session", show=False),
        Binding("3", "select_3", "No", show=False),
        Binding("n", "select_3", "No", show=False),
    ]

    class ApprovalGranted(Message):
        def __init__(
            self,
            tool_name: str,
            tool_args: dict,
            duration_seconds: int | None = None,
            iterations: int | None = None,
        ) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args
            self.duration_seconds = duration_seconds
            self.iterations = iterations

    class ApprovalGrantedAlwaysTool(Message):
        def __init__(
            self, tool_name: str, tool_args: dict, save_permanently: bool
        ) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args
            self.save_permanently = save_permanently

    class ApprovalRejected(Message):
        def __init__(self, tool_name: str, tool_args: dict) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args

    def __init__(
        self,
        tool_name: str,
        tool_args: dict,
        workdir: str,
        config: VibeConfig,
        permission_type: ToolPermission = ToolPermission.ASK,
        expiration_reason: str | None = None,
    ) -> None:
        super().__init__(id="approval-app")
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.workdir = workdir
        self.config = config
        self.permission_type = permission_type
        self.expiration_reason = expiration_reason
        self.selected_option = 0
        self.content_container: Vertical | None = None
        self.title_widget: Static | None = None
        self.tool_info_container: Vertical | None = None
        self.option_widgets: list[Static] = []
        self.help_widget: Static | None = None
        self.expiration_widget: Static | None = None
        self.input_widget: Input | None = None
        self.input_container: Vertical | None = None

        # Default values
        self.duration_minutes = 5  # Default: 5 minutes
        self.iterations = 10  # Default: 10 iterations

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-content"):
            self.title_widget = Static(
                f"⚠ {self.tool_name} command", classes="approval-title"
            )
            yield self.title_widget

            # Expiration notification
            if self.expiration_reason:
                expiration_text = self._get_expiration_text()
                self.expiration_widget = Static(
                    expiration_text, classes="approval-expiration"
                )
                yield self.expiration_widget

            with VerticalScroll(classes="approval-tool-info-scroll"):
                self.tool_info_container = Vertical(
                    classes="approval-tool-info-container"
                )
                yield self.tool_info_container

            yield Static("")

            # Input field for time/iterations if needed
            if self.permission_type in {
                ToolPermission.ASK_TIME,
                ToolPermission.ASK_ITERATIONS,
            }:
                self.input_container = Vertical(classes="approval-input-container")
                yield self.input_container

            for _ in range(3):
                widget = Static("", classes="approval-option")
                self.option_widgets.append(widget)
                yield widget

            yield Static("")

            help_text = "↑↓ navigate  Enter select  ESC reject"
            if self.permission_type in {
                ToolPermission.ASK_TIME,
                ToolPermission.ASK_ITERATIONS,
            }:
                help_text += "  Tab edit value"
            self.help_widget = Static(help_text, classes="approval-help")
            yield self.help_widget

    async def on_mount(self) -> None:
        await self._update_tool_info()
        if self.input_container and self.permission_type in {
            ToolPermission.ASK_TIME,
            ToolPermission.ASK_ITERATIONS,
        }:
            await self._setup_input()
        self._update_options()
        if not self.input_widget:
            # Only focus the container if there's no input field
            self.focus()

    def _focus_input_widget(self) -> None:
        """Focus the input widget after it's fully mounted."""
        if self.input_widget and self.input_widget.is_attached:
            self.input_widget.focus()

    def _get_expiration_text(self) -> str:
        """Get expiration notification text."""
        match self.expiration_reason:
            case PermissionExpirationReason.TIME_EXPIRED:
                return "⚠ Previous time-based permission expired. Grant new permission?"
            case PermissionExpirationReason.ITERATIONS_EXHAUSTED:
                return "⚠ Previous permission exhausted (0 iterations remaining). Grant new permission?"
            case _:
                return "⚠ Previous permission expired. Grant new permission?"

    async def _setup_input(self) -> None:
        """Set up input field for time/iteration values."""
        if not self.input_container:
            return

        if self.permission_type == ToolPermission.ASK_TIME:
            label = Static(
                "Duration (minutes, default: 5):", classes="approval-input-label"
            )
            self.input_widget = Input(
                value=str(self.duration_minutes), placeholder="5", id="duration-input"
            )
            await self.input_container.mount(label)
            await self.input_container.mount(self.input_widget)
            # Focus the input widget after it's mounted
            if self.input_widget:
                self.call_after_refresh(self._focus_input_widget)
        elif self.permission_type == ToolPermission.ASK_ITERATIONS:
            label = Static("Iterations (default: 10):", classes="approval-input-label")
            self.input_widget = Input(
                value=str(self.iterations), placeholder="10", id="iterations-input"
            )
            await self.input_container.mount(label)
            await self.input_container.mount(self.input_widget)
            # Focus the input widget after it's mounted
            if self.input_widget:
                self.call_after_refresh(self._focus_input_widget)

    async def _update_tool_info(self) -> None:
        if not self.tool_info_container:
            return

        renderer = get_renderer(self.tool_name)
        widget_class, data = renderer.get_approval_widget(self.tool_args)

        await self.tool_info_container.remove_children()
        approval_widget = widget_class(data)
        await self.tool_info_container.mount(approval_widget)

    def _update_options(self) -> None:
        """Update option display based on permission type."""
        if self.permission_type == ToolPermission.ASK_TIME:
            duration_text = self._get_duration_text()
            options = [
                (f"Yes (for {duration_text} - edit duration above)", "yes"),
                (f"Yes and always allow {self.tool_name}", "yes"),
                ("No and tell the agent what to do instead", "no"),
            ]
        elif self.permission_type == ToolPermission.ASK_ITERATIONS:
            iterations_text = self._get_iterations_text()
            options = [
                (f"Yes (for {iterations_text} uses - edit count above)", "yes"),
                (f"Yes and always allow {self.tool_name}", "yes"),
                ("No and tell the agent what to do instead", "no"),
            ]
        else:
            options = [
                ("Yes", "yes"),
                (f"Yes and always allow {self.tool_name} this session", "yes"),
                ("No and tell the agent what to do instead", "no"),
            ]

        for idx, ((text, color_type), widget) in enumerate(
            zip(options, self.option_widgets, strict=True)
        ):
            is_selected = idx == self.selected_option

            cursor = "› " if is_selected else "  "
            option_text = f"{cursor}{idx + 1}. {text}"

            widget.update(option_text)

            widget.remove_class("approval-cursor-selected")
            widget.remove_class("approval-option-selected")
            widget.remove_class("approval-option-yes")
            widget.remove_class("approval-option-no")

            if is_selected:
                widget.add_class("approval-cursor-selected")
                if color_type == "yes":
                    widget.add_class("approval-option-yes")
                else:
                    widget.add_class("approval-option-no")
            else:
                widget.add_class("approval-option-selected")
                if color_type == "yes":
                    widget.add_class("approval-option-yes")
                else:
                    widget.add_class("approval-option-no")

    def _get_duration_text(self) -> str:
        """Get duration text for display."""
        if self.input_widget:
            try:
                minutes = int(self.input_widget.value or str(self.duration_minutes))
                self.duration_minutes = minutes
                if minutes == 1:
                    return "1 minute"
                return f"{minutes} minutes"
            except ValueError:
                pass
        if self.duration_minutes == 1:
            return "1 minute"
        return f"{self.duration_minutes} minutes"

    def _get_iterations_text(self) -> str:
        """Get iterations text for display."""
        if self.input_widget:
            try:
                iterations = int(self.input_widget.value or str(self.iterations))
                self.iterations = iterations
                return str(iterations)
            except ValueError:
                pass
        return str(self.iterations)

    def action_move_up(self) -> None:
        self.selected_option = (self.selected_option - 1) % 3
        self._update_options()

    def action_move_down(self) -> None:
        self.selected_option = (self.selected_option + 1) % 3
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self.selected_option)

    def action_select_1(self) -> None:
        self.selected_option = 0
        self._handle_selection(0)

    def action_select_2(self) -> None:
        self.selected_option = 1
        self._handle_selection(1)

    def action_select_3(self) -> None:
        self.selected_option = 2
        self._handle_selection(2)

    def action_reject(self) -> None:
        self.selected_option = 2
        self._handle_selection(2)

    def _handle_selection(self, option: int) -> None:
        """Handle option selection."""
        match option:
            case 0:
                # Yes - with duration/iterations if applicable
                duration_seconds = None
                iterations = None

                if self.permission_type == ToolPermission.ASK_TIME:
                    try:
                        minutes = int(
                            self.input_widget.value
                            if self.input_widget
                            else str(self.duration_minutes)
                        )
                        duration_seconds = minutes * 60
                    except ValueError:
                        duration_seconds = self.duration_minutes * 60

                elif self.permission_type == ToolPermission.ASK_ITERATIONS:
                    try:
                        iterations = int(
                            self.input_widget.value
                            if self.input_widget
                            else str(self.iterations)
                        )
                    except ValueError:
                        iterations = self.iterations

                self.post_message(
                    self.ApprovalGranted(
                        tool_name=self.tool_name,
                        tool_args=self.tool_args,
                        duration_seconds=duration_seconds,
                        iterations=iterations,
                    )
                )
            case 1:
                # Always allow
                self.post_message(
                    self.ApprovalGrantedAlwaysTool(
                        tool_name=self.tool_name,
                        tool_args=self.tool_args,
                        save_permanently=True,  # Save to config
                    )
                )
            case 2:
                # Reject
                self.post_message(
                    self.ApprovalRejected(
                        tool_name=self.tool_name, tool_args=self.tool_args
                    )
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update options when input changes."""
        if event.input.id in {"duration-input", "iterations-input"}:
            self._update_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input field - select first option."""
        if event.input.id in {"duration-input", "iterations-input"}:
            # When user presses Enter in input, select the first option
            self.selected_option = 0
            self._handle_selection(0)

    def action_focus_input(self) -> None:
        """Focus the input field for editing duration/iterations."""
        if self.input_widget:
            self.input_widget.focus()

    async def on_key(self, event: events.Key) -> None:
        """Handle key events, including Tab to focus input."""
        # Handle Tab key to switch between input and options
        if event.key == "tab" and self.input_widget:
            if self.has_focus or self.input_widget.has_focus:  # type: ignore[attr-defined]
                event.prevent_default()
                event.stop()
                if self.input_widget.has_focus:  # type: ignore[attr-defined]
                    # Tab from input: focus back to container
                    self.focus()
                else:
                    # Tab from container: focus to input
                    self.input_widget.focus()
                return
        # Container doesn't have on_key, so we don't call super()

    def on_blur(self, event: events.Blur) -> None:
        # Refocus container if no child has focus
        self.call_after_refresh(self._check_and_refocus)

    def _check_and_refocus(self) -> None:
        """Refocus container if no child has focus."""
        # Check if any child widget has focus
        has_focused_child = False
        for child in self.walk_children():
            if hasattr(child, "has_focus") and child.has_focus:  # type: ignore[attr-defined]
                has_focused_child = True
                break
        if not has_focused_child:
            self.focus()
