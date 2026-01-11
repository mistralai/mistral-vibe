from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterator
import uuid

from prompt_toolkit.history import FileHistory
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, VerticalScroll
from textual.events import ScreenResume
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.worker import Worker, WorkerCancelled, get_current_worker
import yaml

from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.handlers.render_handler import RenderHandler
from vibe.cli.textual_ui.hooks import HookManager, HookContext, HookEvent
from vibe.cli.textual_ui.terminal_theme import (
    TERMINAL_THEME_NAME,
    capture_terminal_theme,
)
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    BaseMessage,
    LoadingMessage,
    SystemMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.todos import TodoOverlay
from vibe.cli.textual_ui.widgets.welcome import WelcomeBanner
from vibe.core.agent import Agent, LoopControl
from vibe.core.config import VibeConfig
from vibe.core.modes import AgentMode
from vibe.core.paths.config_paths import HISTORY_FILE
from vibe.core.types import LLMMessage
from vibe.core.utils import InteractionStopped


@dataclass
class AgentTurnResult:
    turn_control: LoopControl
    rendered_ui: list[BaseMessage]


class TodoCommandProvider(Provider):
    """Command palette provider for TODO list."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        app = self.app
        assert isinstance(app, VibeApp)
        todos = app.todo_overlay.get_all_todos()

        for i, todo in enumerate(todos, 1):
            searchable = f"{i} {todo['content']} {todo['status']}"
            score = matcher.match(searchable)
            if score > 0:
                status_emoji = {
                    "pending": "⏳",
                    "in_progress": "🔄",
                    "completed": "✅",
                }.get(todo["status"], "❓")
                yield Hit(
                    score,
                    matcher.highlight(f"{status_emoji} {todo['content']}"),
                    lambda t=todo: app.focus_todo(t),
                    help=f"Status: {todo['status']}",
                )


class LoadingScreen(ModalScreen[None]):
    """Modal screen showing loading indicator while session loads."""

    DEFAULT_CSS = """
    LoadingScreen {
        align: center middle;
    }
    
    LoadingScreen > Vertical {
        width: auto;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 2 4;
    }
    
    #loading-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    
    #loading-message {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, message: str = "Loading session...") -> None:
        super().__init__()
        self.loading_message = message

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical():
            yield Static("⏳ Loading", id="loading-title")
            yield Static(self.loading_message, id="loading-message")


class VibeApp(App[None]):
    CSS_PATH = "app.tcss"

    COMMANDS = {TodoCommandProvider}

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+l", "clear_chat", "Clear Chat", show=True, priority=True),
        Binding("ctrl+n", "new_session", "New Session", show=True),
        Binding("ctrl+t", "toggle_todos", "Toggle TODOs", show=False),
        Binding("ctrl+p", "command_palette", "Commands", show=False),
        Binding("ctrl+k", "command_palette", "Commands", show=False),
        Binding("ctrl+1", "toggle_panel('files')", "Toggle Files", show=False),
        Binding("ctrl+2", "toggle_panel('telemetry')", "Toggle Telemetry", show=False),
        Binding("ctrl+3", "toggle_panel('tools')", "Toggle Tools", show=False),
        Binding("ctrl+4", "toggle_panel('memory')", "Toggle Memory", show=False),
    ]

    enable_streaming = reactive(True)

    def __init__(
        self,
        config: VibeConfig,
        agent: Agent,
        event_handler: EventHandler,
        render_handler: RenderHandler,
        hook_manager: HookManager | None = None,
        enable_streaming: bool = True,
        initial_prompt: str | None = None,
        loaded_messages: list[LLMMessage] | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.agent = agent
        self.event_handler = event_handler
        self.render_handler = render_handler
        self.hook_manager = hook_manager or HookManager()
        self.enable_streaming = enable_streaming
        self.initial_prompt = initial_prompt
        self.loaded_messages = loaded_messages

        self.pt_history = FileHistory(str(HISTORY_FILE.path))

        self.event_handler.set_mount_callback(self._mount_and_scroll)
        self.event_handler.set_app(self)

        self.render_handler.on_new_assistant_message = self._handle_new_assistant_message
        self.render_handler.on_chunk = self._handle_chunk
        self.render_handler.on_done = self._handle_render_done

        self.current_turn_worker: Worker[AgentTurnResult] | None = None
        self.todo_overlay = TodoOverlay()

        self._terminal_theme = capture_terminal_theme()

    def compose(self) -> ComposeResult:
        """Compose UI layout based on configuration."""
        if self.config.use_grid_layout:
            yield from self._compose_grid_layout()
        else:
            yield from self._compose_linear_layout()

        # Common elements for both layouts
        yield self.todo_overlay
        yield from self._compose_common_elements()

    def _compose_linear_layout(self) -> ComposeResult:
        """Traditional single-column chat layout."""
        with VerticalScroll(id="chat"):
            yield WelcomeBanner(self.config)
            yield Static(id="messages")

    def _compose_grid_layout(self) -> ComposeResult:
        """Bento Grid cockpit layout with dedicated panels."""
        from textual.containers import Grid
        from vibe.cli.textual_ui.widgets.panels import (
            FileExplorerPanel,
            MemoryPanel,
            TelemetryPanel,
            ToolLogsPanel,
        )

        with Grid(id="cockpit"):
            # Left column: File Explorer (rows 1-2)
            yield FileExplorerPanel(id="file-explorer-panel", classes="panel")

            # Center column: Main Chat (rows 1-2, main content area)
            with VerticalScroll(id="chat-panel", classes="panel main-chat"):
                yield WelcomeBanner(self.config)
                yield Static(id="messages")

            # Right column top: Telemetry (row 1)
            yield TelemetryPanel(id="telemetry-panel", classes="panel")

            # Left column bottom: Tool Logs (row 2) - moved to row 2 position
            yield ToolLogsPanel(id="tool-logs-panel", classes="panel")

            # Right column bottom: Memory Bank (row 2)
            max_context = (
                self.config.auto_compact_threshold
                if self.config.auto_compact_threshold > 0
                else 200_000
            )
            yield MemoryPanel(
                max_context=max_context, id="memory-panel", classes="panel"
            )

    def _compose_common_elements(self) -> ComposeResult:
        """Elements common to both layout modes."""
        yield Horizontal(id="loading-area")
        yield Static(id="todo-area")
        yield Static(id="bottom-app-container")
        with Horizontal(id="bottom-bar"):
            yield PathDisplay(self.config.displayed_workdir or str(Path.cwd()))
            yield Static("", id="spacer")
            yield ContextProgress(max_context=self.config.auto_compact_threshold)

    def _get_messages_container(self) -> Static:
        """Get messages container regardless of layout mode."""
        if self.config.use_grid_layout:
            return self.query_one("#chat-panel #messages", Static)
        else:
            return self.query_one("#messages", Static)

    def _get_chat_container(self) -> VerticalScroll:
        """Get chat scroll container regardless of layout mode."""
        if self.config.use_grid_layout:
            return self.query_one("#chat-panel", VerticalScroll)
        else:
            return self.query_one("#chat", VerticalScroll)

    def _update_grid_panels(self) -> None:
        """Update all grid panels after an agent turn."""
        if not self.config.use_grid_layout:
            return

        # Update telemetry panel
        with suppress(Exception):
            from vibe.cli.textual_ui.widgets.panels import TelemetryPanel

            telemetry = self.query_one("#telemetry-panel", TelemetryPanel)
            telemetry.update_from_stats(self.agent.stats)

        # Update memory panel
        with suppress(Exception):
            from vibe.cli.textual_ui.widgets.panels import MemoryPanel

            memory = self.query_one("#memory-panel", MemoryPanel)
            memory.update_from_agent(
                message_count=len(self.agent.messages),
                stats=self.agent.stats,
            )

        # File explorer updates happen automatically via tool events

    async def on_mount(self) -> None:
        if self._terminal_theme:
            self.register_theme(self._terminal_theme)
            self.theme = TERMINAL_THEME_NAME

        # Show layout selection screen if preference not set
        if not self.config.layout_preference_set:
            from vibe.cli.textual_ui.screens.layout_selection import (
                LayoutSelectionScreen,
            )

            use_grid, remember_choice = await self.push_screen_wait(
                LayoutSelectionScreen()
            )

            if remember_choice:
                # Save preference to config
                try:
                    VibeConfig.save_updates(
                        {
                            "use_grid_layout": use_grid,
                            "layout_preference_set": True,
                        }
                    )
                except Exception:
                    pass

            if use_grid and not self.config.use_grid_layout:
                # User selected grid but app started in linear mode
                # Inform them to restart
                from rich import print as rprint

                rprint(
                    "\n[green]Grid layout preference saved![/] "
                    "Please restart Vibe to use the new layout.\n"
                )
                self.exit()
                return

        chat_input_container = ChatInputContainer(
            self.pt_history,
            self.agent.mode,
            submit_callback=self.handle_user_input,
            vim_mode=self.config.vim_keybindings,
        )
        await self.query_one("#bottom-app-container").mount(chat_input_container)
        self.set_focus(chat_input_container.query_one(Input))

        # If we have loaded messages, display them
        if self.loaded_messages:
            await self._restore_session(self.loaded_messages)

        # If there's an initial prompt, send it automatically
        if self.initial_prompt:
            await self.handle_user_input(self.initial_prompt)

    async def _restore_session(self, messages: list[LLMMessage]) -> None:
        """Restore a session from loaded messages."""
        # Show loading screen
        loading_screen = LoadingScreen(
            f"Loading {len(messages)} messages from previous session..."
        )
        self.push_screen(loading_screen)

        try:
            # Add messages to agent (this is fast, just appending to list)
            self.agent.messages = list(messages)

            # Render messages progressively in the UI
            rendered_widgets = []
            for msg in messages:
                widgets = self.render_handler.render_message(msg)
                rendered_widgets.extend(widgets)

            # Mount in chunks to avoid blocking
            messages_container = self._get_messages_container()
            CHUNK_SIZE = 20  # Mount 20 widgets at a time

            for i in range(0, len(rendered_widgets), CHUNK_SIZE):
                chunk = rendered_widgets[i : i + CHUNK_SIZE]
                await messages_container.mount_all(chunk)
                # Brief pause to let UI update
                await asyncio.sleep(0.01)

            # Scroll to bottom
            chat = self._get_chat_container()
            chat.scroll_end(animate=False)

        finally:
            # Dismiss loading screen
            self.pop_screen()

    def focus_todo(self, todo: dict[str, Any]) -> None:
        """Focus and highlight a specific TODO item."""
        if not self.todo_overlay.overlay_open:
            self.action_toggle_todos()
        # Additional focus logic here if needed

    async def handle_user_input(self, user_input: str) -> None:
        hook_ctx = HookContext(self, self.config, self.agent, user_input=user_input)
        await self.hook_manager.run_hook(HookEvent.USER_PROMPT_SUBMIT, hook_ctx)

        # Create user message widget
        user_widget = UserMessage(user_input)

        # Mount user message
        await self._mount_and_scroll(user_widget)

        # Start agent turn
        await self._start_agent_turn(user_input)

    @work(exclusive=True)
    async def _start_agent_turn(self, user_input: str) -> AgentTurnResult:
        """Execute a single agent turn."""
        self.current_turn_worker = get_current_worker()

        try:
            # Add thinking animation class
            if self.config.use_grid_layout:
                chat_panel = self.query_one("#chat-panel")
                chat_panel.add_class("thinking")
            else:
                chat = self.query_one("#chat")
                chat.add_class("thinking")

            turn_control = await self.agent.step(
                user_input, self.event_handler, self.enable_streaming
            )

            # Render any remaining messages
            rendered_ui = self.render_handler.finalize()
            for widget in rendered_ui:
                await self._mount_and_scroll(widget)

            return AgentTurnResult(turn_control=turn_control, rendered_ui=rendered_ui)

        except WorkerCancelled:
            raise
        except InteractionStopped:
            return AgentTurnResult(
                turn_control=LoopControl(should_continue=False, is_stuck=False),
                rendered_ui=[],
            )
        except Exception:
            raise
        finally:
            # Remove thinking animation
            if self.config.use_grid_layout:
                with suppress(Exception):
                    chat_panel = self.query_one("#chat-panel")
                    chat_panel.remove_class("thinking")
            else:
                with suppress(Exception):
                    chat = self.query_one("#chat")
                    chat.remove_class("thinking")

            self.current_turn_worker = None

    def _handle_agent_turn(self, result: AgentTurnResult) -> None:
        """Handle completion of agent turn."""
        # Update grid panels if in grid mode
        self._update_grid_panels()

        # Update context progress
        with suppress(Exception):
            progress = self.query_one(ContextProgress)
            progress.update_from_agent(self.agent)

        # Cleanup old messages if needed (performance optimization)
        self._cleanup_old_messages()

        if result.turn_control.should_continue and not result.turn_control.is_stuck:
            # Schedule next turn
            self._start_agent_turn("")

    def _cleanup_old_messages(self, keep_last_n: int = 200) -> None:
        """Remove old message widgets to prevent DOM bloat.

        Keeps the last N messages in the DOM for performance.
        """
        messages_container = self._get_messages_container()
        all_messages = list(messages_container.query(BaseMessage))

        if len(all_messages) > keep_last_n:
            to_remove = all_messages[: -keep_last_n]
            for msg in to_remove:
                # Call disposal method if it exists
                if hasattr(msg, "dispose"):
                    msg.dispose()
                msg.remove()

    @on(Worker.StateChanged)
    def handle_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self.current_turn_worker:
            return

        if event.state == event.worker.state.SUCCESS:
            assert event.worker.result is not None
            self._handle_agent_turn(event.worker.result)
        elif event.state == event.worker.state.ERROR:
            self._handle_agent_error(event.worker.error)
        elif event.state == event.worker.state.CANCELLED:
            self._handle_agent_cancel()

    def _handle_agent_error(self, error: BaseException | None) -> None:
        err_msg = str(error) if error else "Unknown error"
        self.notify(f"Agent error: {err_msg}", severity="error")
        # Re-enable input
        chat_input = self.query_one(ChatInputContainer)
        chat_input.set_enabled(True)

    def _handle_agent_cancel(self) -> None:
        self.notify("Agent turn cancelled", severity="warning")
        chat_input = self.query_one(ChatInputContainer)
        chat_input.set_enabled(True)

    async def _handle_new_assistant_message(self, widget: AssistantMessage) -> None:
        await self._mount_and_scroll(widget)

    async def _handle_chunk(self, widget: AssistantMessage, text_delta: str) -> None:
        widget.append_text(text_delta)

        # Auto-scroll if near bottom
        chat = self._get_chat_container()
        if chat.scroll_offset.y >= chat.max_scroll_y - 10:
            chat.scroll_end(animate=False)

    async def _handle_render_done(self, widget: AssistantMessage) -> None:
        # Final scroll
        chat = self._get_chat_container()
        chat.scroll_end(animate=False)

    async def _mount_and_scroll(
        self, widget: BaseMessage, scroll: bool = True
    ) -> BaseMessage:
        """Mount widget and optionally scroll to it."""
        messages_container = self._get_messages_container()

        # Route tool widgets to tool panel if in grid mode
        if self.config.use_grid_layout and self.config.route_tools_to_panel:
            from vibe.cli.textual_ui.widgets.messages import ToolResultMessage

            if isinstance(widget, ToolResultMessage):
                # Check if this should go to tool panel
                tool_name = getattr(widget, "tool_name", None)
                if tool_name != "TodoWrite":  # TODOs stay in overlay, not panel
                    with suppress(Exception):
                        from vibe.cli.textual_ui.widgets.panels import ToolLogsPanel

                        tool_panel = self.query_one("#tool-logs-panel", ToolLogsPanel)
                        await tool_panel.add_tool_log(widget)

                        # Also track files if it's a file operation
                        if tool_name in (
                            "Read",
                            "Write",
                            "Edit",
                            "Glob",
                            "Grep",
                        ):
                            from vibe.cli.textual_ui.widgets.panels import (
                                FileExplorerPanel,
                            )

                            file_panel = self.query_one(
                                "#file-explorer-panel", FileExplorerPanel
                            )
                            # Extract file path from widget if available
                            if hasattr(widget, "file_path"):
                                file_panel.add_recent_file(Path(widget.file_path))

                        # Don't mount in chat
                        return widget

        # Default: mount in chat
        await messages_container.mount(widget)

        if scroll:
            chat = self._get_chat_container()
            chat.scroll_end(animate=False)

        return widget

    async def action_clear_chat(self) -> None:
        """Clear chat and start new session."""
        confirmed = await self.push_screen_wait(
            ConfirmDialog("Clear chat and start a new session?")
        )
        if not confirmed:
            return

        # Clear messages
        messages_container = self._get_messages_container()
        await messages_container.query(BaseMessage).remove()

        # Reset agent
        self.agent.reset(new_session_id=str(uuid.uuid4()))

        # Update UI
        self.notify("Started new session", severity="information")
        with suppress(Exception):
            progress = self.query_one(ContextProgress)
            progress.update_from_agent(self.agent)

    async def action_new_session(self) -> None:
        """Alias for clear_chat."""
        await self.action_clear_chat()

    def action_toggle_todos(self) -> None:
        """Toggle TODO overlay."""
        self.todo_overlay.toggle()

    def action_toggle_panel(self, panel_name: str) -> None:
        """Toggle visibility of a grid panel."""
        if not self.config.use_grid_layout:
            self.notify("Panels only available in grid layout", severity="warning")
            return

        panel_ids = {
            "files": "#file-explorer-panel",
            "telemetry": "#telemetry-panel",
            "tools": "#tool-logs-panel",
            "memory": "#memory-panel",
        }

        panel_id = panel_ids.get(panel_name)
        if not panel_id:
            return

        with suppress(Exception):
            panel = self.query_one(panel_id)
            panel.toggle_class("hidden")

    @on(ScreenResume)
    def handle_screen_resume(self, event: ScreenResume) -> None:
        """Handle screen resume to refocus input."""
        with suppress(Exception):
            chat_input = self.query_one(ChatInputContainer)
            self.set_focus(chat_input.query_one(Input))


class ConfirmDialog(ModalScreen[bool]):
    """Simple confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    
    ConfirmDialog > Vertical {
        width: auto;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #confirm-message {
        padding: 1 2;
    }
    
    #confirm-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }
    
    .confirm-button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import Button

        with Vertical():
            yield Static(self.message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", id="yes-btn", variant="success", classes="confirm-button")
                yield Button("No (n)", id="no-btn", variant="error", classes="confirm-button")

    def on_mount(self) -> None:
        self.query_one("#yes-btn").focus()

    @on(Button.Pressed, "#yes-btn")
    def handle_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no-btn")
    def handle_no(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def run_textual_ui(
    config: VibeConfig,
    initial_mode: AgentMode = AgentMode.DEFAULT,
    enable_streaming: bool = True,
    initial_prompt: str | None = None,
    loaded_messages: list[LLMMessage] | None = None,
) -> None:
    agent = Agent(
        config=config,
        mode=initial_mode,
    )

    hook_manager = HookManager()

    event_handler = EventHandler(
        config=config,
        enable_todos=True,
    )

    render_handler = RenderHandler(
        config=config,
        agent_stats=agent.stats,
    )

    app = VibeApp(
        config=config,
        agent=agent,
        event_handler=event_handler,
        render_handler=render_handler,
        hook_manager=hook_manager,
        enable_streaming=enable_streaming,
        initial_prompt=initial_prompt,
        loaded_messages=loaded_messages,
    )

    app.run()
