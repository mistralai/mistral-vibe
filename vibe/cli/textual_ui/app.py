from __future__ import annotations

import asyncio
from enum import StrEnum, auto
import subprocess
import time
from typing import Any, ClassVar, assert_never

from pydantic import BaseModel
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import AppBlur, AppFocus, MouseUp
from textual.widget import Widget
from textual.widgets import Static

from vibe import __version__ as CORE_VERSION
from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.cli.commands import CommandRegistry
from vibe.cli.terminal_setup import setup_terminal
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.terminal_theme import (
    TERMINAL_THEME_NAME,
    capture_terminal_theme,
)

from vibe.cli.textual_ui.utils import (
    build_conversation_tree,
    load_conversation_from_file,
)
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.config_app import ConfigApp
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.conversation_tree_selector import (
    ConversationTreeSelector,
)
from vibe.cli.textual_ui.widgets.folder_selector import FolderSelector
from vibe.cli.textual_ui.widgets.input_dialog import InputDialog
from vibe.cli.textual_ui.widgets.loading import LoadingWidget
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    BashOutputMessage,
    ErrorMessage,
    InterruptMessage,
    ReasoningMessage,
    StreamingMessageBase,
    UserCommandMessage,
    UserMessage,
    WarningMessage,
)
from vibe.cli.textual_ui.widgets.mode_indicator import ModeIndicator
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.cli.textual_ui.widgets.welcome import WelcomeBanner
from vibe.cli.update_notifier import (
    FileSystemUpdateCacheRepository,
    PyPIVersionUpdateGateway,
    UpdateCacheRepository,
    VersionUpdateAvailability,
    VersionUpdateError,
    VersionUpdateGateway,
    get_update_if_available,
)
from vibe.core.agent import Agent
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import VibeConfig
from vibe.core.modes import AgentMode, next_mode
from vibe.core.paths.config_paths import HISTORY_FILE
from vibe.core.paths.global_paths import ensure_conversations_dir_exists
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.types import ApprovalResponse, LLMMessage, Role
from vibe.core.utils import (
    CancellationReason,
    get_user_cancellation_message,
    is_dangerous_directory,
    logger,
)


class BottomApp(StrEnum):
    Approval = auto()
    Config = auto()
    Input = auto()
    InputDialog = auto()
    ConversationTreeSelector = auto()
    FolderSelector = auto()


class VibeApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "clear_quit", "Quit", show=False),
        Binding("ctrl+d", "force_quit", "Quit", show=False, priority=True),
        Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
        Binding("ctrl+o", "toggle_tool", "Toggle Tool", show=False),
        Binding("ctrl+t", "toggle_todo", "Toggle Todo", show=False),
        Binding("shift+tab", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("shift+up", "scroll_chat_up", "Scroll Up", show=False, priority=True),
        Binding(
            "shift+down", "scroll_chat_down", "Scroll Down", show=False, priority=True
        ),
    ]

    def __init__(
        self,
        config: VibeConfig,
        initial_mode: AgentMode = AgentMode.DEFAULT,
        enable_streaming: bool = False,
        initial_prompt: str | None = None,
        loaded_messages: list[LLMMessage] | None = None,
        version_update_notifier: VersionUpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        current_version: str = CORE_VERSION,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.config = config
        self._current_agent_mode = initial_mode
        self.enable_streaming = enable_streaming
        self.agent: Agent | None = None
        self._agent_running = False
        self._agent_initializing = False
        self._interrupt_requested = False
        self._agent_task: asyncio.Task | None = None

        self._loading_widget: LoadingWidget | None = None
        self._pending_approval: asyncio.Future | None = None

        self.event_handler: EventHandler | None = None
        self.commands = CommandRegistry()

        self._chat_input_container: ChatInputContainer | None = None
        self._mode_indicator: ModeIndicator | None = None
        self._context_progress: ContextProgress | None = None
        self._current_bottom_app: BottomApp = BottomApp.Input
        self._selected_folder: str = ""  # Selected folder for saving
        self.theme = config.textual_theme

        self.history_file = HISTORY_FILE.path

        self._tools_collapsed = True
        self._todos_collapsed = False
        self._current_streaming_message: AssistantMessage | None = None
        self._current_streaming_reasoning: ReasoningMessage | None = None
        self._version_update_notifier = version_update_notifier
        self._update_cache_repository = update_cache_repository
        self._is_update_check_enabled = config.enable_update_checks
        self._current_version = current_version
        self._update_notification_task: asyncio.Task | None = None
        self._update_notification_shown = False

        self._initial_prompt = initial_prompt
        self._loaded_messages = loaded_messages
        self._agent_init_task: asyncio.Task | None = None
        # prevent a race condition where the agent initialization
        # completes exactly at the moment the user interrupts
        self._agent_init_interrupted = False
        self._auto_scroll = True
        self._last_escape_time: float | None = None
        self._terminal_theme = capture_terminal_theme()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat"):
            yield WelcomeBanner(self.config)
            yield Static(id="messages")

        with Horizontal(id="loading-area"):
            yield Static(id="loading-area-content")
            yield ModeIndicator(mode=self._current_agent_mode)

        yield Static(id="todo-area")

        with Vertical(id="bottom-app-container"):
            yield ChatInputContainer(
                history_file=self.history_file,
                command_registry=self.commands,
                id="input-container",
                safety=self._current_agent_mode.safety,
            )

        with Horizontal(id="bottom-bar"):
            yield PathDisplay(
                self.config.displayed_workdir or self.config.effective_workdir
            )
            yield Static(id="spacer")
            yield ContextProgress()

    async def on_mount(self) -> None:
        if self._terminal_theme:
            self.register_theme(self._terminal_theme)

        if self.config.textual_theme == TERMINAL_THEME_NAME:
            if self._terminal_theme:
                self.theme = TERMINAL_THEME_NAME
        else:
            self.theme = self.config.textual_theme

        self.event_handler = EventHandler(
            mount_callback=self._mount_and_scroll,
            scroll_callback=self._scroll_to_bottom_deferred,
            todo_area_callback=lambda: self.query_one("#todo-area"),
            get_tools_collapsed=lambda: self._tools_collapsed,
            get_todos_collapsed=lambda: self._todos_collapsed,
        )

        self._chat_input_container = self.query_one(ChatInputContainer)
        self._mode_indicator = self.query_one(ModeIndicator)
        self._context_progress = self.query_one(ContextProgress)

        if self.config.auto_compact_threshold > 0:
            self._context_progress.tokens = TokenState(
                max_tokens=self.config.auto_compact_threshold, current_tokens=0
            )

        chat_input_container = self.query_one(ChatInputContainer)
        chat_input_container.focus_input()
        await self._show_dangerous_directory_warning()
        self._schedule_update_notification()

        if self._loaded_messages:
            await self._rebuild_history_from_messages()

        if self._initial_prompt:
            self.call_after_refresh(self._process_initial_prompt)
        else:
            self._ensure_agent_init_task()

    def _process_initial_prompt(self) -> None:
        if self._initial_prompt:
            self.run_worker(
                self._handle_user_message(self._initial_prompt), exclusive=False
            )

    async def on_chat_input_container_submitted(
        self, event: ChatInputContainer.Submitted
    ) -> None:
        value = event.value.strip()
        if not value:
            return

        input_widget = self.query_one(ChatInputContainer)
        input_widget.value = ""

        if self._agent_running:
            await self._interrupt_agent()

        if value.startswith("!"):
            await self._handle_bash_command(value[1:])
            return

        if await self._handle_command(value):
            return

        await self._handle_user_message(value)

    async def on_approval_app_approval_granted(
        self, message: ApprovalApp.ApprovalGranted
    ) -> None:
        if self._pending_approval and not self._pending_approval.done():
            self._pending_approval.set_result((ApprovalResponse.YES, None))

        await self._switch_to_input_app()

    async def on_approval_app_approval_granted_always_tool(
        self, message: ApprovalApp.ApprovalGrantedAlwaysTool
    ) -> None:
        self._set_tool_permission_always(
            message.tool_name, save_permanently=message.save_permanently
        )

        if self._pending_approval and not self._pending_approval.done():
            self._pending_approval.set_result((ApprovalResponse.YES, None))

        await self._switch_to_input_app()

    async def on_approval_app_approval_rejected(
        self, message: ApprovalApp.ApprovalRejected
    ) -> None:
        if self._pending_approval and not self._pending_approval.done():
            feedback = str(
                get_user_cancellation_message(CancellationReason.OPERATION_CANCELLED)
            )
            self._pending_approval.set_result((ApprovalResponse.NO, feedback))

        await self._switch_to_input_app()

        if self._loading_widget and self._loading_widget.parent:
            await self._remove_loading_widget()

    async def _remove_loading_widget(self) -> None:
        if self._loading_widget and self._loading_widget.parent:
            await self._loading_widget.remove()
            self._loading_widget = None
        self._hide_todo_area()

    def _show_todo_area(self) -> None:
        try:
            todo_area = self.query_one("#todo-area")
            todo_area.add_class("loading-active")
        except Exception:
            pass

    def _hide_todo_area(self) -> None:
        try:
            todo_area = self.query_one("#todo-area")
            todo_area.remove_class("loading-active")
        except Exception:
            pass

    def on_config_app_setting_changed(self, message: ConfigApp.SettingChanged) -> None:
        if message.key == "textual_theme":
            if message.value == TERMINAL_THEME_NAME:
                if self._terminal_theme:
                    self.theme = TERMINAL_THEME_NAME
            else:
                self.theme = message.value

    async def on_config_app_config_closed(
        self, message: ConfigApp.ConfigClosed
    ) -> None:
        if message.changes:
            self._save_config_changes(message.changes)
            await self._reload_config()
        else:
            await self._mount_and_scroll(
                UserCommandMessage("Configuration closed (no changes saved).")
            )

        await self._switch_to_input_app()

    def _set_tool_permission_always(
        self, tool_name: str, save_permanently: bool = False
    ) -> None:
        if save_permanently:
            VibeConfig.save_updates({"tools": {tool_name: {"permission": "always"}}})

        if tool_name not in self.config.tools:
            self.config.tools[tool_name] = BaseToolConfig()

        self.config.tools[tool_name].permission = ToolPermission.ALWAYS

    def _save_config_changes(self, changes: dict[str, str]) -> None:
        if not changes:
            return

        updates: dict = {}

        for key, value in changes.items():
            match key:
                case "active_model":
                    if value != self.config.active_model:
                        updates["active_model"] = value
                case "textual_theme":
                    if value != self.config.textual_theme:
                        updates["textual_theme"] = value

        if updates:
            VibeConfig.save_updates(updates)

    async def _handle_command(self, user_input: str) -> bool:
        if command := self.commands.find_command(user_input):
            await self._mount_and_scroll(UserMessage(user_input))
            handler = getattr(self, command.handler)
            if asyncio.iscoroutinefunction(handler):
                await handler()
            else:
                handler()
            return True
        return False

    async def _handle_bash_command(self, command: str) -> None:
        if not command:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No command provided after '!'", collapsed=self._tools_collapsed
                )
            )
            return

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=False,
                timeout=30,
                cwd=self.config.effective_workdir,
            )
            stdout = (
                result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            )
            stderr = (
                result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            )
            output = stdout or stderr or "(no output)"
            exit_code = result.returncode
            await self._mount_and_scroll(
                BashOutputMessage(
                    command, str(self.config.effective_workdir), output, exit_code
                )
            )
        except subprocess.TimeoutExpired:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Command timed out after 30 seconds",
                    collapsed=self._tools_collapsed,
                )
            )
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(f"Command failed: {e}", collapsed=self._tools_collapsed)
            )

    async def _handle_user_message(self, message: str) -> None:
        init_task = self._ensure_agent_init_task()
        pending_init = bool(init_task and not init_task.done())
        user_message = UserMessage(message, pending=pending_init)

        await self._mount_and_scroll(user_message)

        self.run_worker(
            self._process_user_message_after_mount(
                message=message,
                user_message=user_message,
                init_task=init_task,
                pending_init=pending_init,
            ),
            exclusive=False,
        )

    async def _process_user_message_after_mount(
        self,
        message: str,
        user_message: UserMessage,
        init_task: asyncio.Task | None,
        pending_init: bool,
    ) -> None:
        try:
            if init_task and not init_task.done():
                loading = LoadingWidget()
                self._loading_widget = loading
                await self.query_one("#loading-area-content").mount(loading)

                try:
                    await init_task
                finally:
                    if self._loading_widget and self._loading_widget.parent:
                        await self._loading_widget.remove()
                        self._loading_widget = None
                    if pending_init:
                        await user_message.set_pending(False)
            elif pending_init:
                await user_message.set_pending(False)

            if pending_init and self._agent_init_interrupted:
                self._agent_init_interrupted = False
                return

            if self.agent and not self._agent_running:
                self._agent_task = asyncio.create_task(self._handle_agent_turn(message))
        except asyncio.CancelledError:
            self._agent_init_interrupted = False
            if pending_init:
                await user_message.set_pending(False)
            return

    async def _initialize_agent(self) -> None:
        if self.agent or self._agent_initializing:
            return

        self._agent_initializing = True
        try:
            agent = Agent(
                self.config,
                mode=self._current_agent_mode,
                enable_streaming=self.enable_streaming,
            )

            if not self._current_agent_mode.auto_approve:
                agent.approval_callback = self._approval_callback

            if self._loaded_messages:
                non_system_messages = [
                    msg
                    for msg in self._loaded_messages
                    if not (msg.role == Role.system)
                ]
                agent.messages.extend(non_system_messages)
                logger.info(
                    "Loaded %d messages from previous session", len(non_system_messages)
                )

            self.agent = agent
        except asyncio.CancelledError:
            self.agent = None
            return
        except Exception as e:
            self.agent = None
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
        finally:
            self._agent_initializing = False
            self._agent_init_task = None

    async def _rebuild_history_from_messages(self) -> None:
        if not self._loaded_messages:
            return

        messages_area = self.query_one("#messages")
        tool_call_map: dict[str, str] = {}

        for msg in self._loaded_messages:
            if msg.role == Role.system:
                continue

            match msg.role:
                case Role.user:
                    if msg.content:
                        await messages_area.mount(UserMessage(msg.content))

                case Role.assistant:
                    await self._mount_history_assistant_message(
                        msg, messages_area, tool_call_map
                    )

                case Role.tool:
                    tool_name = msg.name or tool_call_map.get(
                        msg.tool_call_id or "", "tool"
                    )
                    await messages_area.mount(
                        ToolResultMessage(
                            tool_name=tool_name,
                            content=msg.content,
                            collapsed=self._tools_collapsed,
                        )
                    )

    async def _mount_history_assistant_message(
        self, msg: LLMMessage, messages_area: Widget, tool_call_map: dict[str, str]
    ) -> None:
        if msg.content:
            widget = AssistantMessage(msg.content)
            await messages_area.mount(widget)
            await widget.write_initial_content()
            await widget.stop_stream()

        if not msg.tool_calls:
            return

        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name or "unknown"
            if tool_call.id:
                tool_call_map[tool_call.id] = tool_name

            await messages_area.mount(ToolCallMessage(tool_name=tool_name))

    def _ensure_agent_init_task(self) -> asyncio.Task | None:
        if self.agent:
            self._agent_init_task = None
            self._agent_init_interrupted = False
            return None

        if self._agent_init_task and self._agent_init_task.done():
            if self._agent_init_task.cancelled():
                self._agent_init_task = None

        if not self._agent_init_task or self._agent_init_task.done():
            self._agent_init_interrupted = False
            self._agent_init_task = asyncio.create_task(self._initialize_agent())

        return self._agent_init_task

    async def _approval_callback(
        self, tool: str, args: BaseModel, tool_call_id: str
    ) -> tuple[ApprovalResponse, str | None]:
        self._pending_approval = asyncio.Future()
        await self._switch_to_approval_app(tool, args)
        result = await self._pending_approval
        self._pending_approval = None
        return result

    async def _handle_agent_turn(self, prompt: str) -> None:
        if not self.agent:
            return

        self._agent_running = True

        loading_area = self.query_one("#loading-area-content")

        loading = LoadingWidget()
        self._loading_widget = loading
        await loading_area.mount(loading)
        self._show_todo_area()

        try:
            rendered_prompt = render_path_prompt(
                prompt, base_dir=self.config.effective_workdir
            )
            async for event in self.agent.act(rendered_prompt):
                if self._context_progress and self.agent:
                    current_state = self._context_progress.tokens
                    self._context_progress.tokens = TokenState(
                        max_tokens=current_state.max_tokens,
                        current_tokens=self.agent.stats.context_tokens,
                    )

                if self.event_handler:
                    await self.event_handler.handle_event(
                        event,
                        loading_active=self._loading_widget is not None,
                        loading_widget=self._loading_widget,
                    )

        except asyncio.CancelledError:
            if self._loading_widget and self._loading_widget.parent:
                await self._loading_widget.remove()
            if self.event_handler:
                self.event_handler.stop_current_tool_call()
            raise
        except Exception as e:
            if self._loading_widget and self._loading_widget.parent:
                await self._loading_widget.remove()
            if self.event_handler:
                self.event_handler.stop_current_tool_call()
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
        finally:
            self._agent_running = False
            self._interrupt_requested = False
            self._agent_task = None
            if self._loading_widget:
                await self._loading_widget.remove()
            self._loading_widget = None
            self._hide_todo_area()
            await self._finalize_current_streaming_message()

    async def _interrupt_agent(self) -> None:
        interrupting_agent_init = bool(
            self._agent_init_task and not self._agent_init_task.done()
        )

        if (
            not self._agent_running and not interrupting_agent_init
        ) or self._interrupt_requested:
            return

        self._interrupt_requested = True

        if interrupting_agent_init and self._agent_init_task:
            self._agent_init_interrupted = True
            self._agent_init_task.cancel()
            try:
                await self._agent_init_task
            except asyncio.CancelledError:
                pass

        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass

        if self.event_handler:
            self.event_handler.stop_current_tool_call()
            self.event_handler.stop_current_compact()

        self._agent_running = False
        loading_area = self.query_one("#loading-area-content")
        await loading_area.remove_children()
        self._loading_widget = None
        self._hide_todo_area()

        await self._finalize_current_streaming_message()
        await self._mount_and_scroll(InterruptMessage())

        self._interrupt_requested = False

    async def _show_help(self) -> None:
        help_text = self.commands.get_help_text()
        await self._mount_and_scroll(UserCommandMessage(help_text))

    async def _show_status(self) -> None:
        if self.agent is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Agent not initialized yet. Send a message first.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        stats = self.agent.stats
        status_text = f"""## Agent Statistics

- **Steps**: {stats.steps:,}
- **Session Prompt Tokens**: {stats.session_prompt_tokens:,}
- **Session Completion Tokens**: {stats.session_completion_tokens:,}
- **Session Total LLM Tokens**: {stats.session_total_llm_tokens:,}
- **Last Turn Tokens**: {stats.last_turn_total_tokens:,}
- **Cost**: ${stats.session_cost:.4f}
"""
        await self._mount_and_scroll(UserCommandMessage(status_text))

    async def _show_config(self) -> None:
        """Switch to the configuration app in the bottom panel."""
        if self._current_bottom_app == BottomApp.Config:
            return
        await self._switch_to_config_app()

    async def _reload_config(self) -> None:
        try:
            new_config = VibeConfig.load(**self._current_agent_mode.config_overrides)

            if self.agent:
                await self.agent.reload_with_initial_messages(config=new_config)

            self.config = new_config
            if self._context_progress:
                if self.config.auto_compact_threshold > 0:
                    current_tokens = (
                        self.agent.stats.context_tokens if self.agent else 0
                    )
                    self._context_progress.tokens = TokenState(
                        max_tokens=self.config.auto_compact_threshold,
                        current_tokens=current_tokens,
                    )
                else:
                    self._context_progress.tokens = TokenState()

            await self._mount_and_scroll(UserCommandMessage("Configuration reloaded."))
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to reload config: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _clear_history(self) -> None:
        if self.agent is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to clear yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.agent:
            return

        try:
            await self.agent.clear_history()
            await self._finalize_current_streaming_message()
            messages_area = self.query_one("#messages")
            await messages_area.remove_children()
            todo_area = self.query_one("#todo-area")
            await todo_area.remove_children()

            if self._context_progress and self.agent:
                current_state = self._context_progress.tokens
                self._context_progress.tokens = TokenState(
                    max_tokens=current_state.max_tokens,
                    current_tokens=self.agent.stats.context_tokens,
                )
            await messages_area.mount(UserMessage("/clear"))
            await self._mount_and_scroll(
                UserCommandMessage("Conversation history cleared!")
            )
            chat = self.query_one("#chat", VerticalScroll)
            chat.scroll_home(animate=False)

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to clear history: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _show_log_path(self) -> None:
        if self.agent is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No log file created yet. Send a message first.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.agent.interaction_logger.enabled:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Session logging is disabled in configuration.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        try:
            log_path = str(self.agent.interaction_logger.filepath)
            await self._mount_and_scroll(
                UserCommandMessage(
                    f"## Current Log File Path\n\n`{log_path}`\n\nYou can send this file to share your interaction."
                )
            )
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to get log path: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _compact_history(self) -> None:
        if self._agent_running:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Cannot compact while agent is processing. Please wait.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if self.agent is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to compact yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if len(self.agent.messages) <= 1:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to compact yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.agent or not self.event_handler:
            return

        old_tokens = self.agent.stats.context_tokens
        compact_msg = CompactMessage()
        self.event_handler.current_compact = compact_msg
        await self._mount_and_scroll(compact_msg)

        self._agent_task = asyncio.create_task(
            self._run_compact(compact_msg, old_tokens)
        )

    async def _run_compact(self, compact_msg: CompactMessage, old_tokens: int) -> None:
        self._agent_running = True
        try:
            if not self.agent:
                return

            await self.agent.compact()
            new_tokens = self.agent.stats.context_tokens
            compact_msg.set_complete(old_tokens=old_tokens, new_tokens=new_tokens)

            if self._context_progress:
                current_state = self._context_progress.tokens
                self._context_progress.tokens = TokenState(
                    max_tokens=current_state.max_tokens, current_tokens=new_tokens
                )
        except asyncio.CancelledError:
            compact_msg.set_error("Compaction interrupted")
            raise
        except Exception as e:
            compact_msg.set_error(str(e))
        finally:
            self._agent_running = False
            self._agent_task = None
            if self.event_handler:
                self.event_handler.current_compact = None

    def _get_session_resume_info(self) -> str | None:
        if not self.agent:
            return None
        if not self.agent.interaction_logger.enabled:
            return None
        if not self.agent.interaction_logger.session_id:
            return None
        return self.agent.interaction_logger.session_id[:8]

    async def _save_conversation(self) -> None:
        """Save current conversation history to a file with user-provided name."""
        if not self.agent or not self.agent.messages:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to save.", collapsed=self._tools_collapsed
                )
            )
            return

        # Always show the save dialog with folder selection
        await self._switch_to_save_dialog()

    async def _switch_to_save_dialog(self) -> None:
        """Switch to the save dialog in the bottom panel."""
        if self._current_bottom_app == BottomApp.InputDialog:
            return

        bottom_container = self.query_one("#bottom-app-container")

        # Remove chat input if present
        try:
            chat_input_container = self.query_one(ChatInputContainer)
            await chat_input_container.remove()
        except Exception:
            pass

        # Hide mode indicator
        if self._mode_indicator:
            self._mode_indicator.display = False

        # Get list of available folders
        folders = await self._get_available_folders()

        # Show folder selector first
        folder_selector = FolderSelector(folders=folders)
        await bottom_container.mount(folder_selector)
        self._current_bottom_app = BottomApp.FolderSelector

        self.call_after_refresh(folder_selector.focus)

    async def _get_available_folders(self) -> list[str]:
        """Get list of available folders in conversations directory."""
        conv_dir = ensure_conversations_dir_exists()

        folders = []
        for item in conv_dir.iterdir():
            if item.is_dir():
                folders.append(item.name)

        return sorted(folders)

    def _generate_auto_conversation_name(self) -> str:
        """Generate an automatic name from the conversation context."""
        import re

        if not self.agent or not self.agent.messages:
            return "conversation"

        # Try to get first user message
        first_user_msg = None
        for msg in self.agent.messages:
            if msg.role == Role.user and msg.content:
                first_user_msg = msg.content[:50]  # First 50 chars
                break

        if not first_user_msg:
            return "conversation"

        # Clean filename - strip spaces and replace special characters
        safe_name = re.sub(
            r'[\\/:*?"<>|]', "_", first_user_msg.strip().replace(" ", "_")
        )
        return safe_name

    async def on_input_dialog_input_submitted(
        self, message: InputDialog.InputSubmitted
    ) -> None:
        """Handle when user submits a conversation name or folder name."""
        # Check if this is a folder creation dialog by checking the InputDialog's is_folder_creation flag
        input_dialog = self.query_one(InputDialog)
        is_folder_creation = getattr(input_dialog, "is_folder_creation", False)

        if is_folder_creation:
            # This is a folder creation submission
            folder_name = message.value.strip()
            if folder_name:
                try:
                    import re

                    # Create conversations directory if it doesn't exist
                    conv_dir = ensure_conversations_dir_exists()

                    # Clean folder name (preserve case)
                    safe_name = re.sub(r'[\\/:*?"<>|]', "_", folder_name.strip())
                    folder_path = conv_dir / safe_name

                    # Check if folder already exists
                    if folder_path.exists():
                        await self._mount_to_bottom_panel(
                            ErrorMessage(
                                f"Folder '{safe_name}' already exists!",
                                collapsed=self._tools_collapsed,
                            )
                        )
                        # Return to folder creation dialog
                        await self._show_folder_creation_dialog()
                        return

                    # Create folder
                    folder_path.mkdir(exist_ok=True)

                    # Show success message
                    await self._mount_and_scroll(
                        UserCommandMessage(f"✓ Created folder: {safe_name}")
                    )

                    # Return to folder selector to show the new folder
                    await self._show_folder_selector()

                except Exception as e:
                    await self._mount_to_bottom_panel(
                        ErrorMessage(
                            f"Failed to create folder: {e}",
                            collapsed=self._tools_collapsed,
                        )
                    )
                    await self._switch_to_input_app()
            else:
                await self._switch_to_input_app()
            return

        # This is a conversation save submission
        try:
            import json
            import re

            # Get the user-provided name or use auto-generated one
            conv_name = (
                message.value
                if message.value.strip()
                else self._generate_auto_conversation_name()
            )
            if not conv_name:
                conv_name = "conversation"

            # Clean filename - strip spaces and replace special characters for filename
            # But keep the original name with spaces for display
            safe_name = re.sub(
                r'[\\/:*?"<>|]', "_", conv_name.strip().replace(" ", "_")
            )

            timestamp = time.time()
            filename = f"{safe_name}_{int(timestamp)}.json"

            # Get save directory
            save_dir = ensure_conversations_dir_exists()

            # Check if a conversation with this exact name already exists in the save directory
            # Use the actual save directory (which may include selected folder)
            final_save_dir = save_dir
            if self._selected_folder:
                final_save_dir = save_dir / self._selected_folder

            if final_save_dir.exists():
                # Check for exact filename match (without timestamp)
                existing_files = list(final_save_dir.glob("*.json"))
                for f in existing_files:
                    # Extract the name before the timestamp
                    existing_filename = f.name
                    if "_" in existing_filename:
                        base_name = existing_filename.split("_")[0]
                        if base_name == safe_name:
                            await self._mount_to_bottom_panel(
                                ErrorMessage(
                                    f"A conversation named '{conv_name}' already exists! "
                                    f"File: {existing_filename}",
                                    collapsed=self._tools_collapsed,
                                )
                            )
                            # Return to conversation naming dialog for retry
                            await self._show_conversation_naming_dialog(conv_name)
                            return

            # Use selected folder if specified
            if self._selected_folder:
                folder_path = save_dir / self._selected_folder
                folder_path.mkdir(exist_ok=True)
                save_dir = folder_path

            filepath = save_dir / filename

            # Prepare messages for saving (exclude system prompt to save space)
            messages_to_save = []
            for msg in self.agent.messages:
                if msg.role != Role.system:  # Skip system prompt
                    messages_to_save.append(msg.model_dump())

            # Store the original name with spaces for display
            # Escape any special characters that might cause JSON issues
            display_name = conv_name.replace('"', '\\"').replace("\\", "\\\\")

            data = {
                "name": display_name,  # Store the user-friendly name with spaces
                "saved_at": timestamp,
                "messages": messages_to_save,
                "stats": self.agent.stats.model_dump() if self.agent.stats else None,
                "model": self.config.active_model,
                "provider": self.config.get_active_model().provider,
            }

            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)

            # Return to input mode
            await self._switch_to_input_app()

            await self._mount_and_scroll(
                UserCommandMessage(f"✓ Conversation saved as: {conv_name}")
            )

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to save conversation: {e}", collapsed=self._tools_collapsed
                )
            )
            await self._switch_to_input_app()

    async def on_input_dialog_input_cancelled(
        self, message: InputDialog.InputCancelled
    ) -> None:
        """Handle when user cancels the save dialog."""
        await self._switch_to_input_app()

    async def on_folder_selector_folder_selected(
        self, message: FolderSelector.FolderSelected
    ) -> None:
        """Handle when user selects a folder."""
        bottom_container = self.query_one("#bottom-app-container")

        # Remove folder selector
        try:
            folder_selector = self.query_one(FolderSelector)
            await folder_selector.remove()
        except Exception:
            pass

        # Store the selected folder for use in save
        self._selected_folder = message.folder_name

        # Create auto-generated name from first user message
        auto_name = self._generate_auto_conversation_name()

        # Show input dialog for conversation name
        input_dialog = InputDialog(title="Save Conversation", initial_value=auto_name)
        await bottom_container.mount(input_dialog)
        self._current_bottom_app = BottomApp.InputDialog

        # InputDialog will focus its input widget in on_mount, no need to focus the dialog

    async def on_input_blurred(self, event: Input.Blurred) -> None:
        """Handle when any input loses focus."""
        # Check if this is the folder selector's input widget (old implementation)
        if hasattr(event._sender, "parent") and hasattr(event._sender.parent, "id"):
            if event._sender.parent.id == "folder-selector" and isinstance(
                event._sender, Input
            ):
                folder_name = event._sender.value.strip()
                if folder_name:
                    await self._create_folder(folder_name, event._sender)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle when any input is submitted via Enter key."""
        # Check if this is the folder selector's input widget (old implementation)
        if hasattr(event.input, "parent") and hasattr(event.input.parent, "id"):
            if event.input.parent.id == "folder-selector" and isinstance(
                event.input, Input
            ):
                folder_name = event.input.value.strip()
                if folder_name:
                    await self._create_folder(folder_name, event.input)

    async def _create_folder(self, folder_name: str, input_widget: Input) -> None:
        """Helper method to create a folder."""
        try:
            from pathlib import Path
            import re

            # Create conversations directory if it doesn't exist
            conv_dir = ensure_conversations_dir_exists()

            # Clean folder name (preserve case)
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", folder_name.strip())
            folder_path = conv_dir / safe_name

            # Check if folder already exists
            if folder_path.exists():
                await self._mount_and_scroll(
                    ErrorMessage(
                        f"Folder '{safe_name}' already exists!",
                        collapsed=self._tools_collapsed,
                    )
                )
                # Return to folder creation dialog
                await self._show_folder_creation_dialog()
                return

            # Create folder
            folder_path.mkdir(exist_ok=True)

            # Remove the InputDialog (which contains the input widget)
            try:
                # Find and remove the parent InputDialog
                if hasattr(input_widget, "parent") and hasattr(
                    input_widget.parent, "id"
                ):
                    if input_widget.parent.id == "input-dialog":
                        await input_widget.parent.remove()
            except Exception:
                pass

            # Show success message
            await self._mount_and_scroll(
                UserCommandMessage(f"✓ Created folder: {safe_name}")
            )

            # Return to folder selector with updated folder list
            await self._show_folder_selector()

        except PermissionError:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Permission denied: Cannot create folder. Check your permissions for the conversations directory.",
                    collapsed=self._tools_collapsed,
                )
            )
        except OSError as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"System error: Cannot create folder. {e!s}",
                    collapsed=self._tools_collapsed,
                )
            )
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to create folder: {e}", collapsed=self._tools_collapsed
                )
            )
            # Try to clean up the InputDialog on error
            try:
                if hasattr(input_widget, "parent") and hasattr(
                    input_widget.parent, "id"
                ):
                    if input_widget.parent.id == "input-dialog":
                        await input_widget.parent.remove()
            except Exception:
                pass

            # Return to input mode on error
            await self._switch_to_input_app()

    async def on_folder_selector_folder_closed(
        self, message: FolderSelector.FolderClosed
    ) -> None:
        """Handle when user cancels folder selection."""
        await self._switch_to_input_app()

    async def _show_conversation_naming_dialog(self, initial_name: str = "") -> None:
        """Show the conversation naming dialog."""
        bottom_container = self.query_one("#bottom-app-container")

        # Remove any existing widgets
        try:
            folder_selector = self.query_one(FolderSelector)
            await folder_selector.remove()
        except Exception:
            pass

        try:
            input_dialog = self.query_one(InputDialog)
            await input_dialog.remove()
        except Exception:
            pass

        # Show conversation naming dialog
        auto_name = (
            initial_name if initial_name else self._generate_auto_conversation_name()
        )
        input_dialog = InputDialog(title="Save Conversation", initial_value=auto_name)
        await bottom_container.mount(input_dialog)
        self._current_bottom_app = BottomApp.InputDialog

        self.call_after_refresh(input_dialog.focus)

    async def _show_folder_selector(self) -> None:
        """Show the folder selector with updated folder list."""
        bottom_container = self.query_one("#bottom-app-container")

        # Remove any existing widgets
        try:
            folder_selector = self.query_one(FolderSelector)
            await folder_selector.remove()
        except Exception:
            pass

        try:
            input_dialog = self.query_one(InputDialog)
            await input_dialog.remove()
        except Exception:
            pass

        # Get updated list of folders
        folders = await self._get_available_folders()

        # Show folder selector
        folder_selector = FolderSelector(folders=folders)
        await bottom_container.mount(folder_selector)
        self._current_bottom_app = BottomApp.FolderSelector

        self.call_after_refresh(folder_selector.focus)

    async def _show_folder_creation_dialog(self) -> None:
        """Show the folder creation dialog, ensuring any existing one is removed."""
        bottom_container = self.query_one("#bottom-app-container")

        # Remove any existing InputDialog to avoid duplicate IDs
        try:
            existing_dialog = self.query_one(InputDialog)
            await existing_dialog.remove()
        except Exception:
            pass

        # Remove current folder selector
        try:
            folder_selector = self.query_one(FolderSelector)
            await folder_selector.remove()
        except Exception:
            pass

        # Show folder creation dialog
        folder_dialog = InputDialog(
            title="Create Folder",
            initial_value="",
            show_folder_option=False,
            is_folder_creation=True,
        )
        await bottom_container.mount(folder_dialog)
        self._current_bottom_app = BottomApp.InputDialog

    async def on_folder_selector_create_folder(
        self, message: FolderSelector.CreateFolder
    ) -> None:
        """Handle when user wants to create a folder from folder selector."""
        await self._show_folder_creation_dialog()

    async def on_input_dialog_create_folder(
        self, message: InputDialog.CreateFolder
    ) -> None:
        """Handle when user wants to create a folder."""
        # Show a new input dialog for folder name
        bottom_container = self.query_one("#bottom-app-container")

        # Remove current dialog
        try:
            input_dialog = self.query_one(InputDialog)
            await input_dialog.remove()
        except Exception:
            pass

        # Show folder creation dialog
        folder_dialog = InputDialog(
            title="Create Folder",
            initial_value="",
            show_folder_option=False,
            is_folder_creation=True,
        )
        await bottom_container.mount(folder_dialog)
        self._current_bottom_app = BottomApp.InputDialog

        # InputDialog will focus its input widget in on_mount, no need to focus the dialog

    async def _load_conversation(self) -> None:
        """Load conversation history from a file with selection interface."""
        if self._agent_running:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Cannot load conversation while agent is processing. Please wait.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        await self._switch_to_load_selector()

    async def _switch_to_load_selector(self) -> None:
        """Switch to the conversation tree selector in the bottom panel."""
        if self._current_bottom_app == BottomApp.ConversationTreeSelector:
            return

        try:
            # Find conversation files and build tree structure
            conv_dir = ensure_conversations_dir_exists()

            # Build tree structure with folders and conversations
            tree_items = build_conversation_tree(conv_dir)

            if not tree_items:
                await self._mount_and_scroll(
                    ErrorMessage(
                        "No saved conversations found.", collapsed=self._tools_collapsed
                    )
                )
                return

            bottom_container = self.query_one("#bottom-app-container")

            # Remove chat input if present
            try:
                chat_input_container = self.query_one(ChatInputContainer)
                await chat_input_container.remove()
            except Exception:
                pass

            # Hide mode indicator
            if self._mode_indicator:
                self._mode_indicator.display = False

            # Show conversation tree selector
            conversation_selector = ConversationTreeSelector(conversations=tree_items)
            await bottom_container.mount(conversation_selector)
            self._current_bottom_app = BottomApp.ConversationTreeSelector

            self.call_after_refresh(conversation_selector.focus)

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to load conversation: {e}", collapsed=self._tools_collapsed
                )
            )

    async def on_conversation_tree_selector_conversation_selected(
        self, message: ConversationTreeSelector.ConversationSelected
    ) -> None:
        """Handle when user selects a conversation to load from tree."""
        try:
            data, messages = load_conversation_from_file(message.filepath, self.agent)

            if not messages:
                await self._mount_and_scroll(
                    ErrorMessage(
                        "No messages found in saved conversation.",
                        collapsed=self._tools_collapsed,
                    )
                )
                await self._switch_to_input_app()
                return

            # Return to input mode
            await self._switch_to_input_app()

            await self._mount_and_scroll(
                UserCommandMessage(
                    f"✓ Loaded conversation: {data.get('name', message.filepath.name)}"
                    f"\n  - {len(messages)} messages loaded"
                    f"\n  - Model: {data.get('model', 'unknown')}"
                )
            )

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to load conversation: {e}", collapsed=self._tools_collapsed
                )
            )
            await self._switch_to_input_app()

    async def on_conversation_tree_selector_conversation_closed(
        self, message: ConversationTreeSelector.ConversationClosed
    ) -> None:
        """Handle when user cancels conversation selection from tree."""
        await self._switch_to_input_app()

    async def _exit_app(self) -> None:
        self.exit(result=self._get_session_resume_info())

    async def _setup_terminal(self) -> None:
        result = setup_terminal()

        if result.success:
            if result.requires_restart:
                await self._mount_and_scroll(
                    UserCommandMessage(
                        f"{result.terminal.value}: Set up Shift+Enter keybind (You may need to restart your terminal.)"
                    )
                )
            else:
                await self._mount_and_scroll(
                    WarningMessage(
                        f"{result.terminal.value}: Shift+Enter keybind already set up"
                    )
                )
        else:
            await self._mount_and_scroll(
                ErrorMessage(result.message, collapsed=self._tools_collapsed)
            )

    async def _switch_to_config_app(self) -> None:
        if self._current_bottom_app == BottomApp.Config:
            return

        bottom_container = self.query_one("#bottom-app-container")
        await self._mount_and_scroll(UserCommandMessage("Configuration opened..."))

        try:
            chat_input_container = self.query_one(ChatInputContainer)
            await chat_input_container.remove()
        except Exception:
            pass

        if self._mode_indicator:
            self._mode_indicator.display = False

        config_app = ConfigApp(
            self.config, has_terminal_theme=self._terminal_theme is not None
        )
        await bottom_container.mount(config_app)
        self._current_bottom_app = BottomApp.Config

        self.call_after_refresh(config_app.focus)

    async def _switch_to_approval_app(
        self, tool_name: str, tool_args: BaseModel
    ) -> None:
        bottom_container = self.query_one("#bottom-app-container")

        try:
            chat_input_container = self.query_one(ChatInputContainer)
            await chat_input_container.remove()
        except Exception:
            pass

        if self._mode_indicator:
            self._mode_indicator.display = False

        approval_app = ApprovalApp(
            tool_name=tool_name,
            tool_args=tool_args,
            workdir=str(self.config.effective_workdir),
            config=self.config,
        )
        await bottom_container.mount(approval_app)
        self._current_bottom_app = BottomApp.Approval

        self.call_after_refresh(approval_app.focus)
        self.call_after_refresh(self._scroll_to_bottom)

    async def _switch_to_input_app(self) -> None:
        bottom_container = self.query_one("#bottom-app-container")

        try:
            config_app = self.query_one("#config-app")
            await config_app.remove()
        except Exception:
            pass

        try:
            approval_app = self.query_one("#approval-app")
            await approval_app.remove()
        except Exception:
            pass

        try:
            input_dialog = self.query_one("#input-dialog")
            await input_dialog.remove()
        except Exception:
            pass

        try:
            conversation_selector = self.query_one("#conversation-selector")
            await conversation_selector.remove()
        except Exception:
            pass

        try:
            conversation_tree_selector = self.query_one("#conversation-tree-selector")
            await conversation_tree_selector.remove()
        except Exception:
            pass

        try:
            folder_selector = self.query_one("#folder-selector")
            await folder_selector.remove()
        except Exception:
            pass

        if self._mode_indicator:
            self._mode_indicator.display = True

        try:
            chat_input_container = self.query_one(ChatInputContainer)
            self._chat_input_container = chat_input_container
            self._current_bottom_app = BottomApp.Input
            self.call_after_refresh(chat_input_container.focus_input)
            return
        except Exception:
            pass

        chat_input_container = ChatInputContainer(
            history_file=self.history_file,
            command_registry=self.commands,
            id="input-container",
            safety=self._current_agent_mode.safety,
        )
        await bottom_container.mount(chat_input_container)
        self._chat_input_container = chat_input_container

        self._current_bottom_app = BottomApp.Input

        self.call_after_refresh(chat_input_container.focus_input)

    def _focus_current_bottom_app(self) -> None:
        try:
            match self._current_bottom_app:
                case BottomApp.Input:
                    self.query_one(ChatInputContainer).focus_input()
                case BottomApp.Config:
                    self.query_one(ConfigApp).focus()
                case BottomApp.Approval:
                    self.query_one(ApprovalApp).focus()
                case BottomApp.InputDialog:
                    self.query_one(InputDialog).focus()
                case BottomApp.ConversationTreeSelector:
                    self.query_one(ConversationTreeSelector).focus()
                case BottomApp.FolderSelector:
                    self.query_one(FolderSelector).focus()
                case app:
                    assert_never(app)
        except Exception:
            pass

    def action_interrupt(self) -> None:
        current_time = time.monotonic()

        if self._current_bottom_app == BottomApp.Config:
            try:
                config_app = self.query_one(ConfigApp)
                config_app.action_close()
            except Exception:
                pass
            self._last_escape_time = None
            return

        if self._current_bottom_app == BottomApp.Approval:
            try:
                approval_app = self.query_one(ApprovalApp)
                approval_app.action_reject()
            except Exception:
                pass
            self._last_escape_time = None
            return

        if (
            self._current_bottom_app == BottomApp.Input
            and self._last_escape_time is not None
            and (current_time - self._last_escape_time) < 0.2  # noqa: PLR2004
        ):
            try:
                input_widget = self.query_one(ChatInputContainer)
                if input_widget.value:
                    input_widget.value = ""
                    self._last_escape_time = None
                    return
            except Exception:
                pass
        if self._current_bottom_app == BottomApp.InputDialog:
            try:
                input_dialog = self.query_one(InputDialog)
                input_dialog.action_cancel()
            except Exception:
                pass
            return

        if self._current_bottom_app == BottomApp.ConversationTreeSelector:
            try:
                conversation_selector = self.query_one(ConversationTreeSelector)
                conversation_selector.action_close()
            except Exception:
                pass
            return

        if self._current_bottom_app == BottomApp.FolderSelector:
            try:
                folder_selector = self.query_one(FolderSelector)
                folder_selector.action_close()
            except Exception:
                pass
            return

        has_pending_user_message = any(
            msg.has_class("pending") for msg in self.query(UserMessage)
        )

        interrupt_needed = self._agent_running or (
            self._agent_init_task
            and not self._agent_init_task.done()
            and has_pending_user_message
        )

        if interrupt_needed:
            self.run_worker(self._interrupt_agent(), exclusive=False)

        self._last_escape_time = current_time
        self._scroll_to_bottom()
        self._focus_current_bottom_app()

    async def action_toggle_tool(self) -> None:
        self._tools_collapsed = not self._tools_collapsed

        for result in self.query(ToolResultMessage):
            if result.tool_name != "todo":
                await result.set_collapsed(self._tools_collapsed)

        try:
            for error_msg in self.query(ErrorMessage):
                error_msg.set_collapsed(self._tools_collapsed)
        except Exception:
            pass

    async def action_toggle_todo(self) -> None:
        self._todos_collapsed = not self._todos_collapsed

        for result in self.query(ToolResultMessage):
            if result.tool_name == "todo":
                await result.set_collapsed(self._todos_collapsed)

    def action_cycle_mode(self) -> None:
        if self._current_bottom_app != BottomApp.Input:
            return

        new_mode = next_mode(self._current_agent_mode)
        self._switch_mode(new_mode)

    def _switch_mode(self, mode: AgentMode) -> None:
        if mode == self._current_agent_mode:
            return

        self._current_agent_mode = mode

        if self._mode_indicator:
            self._mode_indicator.set_mode(mode)
        if self._chat_input_container:
            self._chat_input_container.set_safety(mode.safety)

        if self.agent:
            if mode.auto_approve:
                self.agent.approval_callback = None
            else:
                self.agent.approval_callback = self._approval_callback

            self.run_worker(
                self._do_agent_switch(mode), group="mode_switch", exclusive=True
            )

        self._focus_current_bottom_app()

    async def _do_agent_switch(self, mode: AgentMode) -> None:
        if self.agent:
            await self.agent.switch_mode(mode)

            if self._context_progress:
                current_state = self._context_progress.tokens
                self._context_progress.tokens = TokenState(
                    max_tokens=current_state.max_tokens,
                    current_tokens=self.agent.stats.context_tokens,
                )

    def action_clear_quit(self) -> None:
        input_widgets = self.query(ChatInputContainer)
        if input_widgets:
            input_widget = input_widgets.first()
            if input_widget.value:
                input_widget.value = ""
                return

        self.action_force_quit()

    def action_force_quit(self) -> None:
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()

        self.exit(result=self._get_session_resume_info())

    def action_scroll_chat_up(self) -> None:
        try:
            chat = self.query_one("#chat", VerticalScroll)
            chat.scroll_relative(y=-5, animate=False)
            self._auto_scroll = False
        except Exception:
            pass

    def action_scroll_chat_down(self) -> None:
        try:
            chat = self.query_one("#chat", VerticalScroll)
            chat.scroll_relative(y=5, animate=False)
            if self._is_scrolled_to_bottom(chat):
                self._auto_scroll = True
        except Exception:
            pass

    async def _show_dangerous_directory_warning(self) -> None:
        is_dangerous, reason = is_dangerous_directory()
        if is_dangerous:
            warning = (
                f"⚠ WARNING: {reason}\n\nRunning in this location is not recommended."
            )
            await self._mount_and_scroll(WarningMessage(warning, show_border=False))

    async def _finalize_current_streaming_message(self) -> None:
        if self._current_streaming_reasoning is not None:
            self._current_streaming_reasoning.stop_spinning()
            await self._current_streaming_reasoning.stop_stream()
            self._current_streaming_reasoning = None

        if self._current_streaming_message is None:
            return

        await self._current_streaming_message.stop_stream()
        self._current_streaming_message = None

    async def _handle_streaming_widget[T: StreamingMessageBase](
        self,
        widget: T,
        current_stream: T | None,
        other_stream: StreamingMessageBase | None,
        messages_area: Widget,
    ) -> T | None:
        if other_stream is not None:
            await other_stream.stop_stream()

        if current_stream is not None:
            if widget._content:
                await current_stream.append_content(widget._content)
            return None

        await messages_area.mount(widget)
        await widget.write_initial_content()
        return widget

    async def _mount_and_scroll(self, widget: Widget) -> None:
        messages_area = self.query_one("#messages")
        chat = self.query_one("#chat", VerticalScroll)
        was_at_bottom = self._is_scrolled_to_bottom(chat)

        if was_at_bottom:
            self._auto_scroll = True

        if isinstance(widget, ReasoningMessage):
            result = await self._handle_streaming_widget(
                widget,
                self._current_streaming_reasoning,
                self._current_streaming_message,
                messages_area,
            )
            if result is not None:
                self._current_streaming_reasoning = result
            self._current_streaming_message = None
        elif isinstance(widget, AssistantMessage):
            if self._current_streaming_reasoning is not None:
                self._current_streaming_reasoning.stop_spinning()
            result = await self._handle_streaming_widget(
                widget,
                self._current_streaming_message,
                self._current_streaming_reasoning,
                messages_area,
            )
            if result is not None:
                self._current_streaming_message = result
            self._current_streaming_reasoning = None
        else:
            await self._finalize_current_streaming_message()
            await messages_area.mount(widget)

            is_tool_message = isinstance(widget, (ToolCallMessage, ToolResultMessage))

            if not is_tool_message:
                self.call_after_refresh(self._scroll_to_bottom)

        if was_at_bottom:
            self.call_after_refresh(self._anchor_if_scrollable)

    async def _mount_to_bottom_panel(self, widget: Widget) -> None:
        """Mount a widget to the bottom panel (for save dialog errors)."""
        try:
            bottom_container = self.query_one("#bottom-app-container")

            # Create a container for the message
            from textual.containers import Vertical

            message_container = Vertical(id=f"bottom-message-container-{id(widget)}")

            # Add the widget
            message_container.mount(widget)

            # Mount to bottom panel
            await bottom_container.mount(message_container)

            # Auto-remove after a delay
            self.call_after_refresh(
                lambda: self.call_later(
                    self._remove_bottom_message, message_container, 5.0
                )
            )
        except Exception:
            # If bottom panel doesn't exist or can't be mounted, fall back to main messages
            await self._mount_and_scroll(widget)

    async def _remove_bottom_message(self, container: Vertical) -> None:
        """Remove a message container from the bottom panel."""
        try:
            await container.remove()
        except Exception:
            pass

    def _is_scrolled_to_bottom(self, scroll_view: VerticalScroll) -> bool:
        try:
            threshold = 3
            return scroll_view.scroll_y >= (scroll_view.max_scroll_y - threshold)
        except Exception:
            return True

    def _scroll_to_bottom(self) -> None:
        try:
            chat = self.query_one("#chat")
            chat.scroll_end(animate=False)
        except Exception:
            pass

    def _scroll_to_bottom_deferred(self) -> None:
        self.call_after_refresh(self._scroll_to_bottom)

    def _anchor_if_scrollable(self) -> None:
        if not self._auto_scroll:
            return
        try:
            chat = self.query_one("#chat", VerticalScroll)
            if chat.max_scroll_y == 0:
                return
            chat.anchor()
        except Exception:
            pass

    def _schedule_update_notification(self) -> None:
        if (
            self._version_update_notifier is None
            or self._update_notification_task
            or not self._is_update_check_enabled
        ):
            return

        self._update_notification_task = asyncio.create_task(
            self._check_version_update(), name="version-update-check"
        )

    async def _check_version_update(self) -> None:
        try:
            if (
                self._version_update_notifier is None
                or self._update_cache_repository is None
            ):
                return

            update = await get_update_if_available(
                version_update_notifier=self._version_update_notifier,
                current_version=self._current_version,
                update_cache_repository=self._update_cache_repository,
            )
        except VersionUpdateError as error:
            self.notify(
                error.message,
                title="Update check failed",
                severity="warning",
                timeout=10,
            )
            return
        except Exception as exc:
            logger.debug("Version update check failed", exc_info=exc)
            return
        finally:
            self._update_notification_task = None

        if update is None or not update.should_notify:
            return

        self._display_update_notification(update)

    def _display_update_notification(self, update: VersionUpdateAvailability) -> None:
        if self._update_notification_shown:
            return

        message = f'{self._current_version} => {update.latest_version}\nRun "uv tool upgrade mistral-vibe" to update'

        self.notify(
            message, title="Update available", severity="information", timeout=10
        )
        self._update_notification_shown = True

    def on_mouse_up(self, event: MouseUp) -> None:
        copy_selection_to_clipboard(self)

    def on_app_blur(self, event: AppBlur) -> None:
        if self._chat_input_container and self._chat_input_container.input_widget:
            self._chat_input_container.input_widget.set_app_focus(False)

    def on_app_focus(self, event: AppFocus) -> None:
        if self._chat_input_container and self._chat_input_container.input_widget:
            self._chat_input_container.input_widget.set_app_focus(True)


def _print_session_resume_message(session_id: str | None) -> None:
    if not session_id:
        return

    print()
    print("To continue this session, run: vibe --continue")
    print(f"Or: vibe --resume {session_id}")


def run_textual_ui(
    config: VibeConfig,
    initial_mode: AgentMode = AgentMode.DEFAULT,
    enable_streaming: bool = False,
    initial_prompt: str | None = None,
    loaded_messages: list[LLMMessage] | None = None,
) -> None:
    update_notifier = PyPIVersionUpdateGateway(project_name="mistral-vibe")
    update_cache_repository = FileSystemUpdateCacheRepository()
    app = VibeApp(
        config=config,
        initial_mode=initial_mode,
        enable_streaming=enable_streaming,
        initial_prompt=initial_prompt,
        loaded_messages=loaded_messages,
        version_update_notifier=update_notifier,
        update_cache_repository=update_cache_repository,
    )
    session_id = app.run()
    _print_session_resume_message(session_id)
