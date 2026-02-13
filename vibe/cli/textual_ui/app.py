from __future__ import annotations

import asyncio
from enum import StrEnum, auto
from os import getenv
from pathlib import Path
import subprocess
import time
from typing import Any, ClassVar, assert_never, cast

from pydantic import BaseModel
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.events import AppBlur, AppFocus, MouseUp
from textual.widget import Widget
from textual.widgets import Static

from vibe import __version__ as CORE_VERSION
from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.cli.commands import CommandRegistry
from vibe.cli.plan_offer.adapters.http_whoami_gateway import HttpWhoAmIGateway
from vibe.cli.plan_offer.decide_plan_offer import (
    ACTION_TO_URL,
    PlanOfferAction,
    PlanType,
    decide_plan_offer,
)
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIGateway
from vibe.cli.terminal_setup import setup_terminal
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.terminal_theme import (
    TERMINAL_THEME_NAME,
    capture_terminal_theme,
)
from vibe.cli.textual_ui.widgets.agent_indicator import AgentIndicator
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.config_app import ConfigApp
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.loading import LoadingWidget, paused_timer
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    BashOutputMessage,
    ErrorMessage,
    InterruptMessage,
    PlanOfferMessage,
    ReasoningMessage,
    StreamingMessageBase,
    UserCommandMessage,
    UserMessage,
    WarningMessage,
    WhatsNewMessage,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.cli.textual_ui.widgets.welcome import WelcomeBanner
from vibe.cli.update_notifier import (
    FileSystemUpdateCacheRepository,
    PyPIUpdateGateway,
    UpdateCacheRepository,
    UpdateError,
    UpdateGateway,
    get_update_if_available,
    load_whats_new_content,
    mark_version_as_seen,
    should_show_whats_new,
)
from vibe.cli.update_notifier.update import do_update
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents import AgentProfile
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import VibeConfig
from vibe.core.paths.config_paths import HISTORY_FILE
from vibe.core.session.session_loader import SessionLoader
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    AskUserQuestionResult,
)
from vibe.core.types import (
    AgentStats,
    ApprovalResponse,
    LLMMessage,
    RateLimitError,
    Role,
)
from vibe.core.utils import (
    CancellationReason,
    get_user_cancellation_message,
    is_dangerous_directory,
    logger,
)


class BottomApp(StrEnum):
    """Bottom panel app types.

    Convention: Each value must match the widget class name with "App" suffix removed.
    E.g., ApprovalApp -> Approval, ConfigApp -> Config, QuestionApp -> Question.
    This allows dynamic lookup via: BottomApp[type(widget).__name__.removesuffix("App")]
    """

    Approval = auto()
    Config = auto()
    Input = auto()
    Question = auto()


class VibeApp(App):  # noqa: PLR0904
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
        Binding("ctrl+y", "toggle_memory", "Toggle Memory", show=False),
        Binding("ctrl+u", "show_memory", "Show Memory", show=False),
    ]

    def __init__(
        self,
        agent_loop: AgentLoop,
        initial_prompt: str | None = None,
        update_notifier: UpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        current_version: str = CORE_VERSION,
        plan_offer_gateway: WhoAmIGateway | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.agent_loop = agent_loop
        self._agent_running = False
        self._interrupt_requested = False
        self._agent_task: asyncio.Task | None = None

        self._loading_widget: LoadingWidget | None = None
        self._pending_approval: asyncio.Future | None = None
        self._pending_question: asyncio.Future | None = None

        self.event_handler: EventHandler | None = None
        self.commands = CommandRegistry()

        self._chat_input_container: ChatInputContainer | None = None
        self._agent_indicator: AgentIndicator | None = None
        self._current_bottom_app: BottomApp = BottomApp.Input

        self.history_file = HISTORY_FILE.path

        self._tools_collapsed = True
        self._todos_collapsed = False
        self._current_streaming_message: AssistantMessage | None = None
        self._current_streaming_reasoning: ReasoningMessage | None = None
        self._update_notifier = update_notifier
        self._update_cache_repository = update_cache_repository
        self._current_version = current_version
        self._plan_offer_gateway = plan_offer_gateway
        self._plan_offer_shown = False
        self._initial_prompt = initial_prompt
        self._auto_scroll = True
        self._last_escape_time: float | None = None
        self._terminal_theme = capture_terminal_theme()

    @property
    def config(self) -> VibeConfig:
        return self.agent_loop.config

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat"):
            yield WelcomeBanner(self.config)
            yield Static(id="messages")

        with Horizontal(id="loading-area"):
            yield Static(id="loading-area-content")
            yield AgentIndicator(profile=self.agent_loop.agent_profile)

        yield Static(id="todo-area")

        with Static(id="bottom-app-container"):
            yield ChatInputContainer(
                history_file=self.history_file,
                command_registry=self.commands,
                id="input-container",
                safety=self.agent_loop.agent_profile.safety,
                skill_entries_getter=self._get_skill_entries,
            )

        with Horizontal(id="bottom-bar"):
            yield PathDisplay(self.config.displayed_workdir or Path.cwd())
            yield NoMarkupStatic(id="spacer")
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
        self._agent_indicator = self.query_one(AgentIndicator)
        context_progress = self.query_one(ContextProgress)

        def update_context_progress(stats: AgentStats) -> None:
            context_progress.tokens = TokenState(
                max_tokens=self.config.auto_compact_threshold,
                current_tokens=stats.context_tokens,
            )

        AgentStats.add_listener("context_tokens", update_context_progress)
        self.agent_loop.stats.trigger_listeners()

        self.agent_loop.set_approval_callback(self._approval_callback)
        self.agent_loop.set_user_input_callback(self._user_input_callback)
        self._refresh_profile_widgets()

        chat_input_container = self.query_one(ChatInputContainer)
        chat_input_container.focus_input()
        await self._show_dangerous_directory_warning()
        await self._check_and_show_whats_new()
        await self._maybe_show_plan_offer()
        self._schedule_update_notification()

        await self._rebuild_history_from_messages()

        if self._initial_prompt:
            self.call_after_refresh(self._process_initial_prompt)

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
            await self._interrupt_agent_loop()

        if value.startswith("!"):
            await self._handle_bash_command(value[1:])
            return

        if await self._handle_command(value):
            return

        if await self._handle_skill(value):
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

    async def on_question_app_answered(self, message: QuestionApp.Answered) -> None:
        if self._pending_question and not self._pending_question.done():
            result = AskUserQuestionResult(answers=message.answers, cancelled=False)
            self._pending_question.set_result(result)

        await self._switch_to_input_app()

    async def on_question_app_cancelled(self, message: QuestionApp.Cancelled) -> None:
        if self._pending_question and not self._pending_question.done():
            result = AskUserQuestionResult(answers=[], cancelled=True)
            self._pending_question.set_result(result)

        await self._switch_to_input_app()
        await self._interrupt_agent_loop()

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
            VibeConfig.save_updates(message.changes)
            await self._reload_config()
        else:
            await self._mount_and_scroll(
                UserCommandMessage("Configuration closed (no changes saved).")
            )

        await self._switch_to_input_app()

    async def on_compact_message_completed(
        self, message: CompactMessage.Completed
    ) -> None:
        messages_area = self.query_one("#messages")
        children = list(messages_area.children)

        try:
            compact_index = children.index(message.compact_widget)
        except ValueError:
            return

        if compact_index == 0:
            return

        with self.batch_update():
            for widget in children[:compact_index]:
                await widget.remove()

    def _set_tool_permission_always(
        self, tool_name: str, save_permanently: bool = False
    ) -> None:
        self.agent_loop.set_tool_permission(
            tool_name, ToolPermission.ALWAYS, save_permanently
        )

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

    def _get_skill_entries(self) -> list[tuple[str, str]]:
        if not self.agent_loop:
            return []
        return [
            (f"/{name}", info.description)
            for name, info in self.agent_loop.skill_manager.available_skills.items()
            if info.user_invocable
        ]

    async def _handle_skill(self, user_input: str) -> bool:
        if not user_input.startswith("/"):
            return False

        if not self.agent_loop:
            return False

        skill_name = user_input[1:].strip().lower()
        skill_info = self.agent_loop.skill_manager.get_skill(skill_name)
        if not skill_info:
            return False

        try:
            skill_content = skill_info.skill_path.read_text(encoding="utf-8")
        except OSError as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to read skill file: {e}", collapsed=self._tools_collapsed
                )
            )
            return True

        await self._handle_user_message(skill_content)
        return True

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
                command, shell=True, capture_output=True, text=False, timeout=30
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
                BashOutputMessage(command, str(Path.cwd()), output, exit_code)
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
        user_message = UserMessage(message)

        await self._mount_and_scroll(user_message)

        if not self._agent_running:
            self._agent_task = asyncio.create_task(
                self._handle_agent_loop_turn(message)
            )

    async def _rebuild_history_from_messages(self) -> None:
        if all(msg.role == Role.system for msg in self.agent_loop.messages):
            return

        messages_area = self.query_one("#messages")
        # Don't rebuild if messages are already displayed
        if messages_area.children:
            return

        tool_call_map: dict[str, str] = {}

        with self.batch_update():
            for msg in self.agent_loop.messages:
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

    def _is_tool_enabled_in_main_agent(self, tool: str) -> bool:
        return tool in self.agent_loop.tool_manager.available_tools

    async def _approval_callback(
        self, tool: str, args: BaseModel, tool_call_id: str
    ) -> tuple[ApprovalResponse, str | None]:
        # Auto-approve only if parent is in auto-approve mode AND tool is enabled
        # This ensures subagents respect the main agent's tool restrictions
        if self.agent_loop and self.agent_loop.config.auto_approve:
            if self._is_tool_enabled_in_main_agent(tool):
                return (ApprovalResponse.YES, None)

        self._pending_approval = asyncio.Future()
        with paused_timer(self._loading_widget):
            await self._switch_to_approval_app(tool, args)
            result = await self._pending_approval

        self._pending_approval = None
        return result

    async def _user_input_callback(self, args: BaseModel) -> BaseModel:
        question_args = cast(AskUserQuestionArgs, args)

        self._pending_question = asyncio.Future()
        with paused_timer(self._loading_widget):
            await self._switch_to_question_app(question_args)
            result = await self._pending_question

        self._pending_question = None
        return result

    async def _handle_agent_loop_turn(self, prompt: str) -> None:
        self._agent_running = True

        loading_area = self.query_one("#loading-area-content")

        loading = LoadingWidget()
        self._loading_widget = loading
        await loading_area.mount(loading)
        self._show_todo_area()

        try:
            rendered_prompt = render_path_prompt(prompt, base_dir=Path.cwd())
            async for event in self.agent_loop.act(rendered_prompt):
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

            message = str(e)
            if isinstance(e, RateLimitError):
                if self.plan_type == PlanType.FREE:
                    message = "Rate limits exceeded. Please wait a moment before trying again, or upgrade to Pro for higher rate limits and uninterrupted access."
                else:
                    message = "Rate limits exceeded. Please wait a moment before trying again."

            await self._mount_and_scroll(
                ErrorMessage(message, collapsed=self._tools_collapsed)
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

    async def _interrupt_agent_loop(self) -> None:
        if not self._agent_running or self._interrupt_requested:
            return

        self._interrupt_requested = True

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
        stats = self.agent_loop.stats
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
            base_config = VibeConfig.load()

            await self.agent_loop.reload_with_initial_messages(base_config=base_config)

            await self._mount_and_scroll(UserCommandMessage("Configuration reloaded."))
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to reload config: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _clear_history(self) -> None:
        try:
            await self.agent_loop.clear_history()
            await self._finalize_current_streaming_message()
            messages_area = self.query_one("#messages")
            await messages_area.remove_children()
            todo_area = self.query_one("#todo-area")
            await todo_area.remove_children()

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
        if not self.agent_loop.session_logger.enabled:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Session logging is disabled in configuration.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        try:
            log_path = str(self.agent_loop.session_logger.session_dir)
            await self._mount_and_scroll(
                UserCommandMessage(
                    f"## Current Log Directory\n\n`{log_path}`\n\nYou can send this directory to share your interaction."
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
                    "Cannot compact while agent loop is processing. Please wait.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if len(self.agent_loop.messages) <= 1:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to compact yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.event_handler:
            return

        old_tokens = self.agent_loop.stats.context_tokens
        compact_msg = CompactMessage()
        self.event_handler.current_compact = compact_msg
        await self._mount_and_scroll(compact_msg)

        self._agent_task = asyncio.create_task(
            self._run_compact(compact_msg, old_tokens)
        )

    async def _run_compact(self, compact_msg: CompactMessage, old_tokens: int) -> None:
        self._agent_running = True
        try:
            await self.agent_loop.compact()
            new_tokens = self.agent_loop.stats.context_tokens
            compact_msg.set_complete(old_tokens=old_tokens, new_tokens=new_tokens)

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
        if not self.agent_loop.session_logger.enabled:
            return None
        if not self.agent_loop.session_logger.session_id:
            return None
        session_config = self.agent_loop.session_logger.session_config
        session_path = SessionLoader.does_session_exist(
            self.agent_loop.session_logger.session_id, session_config
        )
        if session_path is None:
            return None
        return self.agent_loop.session_logger.session_id[:8]

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

    async def _switch_from_input(self, widget: Widget, scroll: bool = False) -> None:
        bottom_container = self.query_one("#bottom-app-container")

        if self._chat_input_container:
            self._chat_input_container.display = False
            self._chat_input_container.disabled = True

        if self._agent_indicator:
            self._agent_indicator.display = False

        self._current_bottom_app = BottomApp[type(widget).__name__.removesuffix("App")]
        await bottom_container.mount(widget)

        self.call_after_refresh(widget.focus)
        if scroll:
            self.call_after_refresh(self._scroll_to_bottom)

    async def _switch_to_config_app(self) -> None:
        if self._current_bottom_app == BottomApp.Config:
            return

        await self._mount_and_scroll(UserCommandMessage("Configuration opened..."))
        await self._switch_from_input(
            ConfigApp(self.config, has_terminal_theme=self._terminal_theme is not None)
        )

    async def _switch_to_approval_app(
        self, tool_name: str, tool_args: BaseModel
    ) -> None:
        approval_app = ApprovalApp(
            tool_name=tool_name, tool_args=tool_args, config=self.config
        )
        await self._switch_from_input(approval_app, scroll=True)

    async def _switch_to_question_app(self, args: AskUserQuestionArgs) -> None:
        await self._switch_from_input(QuestionApp(args=args), scroll=True)

    async def _switch_to_input_app(self) -> None:
        for app in BottomApp:
            if app != BottomApp.Input:
                try:
                    await self.query_one(f"#{app.value}-app").remove()
                except Exception:
                    pass

        if self._agent_indicator:
            self._agent_indicator.display = True

        if self._chat_input_container:
            self._chat_input_container.disabled = False
            self._chat_input_container.display = True
            self._current_bottom_app = BottomApp.Input
            self.call_after_refresh(self._chat_input_container.focus_input)

    def _focus_current_bottom_app(self) -> None:
        try:
            match self._current_bottom_app:
                case BottomApp.Input:
                    self.query_one(ChatInputContainer).focus_input()
                case BottomApp.Config:
                    self.query_one(ConfigApp).focus()
                case BottomApp.Approval:
                    self.query_one(ApprovalApp).focus()
                case BottomApp.Question:
                    self.query_one(QuestionApp).focus()
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

        if self._current_bottom_app == BottomApp.Question:
            try:
                question_app = self.query_one(QuestionApp)
                question_app.action_cancel()
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

        if self._agent_running:
            self.run_worker(self._interrupt_agent_loop(), exclusive=False)

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

    async def action_toggle_memory(self) -> None:
        mm = self.agent_loop.memory_manager

        if not mm.enabled:
            if not mm.config.enabled or mm._store is None:
                await self._mount_and_scroll(
                    UserCommandMessage(
                        "Memory is disabled in config and cannot be enabled from UI."
                    )
                )
                return
            mm.enabled = True
            await self._mount_and_scroll(UserCommandMessage("Memory: **ON**"))
            return

        mm.enabled = False
        state = "OFF"
        msg = f"Memory: **{state}**"
        if not mm.enabled:
            m = mm.metrics
            msg += (
                f"\n\nSession stats: {m.observations_stored} stored, "
                f"{m.reflections_triggered} reflections, "
                f"{m.injections_served} injections"
            )
        await self._mount_and_scroll(UserCommandMessage(msg))

    async def action_show_memory(self) -> None:
        await self._show_memory()

    async def _show_memory(self) -> None:
        mm = self.agent_loop.memory_manager
        if not mm.enabled or not mm._store:
            await self._mount_and_scroll(
                UserCommandMessage("Memory is **disabled**.")
            )
            return

        m = mm.metrics
        user_id = mm._user_id
        ctx_key = mm.get_context_key()
        state = mm._store.get_or_create_user_state(user_id)
        ctx = mm._store.get_or_create_context_memory(ctx_key, user_id)

        seed_summary = state.seed.format_summary() or "(empty)"
        if state.fields:
            fields_text = "\n".join(
                f"- **{f.key}**: {f.value}" for f in state.fields
            )
        else:
            fields_text = "(none)"

        block = mm.get_memory_block()
        block_text = f"```xml\n{block}\n```" if block else "(empty)"

        text = f"""## Memory State

**Status**: Enabled
**User**: {user_id}
**Context**: {ctx_key}

### Session Metrics
- Observations stored: {m.observations_stored}
- Skipped (trivial): {m.observations_skipped_trivial}
- Skipped (low entropy): {m.observations_skipped_low_entropy}
- Skipped (dedup): {m.observations_skipped_dedup}
- Skipped (low score): {m.observations_skipped_low_score}
- Reflections triggered: {m.reflections_triggered}
- Injections served: {m.injections_served}

### User Profile (Seed)
{seed_summary}

### User Fields
{fields_text}

### Context Memory
- Sensory buffer: {len(ctx.sensory)} items
- Short-term: {len(ctx.short_term)} items
- Long-term: {len(ctx.long_term)} chars
- Consolidations: {ctx.consolidation_count}

### Current Injection Block
{block_text}"""

        await self._mount_and_scroll(UserCommandMessage(text))

    def action_cycle_mode(self) -> None:
        if self._current_bottom_app != BottomApp.Input:
            return
        self._refresh_profile_widgets()
        self._focus_current_bottom_app()
        self.run_worker(self._cycle_agent(), group="mode_switch", exclusive=True)

    def _refresh_profile_widgets(self) -> None:
        self._update_profile_widgets(self.agent_loop.agent_profile)

    def _update_profile_widgets(self, profile: AgentProfile) -> None:
        if self._agent_indicator:
            self._agent_indicator.set_profile(profile)
        if self._chat_input_container:
            self._chat_input_container.set_safety(profile.safety)

    async def _cycle_agent(self) -> None:
        new_profile = self.agent_loop.agent_manager.next_agent(
            self.agent_loop.agent_profile
        )
        self._update_profile_widgets(new_profile)
        await self.agent_loop.switch_agent(new_profile.name)
        self.agent_loop.set_approval_callback(self._approval_callback)
        self.agent_loop.set_user_input_callback(self._user_input_callback)

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

        self.run_worker(
            self._shutdown_and_exit(),
            group="app_shutdown",
            exclusive=True,
        )

    async def _shutdown_and_exit(self) -> None:
        await self._flush_memory_on_shutdown()
        self.exit(result=self._get_session_resume_info())

    async def _flush_memory_on_shutdown(self) -> None:
        if not hasattr(self, "agent_loop"):
            return
        mm = self.agent_loop.memory_manager
        try:
            if mm.enabled:
                await mm.on_session_end()
            await mm.aclose()
        except Exception:
            logger.warning("Memory shutdown flush failed", exc_info=True)

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

    async def _check_and_show_whats_new(self) -> None:
        if self._update_cache_repository is None:
            return

        if not await should_show_whats_new(
            self._current_version, self._update_cache_repository
        ):
            return

        content = load_whats_new_content()
        if content is not None:
            await self._mount_and_scroll(WhatsNewMessage(content))
        await mark_version_as_seen(self._current_version, self._update_cache_repository)

    async def _maybe_show_plan_offer(self) -> None:
        if self._plan_offer_shown:
            return
        action, plan_type = await self._resolve_plan_offer_action()
        self.plan_type = plan_type
        if action is PlanOfferAction.NONE:
            return
        url = ACTION_TO_URL[action]
        match action:
            case PlanOfferAction.UPGRADE:
                text = f"Upgrade to [Pro]({url})"
            case PlanOfferAction.SWITCH_TO_PRO_KEY:
                text = f"Switch to your [Pro API key]({url})"
        await self._mount_and_scroll(PlanOfferMessage(text))
        self._plan_offer_shown = True

    async def _resolve_plan_offer_action(self) -> tuple[PlanOfferAction, PlanType]:
        try:
            active_model = self.config.get_active_model()
            provider = self.config.get_provider_for_model(active_model)
        except ValueError:
            return PlanOfferAction.NONE, PlanType.UNKNOWN

        api_key_env = provider.api_key_env_var
        api_key = getenv(api_key_env) if api_key_env else None
        gateway = self._plan_offer_gateway or HttpWhoAmIGateway()
        try:
            return await decide_plan_offer(api_key, gateway)
        except Exception as exc:
            logger.warning(
                "Plan-offer check failed (%s).", type(exc).__name__, exc_info=True
            )
            return PlanOfferAction.NONE, PlanType.UNKNOWN

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
        if self._update_notifier is None or not self.config.enable_update_checks:
            return

        asyncio.create_task(self._check_update(), name="version-update-check")

    async def _check_update(self) -> None:
        try:
            if self._update_notifier is None or self._update_cache_repository is None:
                return

            update_availability = await get_update_if_available(
                update_notifier=self._update_notifier,
                current_version=self._current_version,
                update_cache_repository=self._update_cache_repository,
            )
        except UpdateError as error:
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

        if update_availability is None or not update_availability.should_notify:
            return

        update_message_prefix = (
            f"{self._current_version} => {update_availability.latest_version}"
        )

        if self.config.enable_auto_update and await do_update():
            self.notify(
                f"{update_message_prefix}\nVibe was updated successfully. Please restart to use the new version.",
                title="Update successful",
                severity="information",
                timeout=10,
            )
            return

        message = f"{update_message_prefix}\nPlease update mistral-vibe with your package manager"

        self.notify(
            message, title="Update available", severity="information", timeout=10
        )

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


def run_textual_ui(agent_loop: AgentLoop, initial_prompt: str | None = None) -> None:
    update_notifier = PyPIUpdateGateway(project_name="mistral-vibe")
    update_cache_repository = FileSystemUpdateCacheRepository()
    app = VibeApp(
        agent_loop=agent_loop,
        initial_prompt=initial_prompt,
        update_notifier=update_notifier,
        update_cache_repository=update_cache_repository,
    )
    session_id = app.run()
    _print_session_resume_message(session_id)
