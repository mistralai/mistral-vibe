from __future__ import annotations

import asyncio
from enum import StrEnum, auto
import subprocess
from typing import Any, ClassVar, assert_never

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.events import MouseUp
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.config_app import ConfigApp
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.loading import LoadingWidget
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    BashOutputMessage,
    ErrorMessage,
    InterruptMessage,
    UserCommandMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.mode_indicator import ModeIndicator
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.session_manager_app import SessionManagerApp
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
from vibe.core import __version__ as CORE_VERSION
from vibe.core.agent import Agent
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import VibeConfig
from vibe.core.config_path import HISTORY_FILE
from vibe.core.interaction_logger import InteractionLogger
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.types import ApprovalResponse, LLMMessage, ResumeSessionInfo, Role
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
    SessionManager = auto()


class VibeApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "force_quit", "Quit", show=False),
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
        auto_approve: bool = False,
        enable_streaming: bool = False,
        initial_prompt: str | None = None,
        loaded_messages: list[LLMMessage] | None = None,
        session_info: ResumeSessionInfo | None = None,
        version_update_notifier: VersionUpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        current_version: str = CORE_VERSION,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.auto_approve = auto_approve
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
        self.theme = config.textual_theme

        self.history_file = HISTORY_FILE.path

        self._tools_collapsed = True
        self._todos_collapsed = False
        self._current_streaming_message: AssistantMessage | None = None
        self._version_update_notifier = version_update_notifier
        self._update_cache_repository = update_cache_repository
        self._is_update_check_enabled = config.enable_update_checks
        self._current_version = current_version
        self._update_notification_task: asyncio.Task | None = None
        self._update_notification_shown = False

        self._initial_prompt = initial_prompt
        self._loaded_messages = loaded_messages
        self._session_info = session_info
        self._agent_init_task: asyncio.Task | None = None
        # prevent a race condition where the agent initialization
        # completes exactly at the moment the user interrupts
        self._agent_init_interrupted = False
        self._auto_scroll = True

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat"):
            yield WelcomeBanner(self.config)
            yield Static(id="messages")

        with Horizontal(id="loading-area"):
            yield Static(id="loading-area-content")
            yield ModeIndicator(auto_approve=self.auto_approve)

        yield Static(id="todo-area")

        with Static(id="bottom-app-container"):
            yield ChatInputContainer(
                history_file=self.history_file,
                command_registry=self.commands,
                id="input-container",
                show_warning=self.auto_approve,
            )

        with Horizontal(id="bottom-bar"):
            yield PathDisplay(
                self.config.displayed_workdir or self.config.effective_workdir
            )
            yield Static(id="spacer")
            yield ContextProgress()

    async def on_mount(self) -> None:
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

        if self._session_info:
            await self._mount_and_scroll(AssistantMessage(self._session_info.message()))

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

    def on_config_app_setting_changed(self, message: ConfigApp.SettingChanged) -> None:
        if message.key == "textual_theme":
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
                auto_approve=self.auto_approve,
                enable_streaming=self.enable_streaming,
            )

            if not self.auto_approve:
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
        self, tool: str, args: dict, tool_call_id: str
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

    async def _show_session_manager(self) -> None:
        if not self.config.session_logging.enabled:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Session logging is disabled. Enable it in config to use session management.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        await self._switch_to_session_manager_app()

    async def _rename_session(self) -> None:
        await self._mount_and_scroll(
            UserCommandMessage(
                "Use `/session` to open the session manager, then press 'r' to rename any session."
            )
        )

    async def _show_config(self) -> None:
        """Switch to the configuration app in the bottom panel."""
        if self._current_bottom_app == BottomApp.Config:
            return
        await self._switch_to_config_app()

    async def _reload_config(self) -> None:
        try:
            new_config = VibeConfig.load()

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

        try:
            await self.agent.compact()
            new_tokens = self.agent.stats.context_tokens
            compact_msg.set_complete(old_tokens=old_tokens, new_tokens=new_tokens)
            self.event_handler.current_compact = None

            if self._context_progress:
                current_state = self._context_progress.tokens
                self._context_progress.tokens = TokenState(
                    max_tokens=current_state.max_tokens, current_tokens=new_tokens
                )
        except Exception as e:
            compact_msg.set_error(str(e))
            self.event_handler.current_compact = None

    async def _exit_app(self) -> None:
        self.exit()

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

        config_app = ConfigApp(self.config)
        await bottom_container.mount(config_app)
        self._current_bottom_app = BottomApp.Config

        self.call_after_refresh(config_app.focus)

    async def _switch_to_approval_app(self, tool_name: str, tool_args: dict) -> None:
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

    async def _switch_to_session_manager_app(self) -> None:
        if self._current_bottom_app == BottomApp.SessionManager:
            return

        # Save current session before opening manager to ensure latest state is visible
        if self.agent:
            await self.agent.interaction_logger.save_interaction(
                self.agent.messages,
                self.agent.stats,
                self.config,
                self.agent.tool_manager,
            )

        bottom_container = self.query_one("#bottom-app-container")
        await self._mount_and_scroll(UserCommandMessage("Session Manager opened..."))

        try:
            chat_input_container = self.query_one(ChatInputContainer)
            await chat_input_container.remove()
        except Exception:
            pass

        if self._mode_indicator:
            self._mode_indicator.display = False

        current_session_id = self.agent.session_id if self.agent else ""
        session_manager_app = SessionManagerApp(
            self.config.session_logging, current_session_id
        )
        await bottom_container.mount(session_manager_app)
        self._current_bottom_app = BottomApp.SessionManager

        self.call_after_refresh(session_manager_app.focus)

    async def on_session_manager_app_session_switched(
        self, message: SessionManagerApp.SessionSwitched
    ) -> None:
        await self._switch_to_input_app()
        await self._switch_to_session(message.session_id)

    async def on_session_manager_app_manager_closed(
        self, message: SessionManagerApp.ManagerClosed
    ) -> None:
        await self._switch_to_input_app()

    async def _load_session_data(
        self, session_id: str
    ) -> tuple[list[LLMMessage], dict[str, Any]] | None:
        """Load session data from disk."""
        target_session = InteractionLogger.find_session_by_id(
            session_id, self.config.session_logging
        )
        if not target_session:
            await self._mount_and_scroll(
                ErrorMessage("Session not found", collapsed=self._tools_collapsed)
            )
            return None
        return InteractionLogger.load_session(target_session)

    async def _create_agent_for_session(self, metadata: dict[str, Any]) -> Any:
        """Create a new agent for the loaded session."""
        from vibe.core.agent import Agent

        new_agent = Agent(
            config=self.config,
            auto_approve=self.auto_approve,
            enable_streaming=self.enable_streaming,
        )

        if not self.auto_approve:
            new_agent.approval_callback = self._approval_callback

        target_session_id = metadata.get("session_id", "unknown")
        session_name = metadata.get("name") or f"Session {target_session_id[:8]}"
        start_time = metadata.get("start_time")

        # Set the agent to use the target session_id instead of a new one
        new_agent.session_id = target_session_id
        new_agent.interaction_logger.reset_session(
            target_session_id,
            filepath=InteractionLogger.find_session_by_id(
                target_session_id, self.config.session_logging
            ),
            start_time=start_time,
        )

        # Update session name from loaded metadata
        if session_name and session_name != f"Session {target_session_id[:8]}":
            new_agent.interaction_logger.update_session_name(session_name)

        return new_agent, session_name

    async def _switch_to_session(self, session_id: str) -> None:
        try:
            if self.agent:
                await self.agent.interaction_logger.save_interaction(
                    self.agent.messages,
                    self.agent.stats,
                    self.config,
                    self.agent.tool_manager,
                )

            session_data = await self._load_session_data(session_id)
            if session_data is None:
                return

            loaded_messages, metadata = session_data

            await self._finalize_current_streaming_message()
            messages_area = self.query_one("#messages")
            await messages_area.remove_children()

            new_agent, session_name = await self._create_agent_for_session(metadata)

            if loaded_messages:
                non_system_messages = [
                    msg for msg in loaded_messages if not (msg.role == Role.system)
                ]
                new_agent.messages.extend(non_system_messages)

            self.agent = new_agent

            # Show the latest interaction from the loaded session
            await self._show_session_switch_messages(loaded_messages, session_name)

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to switch session: {e!s}", collapsed=self._tools_collapsed
                )
            )
            logger.exception("Error switching to session %s", session_id)

    async def _show_session_switch_messages(
        self, loaded_messages: list[LLMMessage], session_name: str
    ) -> None:
        """Display appropriate messages after switching to a session."""
        if not loaded_messages:
            await self._mount_and_scroll(
                UserCommandMessage(f"✓ Switched to: {session_name} (empty session)")
            )
            return

        # Find the last user message and its corresponding assistant response
        last_user_idx = -1
        for i, msg in enumerate(reversed(loaded_messages)):
            if msg.role == Role.user:
                last_user_idx = len(loaded_messages) - 1 - i
                break

        if last_user_idx == -1:
            # No user messages found, just show summary
            await self._mount_and_scroll(
                UserCommandMessage(
                    f"✓ Switched to: {session_name} ({len(loaded_messages)} messages)"
                )
            )
            return

        # Show the last user message
        user_msg = loaded_messages[last_user_idx]
        if user_msg.content:
            await self._mount_and_scroll(UserMessage(str(user_msg.content)))

        # Show the assistant response if it exists
        if (
            last_user_idx + 1 < len(loaded_messages)
            and loaded_messages[last_user_idx + 1].role == Role.assistant
        ):
            assistant_msg = loaded_messages[last_user_idx + 1]
            if assistant_msg.content:
                await self._mount_and_scroll(
                    AssistantMessage(str(assistant_msg.content))
                )

        await self._mount_and_scroll(
            UserCommandMessage(
                f"✓ Switched to: {session_name} ({len(loaded_messages)} messages)"
            )
        )

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
            session_manager_app = self.query_one("#session-manager-app")
            await session_manager_app.remove()
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
            show_warning=self.auto_approve,
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
                case BottomApp.SessionManager:
                    self.query_one(SessionManagerApp).focus()
                case app:
                    assert_never(app)
        except Exception:
            pass

    def action_interrupt(self) -> None:
        if self._current_bottom_app == BottomApp.Config:
            try:
                config_app = self.query_one(ConfigApp)
                config_app.action_close()
            except Exception:
                pass
            return

        if self._current_bottom_app == BottomApp.Approval:
            try:
                approval_app = self.query_one(ApprovalApp)
                approval_app.action_reject()
            except Exception:
                pass
            return

        if self._current_bottom_app == BottomApp.SessionManager:
            try:
                session_manager_app = self.query_one(SessionManagerApp)
                session_manager_app.action_close()
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

        self._scroll_to_bottom()
        self._focus_current_bottom_app()

    async def action_toggle_tool(self) -> None:
        if not self.event_handler:
            return

        self._tools_collapsed = not self._tools_collapsed

        non_todo_results = [
            result
            for result in self.event_handler.tool_results
            if result.event.tool_name != "todo"
        ]

        for result in non_todo_results:
            result.collapsed = self._tools_collapsed
            await result.render_result()

        try:
            error_messages = self.query(ErrorMessage)
            for error_msg in error_messages:
                error_msg.set_collapsed(self._tools_collapsed)
        except Exception:
            pass

    async def action_toggle_todo(self) -> None:
        if not self.event_handler:
            return

        self._todos_collapsed = not self._todos_collapsed

        todo_results = [
            result
            for result in self.event_handler.tool_results
            if result.event.tool_name == "todo"
        ]

        for result in todo_results:
            result.collapsed = self._todos_collapsed
            await result.render_result()

    def action_cycle_mode(self) -> None:
        if self._current_bottom_app != BottomApp.Input:
            return

        self.auto_approve = not self.auto_approve

        if self._mode_indicator:
            self._mode_indicator.set_auto_approve(self.auto_approve)

        if self._chat_input_container:
            self._chat_input_container.set_show_warning(self.auto_approve)

        if self.agent:
            self.agent.auto_approve = self.auto_approve

            if self.auto_approve:
                self.agent.approval_callback = None
            else:
                self.agent.approval_callback = self._approval_callback

        self._focus_current_bottom_app()

    def action_force_quit(self) -> None:
        input_widgets = self.query(ChatInputContainer)
        if input_widgets:
            input_widget = input_widgets.first()
            if input_widget.value:
                input_widget.value = ""
                return

        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()

        self.exit()

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
                f"⚠️ WARNING: {reason}\n\nRunning in this location is not recommended."
            )
            await self._mount_and_scroll(UserCommandMessage(warning))

    async def _finalize_current_streaming_message(self) -> None:
        if self._current_streaming_message is None:
            return

        await self._current_streaming_message.stop_stream()
        self._current_streaming_message = None

    async def _mount_and_scroll(self, widget: Widget) -> None:
        messages_area = self.query_one("#messages")
        chat = self.query_one("#chat", VerticalScroll)
        was_at_bottom = self._is_scrolled_to_bottom(chat)

        if was_at_bottom:
            self._auto_scroll = True

        if isinstance(widget, AssistantMessage):
            if self._current_streaming_message is not None:
                content = widget._content or ""
                if content:
                    await self._current_streaming_message.append_content(content)
            else:
                self._current_streaming_message = widget
                await messages_area.mount(widget)
                await widget.write_initial_content()
        else:
            await self._finalize_current_streaming_message()
            await messages_area.mount(widget)

            is_tool_message = isinstance(widget, (ToolCallMessage, ToolResultMessage))

            if not is_tool_message:
                self.call_after_refresh(self._scroll_to_bottom)

        if was_at_bottom:
            self.call_after_refresh(self._anchor_if_scrollable)

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


def run_textual_ui(
    config: VibeConfig,
    auto_approve: bool = False,
    enable_streaming: bool = False,
    initial_prompt: str | None = None,
    loaded_messages: list[LLMMessage] | None = None,
    session_info: ResumeSessionInfo | None = None,
) -> None:
    update_notifier = PyPIVersionUpdateGateway(project_name="mistral-vibe")
    update_cache_repository = FileSystemUpdateCacheRepository()
    app = VibeApp(
        config=config,
        auto_approve=auto_approve,
        enable_streaming=enable_streaming,
        initial_prompt=initial_prompt,
        loaded_messages=loaded_messages,
        session_info=session_info,
        version_update_notifier=update_notifier,
        update_cache_repository=update_cache_repository,
    )
    app.run()
