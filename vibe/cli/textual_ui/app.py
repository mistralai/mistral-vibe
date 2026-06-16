from __future__ import annotations

import asyncio
import codecs
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from dataclasses import dataclass
from enum import StrEnum, auto
import gc
import os
from pathlib import Path
import signal
import time
from typing import Any, ClassVar, assert_never, cast
from uuid import uuid4
from weakref import WeakKeyDictionary

from pydantic import BaseModel
from rich import print as rprint
from textual.app import WINDOWS, App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalGroup, VerticalScroll
from textual.driver import Driver
from textual.events import AppBlur, AppFocus, MouseUp
from textual.theme import BUILTIN_THEMES
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from vibe import __version__ as CORE_VERSION
from vibe.cli.clipboard import copy_selection_to_clipboard, copy_text_to_clipboard
from vibe.cli.commands import CommandAvailabilityContext, CommandRegistry
from vibe.cli.narrator_manager import (
    NarratorManager,
    NarratorManagerPort,
    NarratorState,
)
from vibe.cli.plan_offer.adapters.http_whoami_gateway import HttpWhoAmIGateway
from vibe.cli.plan_offer.decide_plan_offer import (
    PlanInfo,
    decide_plan_offer,
    plan_offer_cta,
    plan_title,
    resolve_api_key_for_plan,
)
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIGateway, WhoAmIPlanType
from vibe.cli.terminal_detect import Terminal, detect_terminal
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.message_queue import MessageQueue, QueueController, QueuePorts
from vibe.cli.textual_ui.notifications import (
    NotificationContext,
    NotificationPort,
    TextualNotificationAdapter,
)
from vibe.cli.textual_ui.quit_manager import QuitManager
from vibe.cli.textual_ui.remote import RemoteSessionManager, is_progress_event
from vibe.cli.textual_ui.scheduled_loop_runner import ScheduledLoopRunner
from vibe.cli.textual_ui.session_exit import print_session_resume_message
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.banner.banner import Banner
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.input_kinds import (
    Bash,
    EmptyBash,
    Prompt,
    Skill,
    SlashCommand,
    Teleport,
    classify,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.config_app import ConfigApp
from vibe.cli.textual_ui.widgets.connector_auth_app import ConnectorAuthApp
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.debug_console import DebugConsole
from vibe.cli.textual_ui.widgets.feedback_bar import FeedbackBar
from vibe.cli.textual_ui.widgets.feedback_bar_manager import FeedbackBarManager
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreRequested
from vibe.cli.textual_ui.widgets.loading import (
    DEFAULT_LOADING_STATUS,
    LoadingWidget,
    paused_timer,
)
from vibe.cli.textual_ui.widgets.mcp_app import MCPApp, MCPSourceKind
from vibe.cli.textual_ui.widgets.messages import (
    VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX,
    AssistantMessage,
    BashOutputMessage,
    ErrorMessage,
    InterruptMessage,
    SlashCommandMessage,
    StreamingMessageBase,
    TeleportUserMessage,
    UserCommandMessage,
    UserMessage,
    VscodeExtensionPromoMessage,
    WarningMessage,
    WhatsNewMessage,
)
from vibe.cli.textual_ui.widgets.model_picker import ModelPickerApp
from vibe.cli.textual_ui.widgets.narrator_status import NarratorStatus
from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.proxy_setup_app import ProxySetupApp
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.cli.textual_ui.widgets.rewind_app import RewindApp
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp
from vibe.cli.textual_ui.widgets.teleport_message import TeleportMessage
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp, sorted_theme_names
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.cli.textual_ui.widgets.tool_widgets import (
    EditApprovalWidget,
    EditResultWidget,
)
from vibe.cli.textual_ui.widgets.voice_app import VoiceApp
from vibe.cli.textual_ui.windowing import (
    HISTORY_RESUME_TAIL_MESSAGES,
    LOAD_MORE_BATCH_SIZE,
    HistoryLoadMoreManager,
    SessionWindowing,
    build_history_widgets,
    create_resume_plan,
    non_system_history_messages,
    should_resume_history,
    sync_backfill_state,
)
from vibe.cli.update_notifier import (
    PyPIUpdateGateway,
    UpdateCacheRepository,
    UpdateError,
    UpdateGateway,
    get_update_if_available,
    load_whats_new_content,
    mark_version_as_seen,
    should_show_whats_new,
)
from vibe.cli.voice_manager import VoiceManager, VoiceManagerPort
from vibe.cli.voice_manager.voice_manager_port import TranscribeState
from vibe.cli.vscode_extension_promo import (
    FileSystemVscodeExtensionPromoRepository,
    VscodeExtensionPromo,
    VscodeExtensionPromoState,
    should_show_promo,
)
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.agents import AgentProfile
from vibe.core.audio_player.audio_player import AudioPlayer
from vibe.core.audio_recorder import AudioRecorder
from vibe.core.autocompletion.path_prompt import (
    PathPromptPayload,
    PathResource,
    build_path_prompt_payload,
    build_title_segments,
)
from vibe.core.autocompletion.path_prompt_adapter import (
    extract_image_resources,
    render_path_prompt_from_payload,
)
from vibe.core.config import DEFAULT_THEME, ModelConfig, VibeConfig
from vibe.core.data_retention import DATA_RETENTION_MESSAGE
from vibe.core.hooks.models import HookStartEvent
from vibe.core.log_reader import LogReader
from vibe.core.logger import logger
from vibe.core.paths import HISTORY_FILE
from vibe.core.rewind import RewindError
from vibe.core.session.image_snapshot import ImageSnapshotError, snapshot_image
from vibe.core.session.resume_sessions import (
    RemoteResumeResult,
    RemoteResumeSessions,
    ResumeSessionInfo,
    can_delete_resume_session_source,
    list_local_resume_sessions,
    session_latest_messages,
    short_session_id,
)
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_title_at_path,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.session.title_format import format_session_title
from vibe.core.skills.manager import SkillManager
from vibe.core.teleport.telemetry import send_teleport_early_failure_telemetry
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    AskUserQuestionResult,
    Choice,
    Question,
)
from vibe.core.tools.connectors import compute_connector_counts
from vibe.core.tools.mcp_settings import persist_mcp_toggle
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.transcribe import make_transcribe_client
from vibe.core.types import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_MESSAGE,
    AgentStats,
    ApprovalResponse,
    BaseEvent,
    ContextTooLongError,
    ImageAttachment,
    LLMMessage,
    RateLimitError,
    RefusalError,
    Role,
    WaitingForInputEvent,
)
from vibe.core.utils import (
    CancellationReason,
    get_user_cancellation_message,
    is_dangerous_directory,
)

_VSCODE_FAMILY_TERMINALS = {Terminal.VSCODE, Terminal.VSCODE_INSIDERS, Terminal.CURSOR}


def _is_vscode_family_terminal() -> bool:
    return detect_terminal() in _VSCODE_FAMILY_TERMINALS


class BottomApp(StrEnum):
    """Bottom panel app types.

    Convention: Each value must match the widget class name with "App" suffix removed.
    E.g., ApprovalApp -> Approval, ConfigApp -> Config, QuestionApp -> Question.
    This allows dynamic lookup via: BottomApp[type(widget).__name__.removesuffix("App")]
    """

    Approval = auto()
    Config = auto()
    ConnectorAuth = auto()
    Input = auto()
    MCP = auto()
    ModelPicker = auto()
    ProxySetup = auto()
    Question = auto()
    ThemePicker = auto()
    ThinkingPicker = auto()
    Rewind = auto()
    SessionPicker = auto()
    Voice = auto()


class ChatScroll(VerticalScroll):
    """Optimized scroll container that skips cascading style recalculations."""

    @property
    def is_at_bottom(self) -> bool:
        return self.scroll_target_y >= self.max_scroll_y

    _reanchor_pending: bool = False
    _scrolling_down: bool = False

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self._scrolling_down = new_value >= old_value

    def release_anchor(self) -> None:
        super().release_anchor()
        # Textual's MRO dispatch calls Widget._on_mouse_scroll_down AFTER
        # our override, so any re-anchor we do gets immediately undone.
        # Defer the re-check until all handlers for this event have finished.
        if not self._reanchor_pending:
            self._reanchor_pending = True
            self.call_later(self._maybe_reanchor)

    def _maybe_reanchor(self) -> None:
        self._reanchor_pending = False
        if (
            self._anchored
            and self._anchor_released
            and self.is_at_bottom
            and self._scrolling_down
        ):
            self.anchor()

    def update_node_styles(self, animate: bool = True) -> None:
        pass


PRUNE_LOW_MARK = 1000
PRUNE_HIGH_MARK = 1500
DOUBLE_ESC_DELAY = 0.2

_DEFAULT_TYPING_DEBOUNCE_MS = 1000
_TYPING_DEBOUNCE_ENV_VAR = "VIBE_TYPING_GRACE_PERIOD_MS"


def _resolve_typing_debounce_s() -> float:
    try:
        ms = int(os.environ[_TYPING_DEBOUNCE_ENV_VAR])
        if ms < 0:
            raise ValueError
    except (KeyError, ValueError):
        ms = _DEFAULT_TYPING_DEBOUNCE_MS
    return ms / 1000


async def prune_oldest_children(
    messages_area: Widget, low_mark: int, high_mark: int
) -> bool:
    """Remove the oldest children so the virtual height stays within bounds.

    Walks children back-to-front to find how much to keep (up to *low_mark*
    of visible height), then removes everything before that point.
    """
    total_height = messages_area.virtual_size.height
    if total_height <= high_mark:
        return False

    children = messages_area.children
    if not children:
        return False

    accumulated = 0
    cut = len(children)

    for child in reversed(children):
        if not child.display:
            cut -= 1
            continue
        accumulated += child.outer_size.height
        cut -= 1
        if accumulated >= low_mark:
            break

    to_remove = list(children[:cut])
    if not to_remove:
        return False

    await messages_area.remove_children(to_remove)
    return True


@dataclass(frozen=True, slots=True)
class StartupOptions:
    initial_prompt: str | None = None
    teleport_on_start: bool = False
    show_resume_picker: bool = False
    is_resuming_session: bool = False


_REJECT_HINT_BUSY = "wait for the current job to finish."
_REJECT_HINT_PAUSED = "clear the queue first or remove this input."


@dataclass(frozen=True, slots=True)
class _ImageAttachmentRejection:
    message: str
    no_vision: bool = False


class VibeApp(App):  # noqa: PLR0904
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = "app.tcss"
    PAUSE_GC_ON_SCROLL: ClassVar[bool] = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "interrupt_or_quit", "Quit", show=False),
        Binding("ctrl+d", "delete_right_or_quit", "Quit", show=False, priority=True),
        Binding("ctrl+z", "suspend_with_message", "Suspend", show=False, priority=True),
        Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
        Binding("ctrl+o", "toggle_tool", "Toggle Tool", show=False),
        Binding("ctrl+y", "copy_selection", "Copy", show=False, priority=True),
        Binding("ctrl+shift+c", "copy_selection", "Copy", show=False, priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("shift+up", "scroll_chat_up", "Scroll Up", show=False, priority=True),
        Binding(
            "shift+down", "scroll_chat_down", "Scroll Down", show=False, priority=True
        ),
        Binding(
            "ctrl+g", "open_plan_in_editor", "Edit Plan", show=False, priority=False
        ),
        Binding("ctrl+backslash", "toggle_debug_console", "Debug Console", show=False),
        Binding("alt+up", "rewind_prev", "Rewind Previous", show=False, priority=True),
        Binding("ctrl+p", "rewind_prev", "Rewind Previous", show=False, priority=True),
        Binding("alt+down", "rewind_next", "Rewind Next", show=False, priority=True),
        Binding("ctrl+n", "rewind_next", "Rewind Next", show=False, priority=True),
    ]

    def get_driver_class(self) -> type[Driver]:
        """Patch the platform driver to strip malformed mouse reports from input."""
        from vibe.cli.textual_ui.terminal_input_filter import patch_driver_parser

        driver_class = super().get_driver_class()
        patch_driver_parser(driver_class)
        return driver_class

    def __init__(
        self,
        agent_loop: AgentLoop,
        startup: StartupOptions | None = None,
        update_notifier: UpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        current_version: str = CORE_VERSION,
        plan_offer_gateway: WhoAmIGateway | None = None,
        terminal_notifier: NotificationPort | None = None,
        voice_manager: VoiceManagerPort | None = None,
        narrator_manager: NarratorManagerPort | None = None,
        vscode_extension_promo: VscodeExtensionPromo | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.agent_loop = agent_loop
        self._plan_info: PlanInfo | None = None
        self._voice_manager: VoiceManagerPort = (
            voice_manager or self._make_default_voice_manager()
        )
        self._terminal_notifier = terminal_notifier or TextualNotificationAdapter(
            self,
            get_enabled=lambda: self.config.enable_notifications,
            default_title="Vibe",
        )
        self._agent_running = False
        self._interrupt_requested = False
        self._agent_task: asyncio.Task | None = None
        self._bash_task: asyncio.Task | None = None
        self._queue = QueueController(self._build_queue_ports())
        self._remote_manager = RemoteSessionManager()
        self._remote_resume = RemoteResumeSessions(lambda: self.config)
        self._resume_merge_task: asyncio.Task[None] | None = None

        self._loading_widget: LoadingWidget | None = None
        self._pending_approval: asyncio.Future | None = None
        self._pending_question: asyncio.Future | None = None
        self._user_interaction_lock = asyncio.Lock()

        self.event_handler: EventHandler | None = None

        self._chat_input_container: ChatInputContainer | None = None
        self._current_bottom_app: BottomApp = BottomApp.Input

        self.history_file = HISTORY_FILE.path

        self._tools_collapsed = True
        self._windowing = SessionWindowing(load_more_batch_size=LOAD_MORE_BATCH_SIZE)
        self._load_more = HistoryLoadMoreManager()
        self._tool_call_map: dict[str, str] | None = None
        self._history_widget_indices: WeakKeyDictionary[Widget, int] = (
            WeakKeyDictionary()
        )
        self._update_notifier = update_notifier
        self._update_cache_repository = update_cache_repository
        self._current_version = current_version
        self._plan_offer_gateway = plan_offer_gateway
        self._vscode_extension_promo = vscode_extension_promo
        self._show_vscode_extension_promo = (
            vscode_extension_promo is not None
            and _is_vscode_family_terminal()
            and should_show_promo(vscode_extension_promo.initial_state)
        )
        self._configure_startup_options(startup)
        self._last_escape_time: float | None = None
        self._quit_manager = QuitManager(self)
        self._banner: Banner | None = None
        self._whats_new_message: WhatsNewMessage | None = None
        self._cached_messages_area: Widget | None = None
        self._cached_chat: ChatScroll | None = None
        self._cached_loading_area: Widget | None = None
        self._log_reader = LogReader()
        self._debug_console: DebugConsole | None = None
        self._switch_agent_generation = 0
        self._narrator_manager: NarratorManagerPort = (
            narrator_manager or self._make_default_narrator_manager()
        )

        self._rewind_mode = False
        self._rewind_highlighted_widget: UserMessage | None = None
        self._fatal_init_error = False
        self._force_quit_task: asyncio.Task[None] | None = None
        self.commands = self._build_command_registry()
        self._loop_runner = ScheduledLoopRunner(
            self.agent_loop.session_logger,
            can_fire=lambda: (
                not self._agent_running and self._current_bottom_app == BottomApp.Input
            ),
            fire=self._handle_user_message,
            mount=self._mount_and_scroll,
            tools_collapsed=lambda: self._tools_collapsed,
        )

    def _configure_startup_options(self, startup: StartupOptions | None) -> None:
        opts = startup or StartupOptions()
        self._initial_prompt = opts.initial_prompt
        self._teleport_on_start = (
            opts.teleport_on_start and self.agent_loop.base_config.vibe_code_enabled
        )
        self._show_resume_picker = opts.show_resume_picker
        self._is_resuming_session = opts.is_resuming_session

    @property
    def config(self) -> VibeConfig:
        return self.agent_loop.config

    @property
    def _input_queue(self) -> MessageQueue:
        return self._queue.queue

    def _build_queue_ports(self) -> QueuePorts:
        return QueuePorts(
            mount_and_scroll=self._mount_and_scroll,
            agent_running=lambda: self._agent_running,
            bash_task=lambda: self._bash_task,
            active_model=self._active_model_or_none,
            remote_is_active=lambda: self._remote_manager.is_active,
            remote_stop_stream=lambda: self._remote_manager.stop_stream(),
            remove_loading_widget=self._remove_loading_widget,
            set_loading_queue_count=self._set_loading_queue_count,
            inject_user_context=self.agent_loop.inject_user_context,
            next_message_index=lambda: len(self.agent_loop.messages),
            start_agent_turn=self._start_queued_agent_turn,
            await_agent_turn=self._await_agent_turn,
            run_bash=self._start_queued_bash,
            handle_user_message=self._handle_user_message,
            maybe_show_feedback_bar=self._maybe_show_feedback_bar,
            send_skill_telemetry=self._send_skill_telemetry,
            send_at_mention_telemetry=self._send_at_mention_telemetry,
            render_payload=lambda payload: render_path_prompt_from_payload(
                payload, skip_images=True
            ),
        )

    def _active_model_or_none(self) -> ModelConfig | None:
        try:
            return self.agent_loop.config.get_active_model()
        except ValueError:
            return None

    def _set_loading_queue_count(self, count: int) -> None:
        if self._loading_widget is not None:
            self._loading_widget.set_queue_count(count)

    def _maybe_show_feedback_bar(self) -> None:
        if self._feedback_bar_manager.should_show(self.agent_loop):
            self._feedback_bar.show()
            self._feedback_bar_manager.record_feedback_asked()

    def _start_queued_agent_turn(
        self,
        content: str,
        *,
        prebuilt_images: list[ImageAttachment] | None = None,
        prebuilt_payload: PathPromptPayload | None = None,
    ) -> asyncio.Task:
        self._agent_task = asyncio.create_task(
            self._handle_agent_loop_turn(
                content,
                prebuilt_images=prebuilt_images,
                prebuilt_payload=prebuilt_payload,
            )
        )
        return self._agent_task

    async def _await_agent_turn(self) -> None:
        agent_task = self._agent_task
        if agent_task is None:
            return
        await agent_task

    def _start_queued_bash(
        self, command: str, *, existing_widget: BashOutputMessage | None = None
    ) -> asyncio.Task:
        self._bash_task = asyncio.create_task(
            self._handle_bash_command(
                command, existing_widget=existing_widget, start_drain_on_finish=False
            )
        )
        return self._bash_task

    @property
    def _connectors_enabled(self) -> bool:
        return self.agent_loop.connector_registry is not None

    def _get_command_availability_context(self) -> CommandAvailabilityContext:
        return CommandAvailabilityContext(
            vibe_code_enabled=self.agent_loop.base_config.vibe_code_enabled,
            is_active_model_mistral=self.config.is_active_model_mistral(),
            plan_info=self._plan_info,
        )

    def _build_command_registry(self) -> CommandRegistry:
        return CommandRegistry(
            availability_context=self._get_command_availability_context()
        )

    def _refresh_command_registry(self) -> None:
        self.commands.refresh(self._get_command_availability_context())

    def compose(self) -> ComposeResult:
        with ChatScroll(id="chat"):
            connectors_connected, connectors_total = compute_connector_counts(
                self.config, self.agent_loop.connector_registry
            )
            self._banner = Banner(
                config=self.config,
                skill_manager=self.agent_loop.skill_manager,
                connectors_connected=connectors_connected,
                connectors_total=connectors_total,
                hooks_count=self.agent_loop.hooks_count,
            )
            yield self._banner
            yield VerticalGroup(id="messages")

        with Horizontal(id="loading-area"):
            yield NarratorStatus(self._narrator_manager)
            yield Static(id="loading-area-content")
            self._clipboard_notice = NonSelectableStatic(id="clipboard-notice")
            self._clipboard_notice.display = False
            self._clipboard_hide_timer: Timer | None = None
            yield self._clipboard_notice
            yield FeedbackBar()

        with Static(id="bottom-app-container"):
            yield ChatInputContainer(
                history_file=self.history_file,
                command_registry=self.commands,
                id="input-container",
                safety=self.agent_loop.agent_profile.safety,
                agent_name=self.agent_loop.agent_profile.display_name.lower(),
                skill_entries_getter=self._get_skill_entries,
                file_watcher_for_autocomplete_getter=self._is_file_watcher_enabled,
                voice_manager=self._voice_manager,
            )

        with Horizontal(id="bottom-bar"):
            yield PathDisplay(self.config.displayed_workdir or Path.cwd())
            yield NoMarkupStatic(id="spacer")
            yield ContextProgress()

    @property
    def _messages_area(self) -> Widget:
        if self._cached_messages_area is None:
            self._cached_messages_area = self.query_one("#messages")
        return self._cached_messages_area

    @property
    def _chat_widget(self) -> ChatScroll:
        if self._cached_chat is None:
            self._cached_chat = self.query_one("#chat", ChatScroll)
        return self._cached_chat

    @property
    def _loading_area(self) -> Widget:
        if self._cached_loading_area is None:
            self._cached_loading_area = self.query_one("#loading-area-content")
        return self._cached_loading_area

    async def on_mount(self) -> None:
        self._apply_theme(self.config.theme)
        self._terminal_notifier.restore()
        self._feedback_bar = self.query_one(FeedbackBar)
        self._feedback_bar_manager = FeedbackBarManager()

        self.event_handler = EventHandler(
            mount_callback=self._mount_and_scroll,
            get_tools_collapsed=lambda: self._tools_collapsed,
            on_profile_changed=self._on_profile_changed,
            is_remote=self._remote_manager.is_active,
        )

        self._chat_input_container = self.query_one(ChatInputContainer)
        context_progress = self.query_one(ContextProgress)

        def update_context_progress(stats: AgentStats) -> None:
            context_progress.tokens = TokenState(
                max_tokens=self.config.get_active_model().auto_compact_threshold,
                current_tokens=stats.context_tokens,
            )

        self.agent_loop.stats.add_listener("context_tokens", update_context_progress)
        self.agent_loop.stats.trigger_listeners()

        self.agent_loop.set_approval_callback(self._approval_callback)
        self.agent_loop.set_user_input_callback(self._user_input_callback)
        self._refresh_profile_widgets()

        chat_input_container = self.query_one(ChatInputContainer)
        chat_input_container.focus_input()
        await self._resolve_plan()
        await self._show_dangerous_directory_warning()
        await self._resume_history_from_messages()
        self._loop_runner.restore_from_session()
        self._loop_runner.start()
        await self._check_and_show_whats_new()
        self._schedule_update_notification()
        if self._is_resuming_session:
            await self.agent_loop.hydrate_experiments_from_session()
        else:
            self.agent_loop.start_initialize_experiments()

        self.call_after_refresh(self._refresh_banner)
        self._show_config_issues()

        self.run_worker(self._watch_init_completion(), exclusive=False)

        if self._show_resume_picker:
            self.run_worker(self._show_session_picker(), exclusive=False)
        elif self._initial_prompt or self._teleport_on_start:
            self.call_after_refresh(self._process_initial_prompt)

        gc.collect()
        gc.freeze()

    def _show_config_issues(self) -> None:
        for issue in (
            *self.agent_loop.hook_config_issues,
            *self.agent_loop.skill_manager.config_issues,
        ):
            self.notify(
                f"{issue.file}\n{issue.message}",
                severity="warning",
                markup=False,
                timeout=10,
            )

    async def _watch_init_completion(self) -> None:
        """Show 'Initializing' loading indicator until background init finishes."""
        init_widget = None
        try:
            if not self.agent_loop.is_initialized:
                await self._ensure_loading_widget("Initializing", show_hint=False)
                init_widget = self._loading_widget
            await self.agent_loop.wait_until_ready()
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Background initialization failed: {e}",
                    collapsed=self._tools_collapsed,
                )
            )
            await self._mount_and_scroll(
                Static("Press any key to exit...", classes="error-hint")
            )
            if self._chat_input_container:
                self._chat_input_container.disabled = True
                self._chat_input_container.display = False
            self._fatal_init_error = True
        finally:
            if self._loading_widget is init_widget:
                await self._remove_loading_widget()
            self._refresh_banner()
            try:
                self.query_one(MCPApp).refresh_index()
            except Exception:
                pass

    def _process_initial_prompt(self) -> None:
        if self._teleport_on_start and self.commands.has_command("teleport"):
            self.run_worker(
                self._handle_teleport_command(self._initial_prompt), exclusive=False
            )
        elif self._initial_prompt:
            self.run_worker(
                self._handle_user_message(self._initial_prompt), exclusive=False
            )

    def _is_file_watcher_enabled(self) -> bool:
        return self.config.file_watcher_for_autocomplete

    def on_key(self) -> None:
        if self._fatal_init_error:
            self.exit()

    async def on_chat_input_container_submitted(
        self, event: ChatInputContainer.Submitted
    ) -> None:
        value = event.value.strip()
        input_widget = self.query_one(ChatInputContainer)

        if not value and not self._input_queue.paused:
            return

        if self._banner:
            self._banner.freeze_animation()

        if self._whats_new_message:
            await self._whats_new_message.remove()
            self._whats_new_message = None

        if self._input_queue.paused:
            if not await self._handle_paused_submit(value):
                self._restore_input_if_empty(input_widget, value)
            return

        if self._is_busy():
            if not await self._handle_queue_submit(
                value, reject_hint=_REJECT_HINT_BUSY
            ):
                self._restore_input_if_empty(input_widget, value)
            return

        await self._dispatch_idle_input(value)

    @staticmethod
    def _restore_input_if_empty(input_widget: ChatInputContainer, value: str) -> None:
        if not input_widget.value:
            input_widget.value = value

    async def _empty_bash_error(self) -> None:
        await self._mount_and_scroll(
            ErrorMessage(
                "No command provided after '!'", collapsed=self._tools_collapsed
            )
        )

    def _warn_not_queueable(self, message: str) -> None:
        self.notify(message, severity="warning", markup=False)

    async def _dispatch_idle_input(self, value: str) -> None:
        match classify(value, commands=self.commands, expand_skill=self._expand_skill):
            case Teleport(target=target):
                await self._handle_teleport_command(target)
            case SlashCommand():
                await self._handle_command(value)
            case Skill(expanded_prompt=expanded):
                await self._handle_user_message(expanded, title_source=value)
            case Bash(command=command):
                self._bash_task = asyncio.create_task(
                    self._handle_bash_command(command)
                )
                self._queue.notify_busy_changed()
            case EmptyBash():
                await self._empty_bash_error()
            case Prompt(text=text):
                await self._handle_user_message(text)

    async def _handle_paused_submit(self, value: str) -> bool:
        if value and not await self._handle_queue_submit(
            value, reject_hint=_REJECT_HINT_PAUSED
        ):
            return False
        self._queue.set_paused(False)
        self._queue.start_drain_if_needed()
        return True

    async def _handle_queue_submit(self, value: str, *, reject_hint: str) -> bool:
        match classify(value, commands=self.commands, expand_skill=self._expand_skill):
            case Teleport():
                self._warn_not_queueable(f"Teleport cannot be queued — {reject_hint}")
                return False
            case SlashCommand():
                self._warn_not_queueable(
                    f"Slash commands cannot be queued — {reject_hint}"
                )
                return False
            case Skill(expanded_prompt=expanded, name=name):
                return await self._enqueue_prompt_with_resources(
                    expanded, skill_name=name
                )
            case Bash(command=command):
                await self._queue.enqueue_bash(command)
            case EmptyBash():
                await self._empty_bash_error()
            case Prompt(text=text):
                return await self._enqueue_prompt_with_resources(text)
        return True

    async def _enqueue_prompt_with_resources(
        self, content: str, *, skill_name: str | None = None
    ) -> bool:
        payload = build_path_prompt_payload(content, base_dir=Path.cwd())
        images = await self._prepare_images_or_abort(payload)
        if images is None:
            return False
        await self._queue.enqueue_prompt(
            content, skill_name=skill_name, images=images, payload=payload
        )
        return True

    def _is_busy(self) -> bool:
        if self._agent_running:
            return True
        if self._bash_task is not None and not self._bash_task.done():
            return True
        if self._queue.draining:
            return True
        return False

    async def on_approval_app_approval_granted(
        self, message: ApprovalApp.ApprovalGranted
    ) -> None:
        if self._pending_approval and not self._pending_approval.done():
            self._pending_approval.set_result((ApprovalResponse.YES, None))

    async def on_approval_app_approval_granted_always_tool(
        self, message: ApprovalApp.ApprovalGrantedAlwaysTool
    ) -> None:
        self.agent_loop.approve_always(message.tool_name, message.required_permissions)

        if self._pending_approval and not self._pending_approval.done():
            self._pending_approval.set_result((ApprovalResponse.YES, None))

    async def on_approval_app_approval_granted_always_permanent(
        self, message: ApprovalApp.ApprovalGrantedAlwaysPermanent
    ) -> None:
        self.agent_loop.approve_always(
            message.tool_name, message.required_permissions, save_permanently=True
        )

        if self._pending_approval and not self._pending_approval.done():
            self._pending_approval.set_result((ApprovalResponse.YES, None))

    async def on_approval_app_approval_rejected(
        self, message: ApprovalApp.ApprovalRejected
    ) -> None:
        if self._pending_approval and not self._pending_approval.done():
            feedback = str(
                get_user_cancellation_message(CancellationReason.OPERATION_CANCELLED)
            )
            self._pending_approval.set_result((ApprovalResponse.NO, feedback))

        if self._loading_widget and self._loading_widget.parent:
            await self._remove_loading_widget()

    async def on_question_app_answered(self, message: QuestionApp.Answered) -> None:
        if self._remote_manager.has_pending_input and self._remote_manager.is_active:
            result = AskUserQuestionResult(answers=message.answers, cancelled=False)
            await self._handle_remote_answer(result)
            return

        if self._pending_question and not self._pending_question.done():
            result = AskUserQuestionResult(answers=message.answers, cancelled=False)
            self._pending_question.set_result(result)

    async def on_question_app_cancelled(self, message: QuestionApp.Cancelled) -> None:
        if self._remote_manager.has_pending_input:
            self._remote_manager.cancel_pending_input()
            await self._switch_to_input_app()
            return

        if self._pending_question and not self._pending_question.done():
            result = AskUserQuestionResult(answers=[], cancelled=True)
            self._pending_question.set_result(result)

    def on_chat_text_area_feedback_key_pressed(
        self, message: ChatTextArea.FeedbackKeyPressed
    ) -> None:
        self._feedback_bar.handle_feedback_key(message.rating)

    def on_chat_text_area_non_feedback_key_pressed(
        self, message: ChatTextArea.NonFeedbackKeyPressed
    ) -> None:
        self._feedback_bar.hide()

    def on_feedback_bar_feedback_given(
        self, message: FeedbackBar.FeedbackGiven
    ) -> None:
        self.agent_loop.telemetry_client.send_user_rating_feedback(
            rating=message.rating, model=self.config.active_model
        )

    async def _remove_loading_widget(self) -> None:
        if self._loading_widget and self._loading_widget.parent:
            await self._loading_widget.remove()
            self._loading_widget = None

    async def _resolve_turn_images(
        self, payload: PathPromptPayload, prebuilt: list[ImageAttachment] | None
    ) -> list[ImageAttachment] | None:
        if prebuilt is not None:
            return prebuilt
        return await self._prepare_images_or_abort(payload)

    async def _prepare_images_or_abort(
        self, payload: PathPromptPayload
    ) -> list[ImageAttachment] | None:
        result = await self._build_image_attachments(payload)
        if isinstance(result, _ImageAttachmentRejection):
            await self._remove_loading_widget()
            if result.no_vision:
                await self._mount_and_scroll(
                    ErrorMessage(result.message, show_border=False)
                )
            else:
                await self._mount_and_scroll(
                    ErrorMessage(result.message, collapsed=self._tools_collapsed)
                )
            return None
        return result

    async def _build_image_attachments(
        self, payload: PathPromptPayload
    ) -> list[ImageAttachment] | _ImageAttachmentRejection:
        image_resources = extract_image_resources(payload)
        if not image_resources:
            return []

        if len(image_resources) > MAX_IMAGES_PER_MESSAGE:
            return _ImageAttachmentRejection(
                f"Too many image attachments (got {len(image_resources)}, "
                f"max {MAX_IMAGES_PER_MESSAGE})."
            )

        try:
            active_model = self.agent_loop.config.get_active_model()
        except ValueError:
            active_model = None
        if active_model is not None and not active_model.supports_images:
            return _ImageAttachmentRejection(
                f"Model `{active_model.alias}` does not support images. "
                f"Switch with /model, remove the attachment, or ask me to enable the support for this model.",
                no_vision=True,
            )

        attachments: list[ImageAttachment] = []
        session_dir = self.agent_loop.session_logger.session_dir
        for resource in image_resources:
            result = self._snapshot_single_image(resource, session_dir)
            if isinstance(result, str):
                return _ImageAttachmentRejection(result)
            attachments.append(result)
        return attachments

    def _snapshot_single_image(
        self, resource: PathResource, session_dir: Path | None
    ) -> ImageAttachment | str:
        try:
            size = resource.path.stat().st_size
        except OSError as e:
            return f"Cannot read image {resource.alias}: {e}"
        if size > MAX_IMAGE_BYTES:
            return (
                f"Image `{resource.alias}` is "
                f"{size / (1024 * 1024):.1f} MB; max is "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB."
            )
        try:
            return snapshot_image(
                resource.path, alias=resource.alias, session_dir=session_dir
            )
        except ImageSnapshotError as e:
            return f"Failed to attach image {resource.alias}: {e}"

    async def on_config_app_open_model_picker(
        self, _message: ConfigApp.OpenModelPicker
    ) -> None:
        config_app = self.query_one(ConfigApp)
        changes = config_app._convert_changes_for_save()
        if changes:
            VibeConfig.save_updates(changes)
            await self._reload_config()
        await self._switch_to_input_app()
        await self._switch_to_model_picker_app()

    async def on_config_app_open_thinking_picker(
        self, _message: ConfigApp.OpenThinkingPicker
    ) -> None:
        config_app = self.query_one(ConfigApp)
        changes = config_app._convert_changes_for_save()
        if changes:
            VibeConfig.save_updates(changes)
            await self._reload_config()
        await self._switch_to_input_app()
        await self._switch_to_thinking_picker_app()

    async def _ensure_loading_widget(
        self, status: str = DEFAULT_LOADING_STATUS, *, show_hint: bool = True
    ) -> None:
        if self._loading_widget and self._loading_widget.parent:
            self._loading_widget.set_status(status)
            return

        try:
            loading_area = self._loading_area
        except Exception:
            return
        loading = LoadingWidget(status=status, show_hint=show_hint)
        self._loading_widget = loading
        await loading_area.mount(loading)

    async def on_config_app_config_closed(
        self, message: ConfigApp.ConfigClosed
    ) -> None:
        await self._handle_config_settings_closed(message.changes)
        await self._switch_to_input_app()

    async def on_voice_app_config_closed(self, message: VoiceApp.ConfigClosed) -> None:
        await self._handle_voice_settings_closed(message.changes)
        await self._switch_to_input_app()

    async def _handle_config_settings_closed(
        self, changes: dict[str, str | bool]
    ) -> None:
        if changes:
            VibeConfig.save_updates(changes)
            await self._reload_config()
        else:
            await self._mount_and_scroll(
                UserCommandMessage("Configuration closed (no changes saved).")
            )

    async def _handle_voice_settings_closed(
        self, changes: dict[str, str | bool]
    ) -> None:
        if not changes:
            await self._mount_and_scroll(
                UserCommandMessage("Voice settings closed (no changes saved).")
            )
            return

        if "voice_mode_enabled" in changes:
            current = self._voice_manager.is_enabled
            desired = changes["voice_mode_enabled"]
            if current != desired:
                self._voice_manager.toggle_voice_mode()
                self.agent_loop.telemetry_client.send_telemetry_event(
                    "vibe.voice_mode_toggled", {"enabled": desired}
                )
                self.agent_loop.refresh_config()
                if desired:
                    await self._mount_and_scroll(
                        UserCommandMessage(
                            "Voice mode enabled. Press ctrl+r to start recording."
                        )
                    )
                else:
                    await self._mount_and_scroll(
                        UserCommandMessage("Voice mode disabled.")
                    )

        non_voice_changes = {
            k: v for k, v in changes.items() if k != "voice_mode_enabled"
        }
        if non_voice_changes:
            VibeConfig.save_updates(non_voice_changes)
            self.agent_loop.refresh_config()
            self._narrator_manager.sync()

    async def on_model_picker_app_model_selected(
        self, message: ModelPickerApp.ModelSelected
    ) -> None:
        VibeConfig.save_updates({"active_model": message.alias})
        await self._reload_config()
        await self._switch_to_input_app()

    async def on_model_picker_app_cancelled(
        self, _event: ModelPickerApp.Cancelled
    ) -> None:
        await self._switch_to_input_app()

    async def on_thinking_picker_app_thinking_selected(
        self, message: ThinkingPickerApp.ThinkingSelected
    ) -> None:
        self.config.set_thinking(message.level)
        await self._reload_config()
        await self._switch_to_input_app()

    async def on_thinking_picker_app_cancelled(
        self, _event: ThinkingPickerApp.Cancelled
    ) -> None:
        await self._switch_to_input_app()

    async def on_theme_picker_app_theme_previewed(
        self, message: ThemePickerApp.ThemePreviewed
    ) -> None:
        self._apply_theme(message.theme)
        await self._restyle_diff_widgets()

    async def on_theme_picker_app_theme_selected(
        self, message: ThemePickerApp.ThemeSelected
    ) -> None:
        self._apply_theme(message.theme)
        self.config.theme = message.theme
        VibeConfig.save_updates({"theme": message.theme})
        await self._restyle_diff_widgets()
        await self._switch_to_input_app()

    async def on_theme_picker_app_cancelled(
        self, message: ThemePickerApp.Cancelled
    ) -> None:
        self._apply_theme(message.original_theme)
        await self._restyle_diff_widgets()
        await self._switch_to_input_app()

    async def _restyle_diff_widgets(self) -> None:
        # Diff content bakes in ANSI-vs-truecolor styling, so it must be rebuilt.
        for widget in self.query(EditResultWidget):
            await widget.recompose()
        for widget in self.query(EditApprovalWidget):
            await widget.recompose()

    async def on_mcpapp_mcpclosed(self, _message: MCPApp.MCPClosed) -> None:
        await self._mount_and_scroll(UserCommandMessage("MCP servers closed."))
        await self._switch_to_input_app()

    async def on_mcpapp_mcptoggled(self, message: MCPApp.MCPToggled) -> None:
        persist_mcp_toggle(
            self.agent_loop.config,
            name=message.name,
            is_connector=message.kind == MCPSourceKind.CONNECTOR,
            disabled=message.disabled,
            tool_name=message.tool_name,
        )
        self.agent_loop.refresh_config()
        self.query_one(MCPApp).refresh_index()
        self._refresh_banner()

    async def on_mcpapp_connector_auth_requested(
        self, message: MCPApp.ConnectorAuthRequested
    ) -> None:
        await self._switch_to_input_app()
        await self._switch_from_input(
            ConnectorAuthApp(
                connector_name=message.connector_name,
                connector_registry=message.connector_registry,
                tool_manager=message.tool_manager,
            )
        )

    async def on_connector_auth_app_connector_auth_closed(
        self, message: ConnectorAuthApp.ConnectorAuthClosed
    ) -> None:
        if message.refreshed:
            await self.agent_loop.refresh_system_prompt()
            self._refresh_banner()
        await self._switch_to_input_app()
        await self._show_mcp(cmd_args=message.connector_name)

    async def on_proxy_setup_app_proxy_setup_closed(
        self, message: ProxySetupApp.ProxySetupClosed
    ) -> None:
        if message.error:
            await self._mount_and_scroll(
                ErrorMessage(f"Failed to save proxy settings: {message.error}")
            )
        elif message.saved:
            await self._mount_and_scroll(
                UserCommandMessage(
                    "Proxy settings saved. Restart the CLI for changes to take effect."
                )
            )
        else:
            await self._mount_and_scroll(UserCommandMessage("Proxy setup cancelled."))

        await self._switch_to_input_app()

    async def on_compact_message_completed(
        self, message: CompactMessage.Completed
    ) -> None:
        children = list(self._messages_area.children)

        try:
            compact_index = children.index(message.compact_widget)
        except ValueError:
            return

        if compact_index == 0:
            return

        with self.batch_update():
            for widget in children[:compact_index]:
                await widget.remove()

    async def _handle_command(self, user_input: str) -> bool:
        if resolved := self.commands.parse_command(user_input):
            cmd_name, command, cmd_args = resolved
            self.agent_loop.telemetry_client.send_slash_command_used(
                cmd_name, "builtin"
            )
            command_text = user_input.strip()
            display = (
                command_text.removeprefix("/")
                if command_text.startswith("/")
                else cmd_name
            )
            await self._mount_and_scroll(SlashCommandMessage(display))
            handler = getattr(self, command.handler)
            if asyncio.iscoroutinefunction(handler):
                await handler(cmd_args=cmd_args)
            else:
                handler(cmd_args=cmd_args)
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

    def _expand_skill(self, user_input: str) -> Skill | None:
        if not self.agent_loop:
            return None
        skill = self.agent_loop.skill_manager.parse_skill_command(user_input)
        if skill is None:
            return None
        return Skill(
            expanded_prompt=SkillManager.build_skill_prompt(user_input, skill),
            name=skill.name,
        )

    def _send_skill_telemetry(self, name: str | None) -> None:
        if name is None:
            return
        self.agent_loop.telemetry_client.send_slash_command_used(name, "skill")

    def _send_at_mention_telemetry(
        self, payload: PathPromptPayload, message_id: str
    ) -> None:
        if not payload.all_resources:
            return
        context_types: dict[str, int] = {}
        for r in payload.all_resources:
            context_types[r.kind] = context_types.get(r.kind, 0) + 1
        file_ext_counts: dict[str, int] = {}
        for r in payload.all_resources:
            if r.kind == "file" and r.path.suffix:
                file_ext_counts[r.path.suffix] = (
                    file_ext_counts.get(r.path.suffix, 0) + 1
                )
        self.agent_loop.telemetry_client.send_at_mention_inserted(
            nb_mentions=len(payload.all_resources),
            context_types=context_types,
            file_extensions=file_ext_counts or None,
            message_id=message_id,
        )

    @staticmethod
    async def _bash_read_stream(
        stream: asyncio.StreamReader | None,
        parts: list[str],
        bash_msg: BashOutputMessage,
    ) -> None:
        if not stream:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            text = decoder.decode(chunk)
            if not text:
                continue
            parts.append(text)
            await bash_msg.append_output(text)
        final_text = decoder.decode(b"", final=True)
        if not final_text:
            return
        parts.append(final_text)
        await bash_msg.append_output(final_text)

    @staticmethod
    async def _kill_running_process(proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()

    async def _handle_bash_command(
        self,
        command: str,
        *,
        existing_widget: BashOutputMessage | None = None,
        start_drain_on_finish: bool = True,
    ) -> None:
        try:
            await self._handle_bash_command_inner(
                command, existing_widget=existing_widget
            )
        finally:
            current = asyncio.current_task()
            if self._bash_task is current:
                self._bash_task = None
            self._queue.notify_busy_changed()
            if start_drain_on_finish:
                self._queue.start_drain_if_needed()

    async def _handle_bash_command_inner(
        self, command: str, *, existing_widget: BashOutputMessage | None = None
    ) -> None:
        if not command:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No command provided after '!'", collapsed=self._tools_collapsed
                )
            )
            return

        if existing_widget is not None:
            bash_msg = existing_widget
        else:
            bash_msg = BashOutputMessage(command, str(Path.cwd()), pending=True)
            await self._mount_and_scroll(bash_msg)
        await self._ensure_loading_widget("Running command")
        bash_loading_widget = self._loading_widget

        proc: asyncio.subprocess.Process | None = None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        self._bash_read_stream(proc.stdout, stdout_parts, bash_msg),
                        self._bash_read_stream(proc.stderr, stderr_parts, bash_msg),
                        proc.wait(),
                    ),
                    timeout=30,
                )
            except TimeoutError:
                await self._kill_running_process(proc)
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                await bash_msg.finish(1)
                await self._mount_and_scroll(
                    ErrorMessage(
                        "Command timed out after 30 seconds",
                        collapsed=self._tools_collapsed,
                    )
                )
                await self.agent_loop.inject_user_context(
                    self._format_manual_command_context(
                        command=command,
                        cwd=str(Path.cwd()),
                        stdout=stdout,
                        stderr=stderr,
                        status="timed out after 30 seconds",
                    )
                )
                return

            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            exit_code = proc.returncode or 0
            await bash_msg.finish(exit_code)
            await self.agent_loop.inject_user_context(
                self._format_manual_command_context(
                    command=command,
                    cwd=str(Path.cwd()),
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
        except asyncio.CancelledError:
            await self._kill_running_process(proc)
            await bash_msg.finish(1, interrupted=True)
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            await self.agent_loop.inject_user_context(
                self._format_manual_command_context(
                    command=command,
                    cwd=str(Path.cwd()),
                    stdout=stdout,
                    stderr=stderr,
                    status="interrupted by user",
                )
            )
        except Exception as e:
            await self._kill_running_process(proc)
            await bash_msg.finish(1)
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            await self._mount_and_scroll(
                ErrorMessage(f"Command failed: {e}", collapsed=self._tools_collapsed)
            )
            await self.agent_loop.inject_user_context(
                self._format_manual_command_context(
                    command=command,
                    cwd=str(Path.cwd()),
                    stdout=stdout,
                    stderr=stderr,
                    status=f"failed before completion: {e}",
                )
            )
        finally:
            if self._loading_widget is bash_loading_widget:
                await self._remove_loading_widget()

    def _get_bash_max_output_bytes(self) -> int:
        from vibe.core.tools.builtins.bash import BashToolConfig

        config = self.agent_loop.tool_manager.get_tool_config("bash")
        if isinstance(config, BashToolConfig):
            return config.max_output_bytes
        return BashToolConfig().max_output_bytes

    @staticmethod
    def _cap_output(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n... [truncated]"

    def _format_manual_command_context(
        self,
        *,
        command: str,
        cwd: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        status: str | None = None,
    ) -> str:
        limit = self._get_bash_max_output_bytes()
        stdout = self._cap_output(stdout, limit)
        stderr = self._cap_output(stderr, limit)

        sections = [
            "Manual `!` command result from the user. Use this as context only.",
            f"Command: `{command}`",
            f"Working directory: `{cwd}`",
        ]

        if status is not None:
            sections.append(f"Status: {status}")

        if exit_code is not None:
            sections.append(f"Exit code: {exit_code}")

        if stdout:
            sections.append(f"Stdout:\n```text\n{stdout.rstrip()}\n```")

        if stderr:
            sections.append(f"Stderr:\n```text\n{stderr.rstrip()}\n```")

        if not stdout and not stderr:
            sections.append("Output:\n```text\n(no output)\n```")

        return "\n\n".join(sections)

    async def _handle_user_message(
        self, message: str, *, title_source: str | None = None
    ) -> None:
        if self._remote_manager.is_active:
            await self._handle_remote_user_message(message)
            return

        prompt_payload = build_path_prompt_payload(message, base_dir=Path.cwd())
        images = await self._prepare_images_or_abort(prompt_payload)
        if images is None:
            input_widget = self.query_one(ChatInputContainer)
            if not input_widget.value:
                input_widget.value = message
            return

        # message_index is where the user message will land in agent_loop.messages
        # (checkpoint is created in agent_loop.act())
        message_index = len(self.agent_loop.messages)
        user_message = UserMessage(
            message, message_index=message_index, images=images or None
        )

        messages_area = self._cached_messages_area or self.query_one("#messages")
        last_child = messages_area.children[-1] if messages_area.children else None
        if isinstance(last_child, UserMessage):
            last_child.set_show_separator(False)
            user_message.set_follows_previous(True)

        await self._mount_and_scroll(user_message)
        if self._feedback_bar_manager.should_show(self.agent_loop):
            self._feedback_bar.show()
            self._feedback_bar_manager.record_feedback_asked()

        if not self._agent_running:
            await self._remote_manager.stop_stream()
            await self._remove_loading_widget()
            self._agent_task = asyncio.create_task(
                self._handle_agent_loop_turn(
                    message,
                    title_source=title_source,
                    prebuilt_images=images,
                    prebuilt_payload=prompt_payload,
                )
            )
            self._queue.notify_busy_changed()

    async def _handle_remote_user_message(self, message: str) -> None:
        warning = self._remote_manager.validate_input()
        if warning:
            await self._mount_and_scroll(WarningMessage(warning))
            return
        try:
            await self._remote_manager.send_prompt(message)
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to send message: {e}", collapsed=self._tools_collapsed
                )
            )
            return
        await self._ensure_loading_widget()

    async def _handle_remote_waiting_input(self, event: WaitingForInputEvent) -> None:
        self._remote_manager.set_pending_input(event)
        if question_args := self._remote_manager.build_question_args(event):
            await self._switch_to_question_app(question_args)
            return
        await self._switch_to_input_app()

    async def _handle_remote_answer(self, result: AskUserQuestionResult) -> None:
        if result.cancelled or not result.answers:
            self._remote_manager.cancel_pending_input()
            await self._switch_to_input_app()
            return
        await self._remote_manager.send_prompt(
            result.answers[0].answer, require_source=False
        )
        await self._switch_to_input_app()
        await self._ensure_loading_widget()

    def _reset_ui_state(self) -> None:
        self._windowing.reset()
        self._tool_call_map = None
        self._history_widget_indices = WeakKeyDictionary()

    async def _resume_history_from_messages(self) -> None:
        messages_area = self._messages_area
        if not should_resume_history(list(messages_area.children)):
            return

        history_messages = non_system_history_messages(self.agent_loop.messages)
        if (
            plan := create_resume_plan(history_messages, HISTORY_RESUME_TAIL_MESSAGES)
        ) is None:
            return
        await self._mount_history_batch(
            plan.tail_messages,
            messages_area,
            plan.tool_call_map,
            start_index=plan.tail_start_index,
        )
        self.call_after_refresh(self._chat_widget.anchor)
        self._tool_call_map = plan.tool_call_map
        self._windowing.set_backfill(plan.backfill_messages)
        await self._load_more.set_visible(
            messages_area,
            visible=self._windowing.has_backfill,
            remaining=self._windowing.remaining,
        )

    async def _mount_history_batch(
        self,
        batch: list[LLMMessage],
        messages_area: Widget,
        tool_call_map: dict[str, str],
        *,
        start_index: int,
        before: Widget | int | None = None,
        after: Widget | None = None,
    ) -> None:
        widgets = build_history_widgets(
            batch=batch,
            tool_call_map=tool_call_map,
            start_index=start_index,
            history_widget_indices=self._history_widget_indices,
        )

        with self.batch_update():
            if not widgets:
                return
            if before is not None:
                await messages_area.mount_all(widgets, before=before)
            elif after is not None:
                await messages_area.mount_all(widgets, after=after)
            else:
                await messages_area.mount_all(widgets)

        for widget in widgets:
            if isinstance(widget, StreamingMessageBase):
                await widget.write_initial_content()

    def _is_tool_enabled_in_main_agent(self, tool: str) -> bool:
        return tool in self.agent_loop.tool_manager.available_tools

    async def _wait_for_typing_pause(self) -> None:
        try:
            text_area = self.query_one(ChatTextArea)
        except Exception:
            return

        debounce_s = _resolve_typing_debounce_s()
        if text_area.time_since_last_keystroke() >= debounce_s:
            return

        if self._loading_widget:
            self._loading_widget.show_debounce_hint()

        try:
            while True:
                elapsed = text_area.time_since_last_keystroke()
                if elapsed >= debounce_s:
                    return
                await asyncio.sleep(debounce_s - elapsed)
        finally:
            if self._loading_widget:
                self._loading_widget.hide_debounce_hint()

    async def _approval_callback(
        self,
        tool: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission] | None,
    ) -> tuple[ApprovalResponse, str | None]:
        # Auto-approve only if parent is in auto-approve mode AND tool is enabled
        # This ensures subagents respect the main agent's tool restrictions
        if self.agent_loop and self.agent_loop.config.bypass_tool_permissions:
            if self._is_tool_enabled_in_main_agent(tool):
                return (ApprovalResponse.YES, None)

        async with self._user_interaction_lock:
            await self._wait_for_typing_pause()
            self._pending_approval = asyncio.Future()
            self._terminal_notifier.notify(NotificationContext.ACTION_REQUIRED)
            try:
                with paused_timer(self._loading_widget):
                    await self._switch_to_approval_app(tool, args, required_permissions)
                    result = await self._pending_approval
                return result
            finally:
                self._pending_approval = None
                await self._switch_to_input_app()

    async def _user_input_callback(self, args: BaseModel) -> BaseModel:
        question_args = cast(AskUserQuestionArgs, args)

        async with self._user_interaction_lock:
            await self._wait_for_typing_pause()
            self._pending_question = asyncio.Future()
            self._terminal_notifier.notify(NotificationContext.ACTION_REQUIRED)
            try:
                with paused_timer(self._loading_widget):
                    await self._switch_to_question_app(question_args)
                    result = await self._pending_question
                return result
            finally:
                self._pending_question = None
                await self._switch_to_input_app()

    async def _handle_turn_error(self) -> None:
        if self._loading_widget and self._loading_widget.parent:
            await self._loading_widget.remove()
        if self.event_handler:
            self.event_handler.stop_current_tool_call(success=False)

    async def _handle_agent_loop_init(self) -> None:
        show_init_spinner = not self.agent_loop.is_initialized
        if show_init_spinner:
            await self._ensure_loading_widget("Initializing", show_hint=False)
        await self.agent_loop.wait_until_ready()
        if show_init_spinner:
            await self._remove_loading_widget()
            self._refresh_banner()

    async def _handle_agent_loop_events(
        self, events: AsyncGenerator[BaseEvent]
    ) -> None:
        async for event in events:
            self._narrator_manager.on_turn_event(event)
            if isinstance(event, WaitingForInputEvent):
                await self._remove_loading_widget()
                if self._remote_manager.is_active:
                    await self._handle_remote_waiting_input(event)
            elif isinstance(event, HookStartEvent):
                await self._ensure_loading_widget(f"Running hook {event.hook_name}")
            elif self._loading_widget is None and is_progress_event(event):
                await self._ensure_loading_widget()
            if self.event_handler:
                await self.event_handler.handle_event(
                    event, loading_widget=self._loading_widget
                )

    async def _handle_agent_loop_turn(
        self,
        prompt: str,
        *,
        title_source: str | None = None,
        prebuilt_images: list[ImageAttachment] | None = None,
        prebuilt_payload: PathPromptPayload | None = None,
    ) -> None:
        self._agent_running = True

        await self._remove_loading_widget()

        try:
            await self._handle_agent_loop_init()
            await self._ensure_loading_widget()
            message_id = str(uuid4())
            prompt_payload = prebuilt_payload or build_path_prompt_payload(
                prompt, base_dir=Path.cwd()
            )
            self._send_at_mention_telemetry(prompt_payload, message_id)
            images = await self._resolve_turn_images(prompt_payload, prebuilt_images)
            if images is None:
                return
            rendered_prompt = render_path_prompt_from_payload(
                prompt_payload, skip_images=True
            )
            auto_title: str | None = None
            if self.agent_loop.session_logger.needs_initial_auto_title():
                auto_title = (
                    format_session_title(
                        build_title_segments(
                            title_source or prompt, base_dir=Path.cwd()
                        )
                    )
                    or None
                )
            self._narrator_manager.cancel()
            self._narrator_manager.on_turn_start(rendered_prompt)
            async with aclosing(
                self.agent_loop.act(
                    rendered_prompt,
                    client_message_id=message_id,
                    auto_title=auto_title,
                    images=images or None,
                )
            ) as events:
                await self._handle_agent_loop_events(events)
        except asyncio.CancelledError:
            await self._handle_turn_error()
            self._narrator_manager.on_turn_cancel()
            raise
        except Exception as e:
            await self._handle_turn_error()

            # _watch_init_completion already rendered the fatal startup error
            # and told the user to exit -- don't duplicate the message.
            if self._fatal_init_error:
                return

            message = self._resolve_turn_error_message(e)
            self._narrator_manager.on_turn_error(message)

            await self._mount_and_scroll(
                ErrorMessage(message, collapsed=self._tools_collapsed)
            )
        finally:
            self._narrator_manager.on_turn_end()
            self._agent_running = False
            self._interrupt_requested = False
            self._agent_task = None
            if self._loading_widget:
                await self._loading_widget.remove()
            self._loading_widget = None
            if self.event_handler:
                await self.event_handler.finalize_streaming()
            self._queue.notify_busy_changed()
            self._queue.start_drain_if_needed()
            await self._refresh_windowing_from_history()
            self._terminal_notifier.notify(NotificationContext.COMPLETE)

    def _resolve_turn_error_message(self, e: Exception) -> str:
        if isinstance(e, RateLimitError):
            return self._rate_limit_message()
        if isinstance(e, ContextTooLongError):
            return self._context_too_long_message()
        if isinstance(e, RefusalError):
            return self._refusal_message(e)
        return str(e)

    def _rate_limit_message(self) -> str:
        upgrade_to_pro = self._plan_info and (
            self._plan_info.plan_type
            in {WhoAmIPlanType.API, WhoAmIPlanType.UNAUTHORIZED}
            or self._plan_info.is_free_mistral_code_plan()
        )
        if upgrade_to_pro:
            return "Rate limits exceeded. Please wait a moment before trying again, or upgrade to Pro for higher rate limits and uninterrupted access."
        return "Rate limits exceeded. Please wait a moment before trying again."

    def _context_too_long_message(self) -> str:
        return (
            "The conversation context exceeds the model's maximum limit. "
            "The last messages and output of agent actions went above the allowed size.\n\n"
            "To recover:\n"
            "1. Use /rewind to undo recent messages and tool outputs\n"
            "2. Then use /compact to summarize the remaining conversation\n\n"
            "This will free up context space so you can continue working."
        )

    def _refusal_message(self, e: RefusalError) -> str:
        lead = "The model declined to respond and stopped early (refusal)."
        if e.category:
            lead += f"\nCategory: {e.category}."
        detail = e.explanation or (
            "This can happen with certain prompts or content. "
            "Try rephrasing your request or starting a new conversation."
        )
        return f"{lead}\n\n{detail}"

    async def _teleport_command(self, **kwargs: Any) -> None:
        await self._handle_teleport_command(show_message=False)

    async def _handle_teleport_command(
        self, value: str | None = None, show_message: bool = True
    ) -> None:
        has_history = any(msg.role != Role.system for msg in self.agent_loop.messages)
        if not value:
            if show_message:
                await self._mount_and_scroll(SlashCommandMessage("teleport"))
            if not has_history:
                send_teleport_early_failure_telemetry(
                    self.agent_loop.telemetry_client,
                    stage="no_history",
                    error_class="TeleportNoHistoryError",
                    nb_session_messages=len(self.agent_loop.messages[1:]),
                )
                await self._mount_and_scroll(
                    ErrorMessage(
                        "No conversation history to teleport.",
                        collapsed=self._tools_collapsed,
                    )
                )
                return
        elif show_message:
            await self._mount_and_scroll(TeleportUserMessage(value))
        self.run_worker(self._teleport(value), exclusive=False)

    async def _teleport(self, prompt: str | None = None) -> None:
        loading = LoadingWidget()
        await self._loading_area.mount(loading)

        teleport_msg = TeleportMessage()
        await self._mount_and_scroll(teleport_msg)

        if self._remote_manager.is_active:
            send_teleport_early_failure_telemetry(
                self.agent_loop.telemetry_client,
                stage="remote_session",
                error_class="TeleportRemoteSessionError",
                nb_session_messages=len(self.agent_loop.messages[1:]),
            )
            await loading.remove()
            await self._mount_and_scroll(
                ErrorMessage(
                    "Teleport is not available for remote sessions.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        try:
            gen = self.agent_loop.teleport_to_vibe_code(prompt)
            async for event in gen:
                match event:
                    case TeleportCheckingGitEvent():
                        teleport_msg.set_status("Preparing workspace...")
                    case TeleportPushRequiredEvent(
                        unpushed_count=count, branch_not_pushed=branch_not_pushed
                    ):
                        await loading.remove()
                        response = await self._ask_push_approval(
                            count, branch_not_pushed
                        )
                        await self._loading_area.mount(loading)
                        teleport_msg.set_status("Teleporting...")
                        next_event = await gen.asend(response)
                        if isinstance(next_event, TeleportPushingEvent):
                            teleport_msg.set_status("Syncing with remote...")
                    case TeleportPushingEvent():
                        teleport_msg.set_status("Syncing with remote...")
                    case TeleportStartingWorkflowEvent():
                        teleport_msg.set_status("Teleporting...")
                    case TeleportCompleteEvent(url=url):
                        teleport_msg.set_complete(url)
        except TeleportError as e:
            await teleport_msg.remove()
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
        finally:
            if loading.parent:
                await loading.remove()

    async def _ask_push_approval(
        self, count: int, branch_not_pushed: bool
    ) -> TeleportPushResponseEvent:
        if branch_not_pushed:
            question = "Your branch doesn't exist on remote. Push to continue?"
        else:
            word = f"commit{'s' if count != 1 else ''}"
            question = f"You have {count} unpushed {word}. Push to continue?"
        push_label = "Push and continue"
        result = await self._user_input_callback(
            AskUserQuestionArgs(
                questions=[
                    Question(
                        question=question,
                        header="Push",
                        options=[Choice(label=push_label), Choice(label="Cancel")],
                        hide_other=True,
                    )
                ]
            )
        )
        ok = (
            isinstance(result, AskUserQuestionResult)
            and not result.cancelled
            and bool(result.answers)
            and result.answers[0].answer == push_label
        )
        return TeleportPushResponseEvent(approved=ok)

    async def _interrupt_agent_loop(self) -> None:
        if not self._agent_running or self._interrupt_requested:
            return

        self._interrupt_requested = True

        if self._pending_approval and not self._pending_approval.done():
            feedback = str(
                get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
            )
            self._pending_approval.set_result((ApprovalResponse.NO, feedback))
        if self._pending_question and not self._pending_question.done():
            self._pending_question.set_result(
                AskUserQuestionResult(answers=[], cancelled=True)
            )

        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass

        if self.event_handler:
            self.event_handler.stop_current_tool_call(success=False)
            self.event_handler.stop_current_compact()
            await self.event_handler.finalize_streaming()

        self._agent_running = False
        await self._loading_area.remove_children()
        self._loading_widget = None

        await self._mount_and_scroll(InterruptMessage())

        self._interrupt_requested = False

    async def _show_help(self, **kwargs: Any) -> None:
        help_text = self.commands.get_help_text()
        await self._mount_and_scroll(UserCommandMessage(help_text))

    def _get_last_assistant_message_text(self) -> str | None:
        for child in reversed(self._messages_area.children):
            if not isinstance(child, AssistantMessage):
                continue
            if not (content := child.get_content().strip()):
                continue
            return content
        return None

    async def _copy_last_agent_message(self, **kwargs: Any) -> None:
        if (content := self._get_last_assistant_message_text()) is None:
            self.notify(
                "No agent message available to copy", severity="warning", timeout=3
            )
            return

        copied_text = copy_text_to_clipboard(
            self, content, success_message="Last agent message copied to clipboard"
        )
        if copied_text is not None:
            self.agent_loop.telemetry_client.send_user_copied_text(copied_text)

    async def _refresh_mcp_browser(self) -> str:
        await self.agent_loop.tool_manager.refresh_remote_tools_async()
        await self.agent_loop.refresh_system_prompt()
        self._refresh_banner()
        return "Refreshed."

    async def _show_mcp(self, cmd_args: str = "", **kwargs: Any) -> None:
        mcp_servers = self.config.mcp_servers
        connector_registry = (
            self.agent_loop.connector_registry if self._connectors_enabled else None
        )
        has_connectors = (
            connector_registry is not None and connector_registry.connector_count > 0
        )
        if not mcp_servers and not has_connectors:
            await self._mount_and_scroll(
                UserCommandMessage("No MCP servers or connectors configured.")
            )
            return

        if self._current_bottom_app == BottomApp.MCP:
            return
        name = cmd_args.strip()
        connector_names = (
            connector_registry.get_connector_names() if connector_registry else []
        )
        if (
            name
            and not any(s.name == name for s in mcp_servers)
            and name not in connector_names
        ):
            all_names = [s.name for s in mcp_servers] + connector_names
            entity = "MCP server or connector" if has_connectors else "MCP server"
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Unknown {entity}: {name}. Known: " + ", ".join(all_names),
                    collapsed=self._tools_collapsed,
                )
            )
            return
        await self._mount_and_scroll(UserCommandMessage("MCP servers opened..."))
        await self._switch_from_input(
            MCPApp(
                mcp_servers=mcp_servers,
                tool_manager=self.agent_loop.tool_manager,
                initial_server=name,
                connector_registry=connector_registry,
                get_vibe_config=lambda: self.agent_loop.config,
                refresh_callback=self._refresh_mcp_browser,
            )
        )

    async def _show_status(self, **kwargs: Any) -> None:
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

    async def _show_config(self, **kwargs: Any) -> None:
        """Switch to the configuration app in the bottom panel."""
        if self._current_bottom_app == BottomApp.Config:
            return
        await self._switch_to_config_app()

    async def _show_model(self, **kwargs: Any) -> None:
        """Switch to the model picker in the bottom panel."""
        if self._current_bottom_app == BottomApp.ModelPicker:
            return
        await self._switch_to_model_picker_app()

    async def _show_thinking(self, **kwargs: Any) -> None:
        """Switch to the thinking level picker in the bottom panel."""
        if self._current_bottom_app == BottomApp.ThinkingPicker:
            return
        await self._switch_to_thinking_picker_app()

    async def _show_theme(self, **kwargs: Any) -> None:
        if self._current_bottom_app == BottomApp.ThemePicker:
            return
        await self._switch_to_theme_picker_app()

    async def _show_proxy_setup(self, **kwargs: Any) -> None:
        if self._current_bottom_app == BottomApp.ProxySetup:
            return
        await self._switch_to_proxy_setup_app()

    async def _show_data_retention(self, **kwargs: Any) -> None:
        await self._mount_and_scroll(UserCommandMessage(DATA_RETENTION_MESSAGE))

    async def _rename_local_session(self, title: str) -> str:
        session_logger = self.agent_loop.session_logger
        if not session_logger.enabled or session_logger.session_metadata is None:
            raise ValueError("Session logging is disabled in configuration.")

        if (
            session_logger.session_dir is not None
            and session_logger.metadata_filepath.exists()
        ):
            await update_saved_session_title_at_path(session_logger.session_dir, title)

        session_logger.set_title(title)
        renamed_title = session_logger.session_metadata.title
        assert renamed_title is not None
        return renamed_title

    async def _rename_session(self, cmd_args: str = "", **kwargs: Any) -> None:
        if self._remote_manager.is_active:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Renaming is only supported for local sessions.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        title = cmd_args.strip()
        if not title:
            await self._mount_and_scroll(
                ErrorMessage("Usage: /rename <title>", collapsed=self._tools_collapsed)
            )
            return

        try:
            renamed_title = await self._rename_local_session(title)
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to rename session: {e}", collapsed=self._tools_collapsed
                )
            )
            return

        await self._mount_and_scroll(
            UserCommandMessage(f'Session renamed to "{renamed_title}".')
        )

    def _build_picker(self, sessions: list[ResumeSessionInfo]) -> SessionPickerApp:
        sessions = sorted(sessions, key=lambda s: s.end_time or "", reverse=True)
        return SessionPickerApp(
            sessions=sessions,
            latest_messages=session_latest_messages(sessions, self.config),
            current_session_id=self.agent_loop.session_id,
            cwd=str(Path.cwd()),
        )

    async def _merge_remote_into_picker(
        self, picker: SessionPickerApp, remote_task: asyncio.Task[RemoteResumeResult]
    ) -> None:
        remote_sessions, remote_error = await remote_task
        if not picker.is_mounted:
            return
        if remote_error is not None:
            await self._mount_and_scroll(
                ErrorMessage(remote_error, collapsed=self._tools_collapsed)
            )
        if remote_sessions:
            picker.add_sessions(
                remote_sessions, session_latest_messages(remote_sessions, self.config)
            )

    async def _cancel_resume_merge(self) -> None:
        """Cancel the task that fetch and merges remote sessions into the picker, if it exists."""
        if self._resume_merge_task is not None and not self._resume_merge_task.done():
            self._resume_merge_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._resume_merge_task
        self._resume_merge_task = None

    async def _close_remote_resume(self) -> None:
        """Close the remote resume connection and cancel any ongoing merge task.
        Used to gracefully close the connection when the app is exiting
        """
        await self._cancel_resume_merge()
        await self._remote_resume.aclose()

    async def _show_session_picker(self, **kwargs: Any) -> None:
        await self._cancel_resume_merge()
        remote_list_timeout = max(float(self.config.api_timeout), 10.0)
        remote_task = self._remote_resume.start(remote_list_timeout)

        # If there are no local sessions, show the remote picker directly
        if not self.config.session_logging.enabled or not (
            local_sessions := list_local_resume_sessions(self.config, str(Path.cwd()))
        ):
            await self._show_session_picker_remote_only(remote_task)
            return

        picker = self._build_picker(local_sessions)
        await self._switch_from_input(picker)
        self._resume_merge_task = asyncio.create_task(
            self._merge_remote_into_picker(picker, remote_task)
        )

    async def _show_session_picker_remote_only(
        self, remote_task: asyncio.Task[RemoteResumeResult]
    ) -> None:
        await self._ensure_loading_widget("Loading sessions")
        try:
            remote_sessions, remote_error = await remote_task
        except asyncio.CancelledError:
            return
        finally:
            await self._remove_loading_widget()

        if remote_error is not None:
            await self._mount_and_scroll(
                ErrorMessage(remote_error, collapsed=self._tools_collapsed)
            )

        if not remote_sessions:
            await self._mount_and_scroll(
                UserCommandMessage("No sessions found for this directory.")
            )
            return

        await self._switch_from_input(self._build_picker(remote_sessions))

    async def on_session_picker_app_session_selected(
        self, event: SessionPickerApp.SessionSelected
    ) -> None:
        await self._switch_to_input_app()
        session = ResumeSessionInfo(
            session_id=event.session_id,
            source=event.source,
            cwd="",
            title=None,
            end_time=None,
        )
        try:
            if event.source == "local":
                await self._resume_local_session(session)
            elif event.source == "remote":
                await self._resume_remote_session(session)
            else:
                raise ValueError(f"Unknown session source: {event.source}")
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to load session: {e}", collapsed=self._tools_collapsed
                )
            )

    async def on_session_picker_app_session_delete_requested(
        self, event: SessionPickerApp.SessionDeleteRequested
    ) -> None:
        if not can_delete_resume_session_source(event.source):
            self._clear_pending_session_delete(event.option_id)
            await self._mount_and_scroll(
                ErrorMessage(
                    "Deleting remote sessions is not supported.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if event.source == "local" and event.session_id == self.agent_loop.session_id:
            self._clear_pending_session_delete(event.option_id)
            await self._mount_and_scroll(
                ErrorMessage(
                    "Deleting the current session is not supported.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        try:
            await delete_saved_session(event.session_id, self.config.session_logging)
        except Exception as e:
            self._clear_pending_session_delete(event.option_id)
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to delete session: {e}", collapsed=self._tools_collapsed
                )
            )
            return

        try:
            picker = self.query_one(SessionPickerApp)
        except Exception:
            picker = None

        if picker is not None:
            picker.remove_session(event.option_id)

        await self._mount_and_scroll(
            UserCommandMessage(
                f"Deleted session `{short_session_id(event.session_id)}`."
            )
        )

        if picker is not None and not picker.has_sessions:
            await self._switch_to_input_app()
            await self._mount_and_scroll(
                UserCommandMessage("No saved sessions left for this directory.")
            )

    def _clear_pending_session_delete(self, option_id: str) -> None:
        try:
            self.query_one(SessionPickerApp).clear_pending_delete(option_id)
        except Exception:
            pass

    async def on_session_picker_app_cancelled(
        self, event: SessionPickerApp.Cancelled
    ) -> None:
        await self._switch_to_input_app()

        await self._mount_and_scroll(UserCommandMessage("Resume cancelled."))

    async def _resume_local_session(self, session: ResumeSessionInfo) -> None:
        await self._remote_manager.detach()
        session_config = self.config.session_logging
        session_path = SessionLoader.find_session_by_id(
            session.session_id, session_config
        )

        if not session_path:
            raise ValueError(
                f"Session `{short_session_id(session.session_id)}` not found."
            )

        self._emit_session_closed_for_active_session()

        loaded_messages, metadata = SessionLoader.load_session(session_path)
        if self._chat_input_container:
            self._chat_input_container.set_custom_border(None)

        non_system_messages = [
            msg for msg in loaded_messages if msg.role != Role.system
        ]

        self.agent_loop.session_id = session.session_id
        self.agent_loop.parent_session_id = metadata.get("parent_session_id")
        self.agent_loop.session_logger.resume_existing_session(
            session.session_id, session_path
        )
        await self.agent_loop.hydrate_experiments_from_session()
        current_system_messages = [
            msg for msg in self.agent_loop.messages if msg.role == Role.system
        ]
        self.agent_loop.messages.reset(current_system_messages + non_system_messages)
        self._refresh_profile_widgets()

        self._reset_ui_state()
        await self._load_more.hide()

        await self._messages_area.remove_children()

        if self.event_handler:
            self.event_handler.is_remote = False
        await self._resume_history_from_messages()
        self._loop_runner.restore_from_session()
        await self._mount_and_scroll(
            UserCommandMessage(
                f"Resumed session `{short_session_id(session.session_id)}`"
            )
        )

    async def _resume_remote_session(self, session: ResumeSessionInfo) -> None:
        self.agent_loop.telemetry_client.send_remote_resume_requested(
            session_id=session.session_id
        )
        await self._remote_manager.attach(
            session_id=session.session_id, config=self.config
        )
        self._emit_session_closed_for_active_session()
        self.agent_loop.session_id = session.session_id
        self._refresh_profile_widgets()
        if self._chat_input_container:
            self._chat_input_container.set_custom_border(None)

        self._reset_ui_state()
        await self._load_more.hide()

        await self._messages_area.remove_children()

        if self.event_handler:
            self.event_handler.is_remote = True
        self._remote_manager.start_stream(self)

    async def on_remote_event(self, event: BaseEvent, loading_widget: Any) -> None:
        if self.event_handler:
            await self.event_handler.handle_event(event, loading_widget=loading_widget)

    async def on_remote_waiting_input(self, event: WaitingForInputEvent) -> None:
        await self._handle_remote_waiting_input(event)

    async def on_remote_user_message_cleared_input(self) -> None:
        await self._switch_to_input_app()

    async def on_remote_stream_error(self, error: str) -> None:
        await self._mount_and_scroll(
            ErrorMessage(error, collapsed=self._tools_collapsed)
        )

    async def on_remote_stream_ended(self, msg_type: str, text: str) -> None:
        if msg_type == "error":
            widget = ErrorMessage(text, collapsed=self._tools_collapsed)
        elif msg_type == "warning":
            widget = WarningMessage(text)
        else:
            widget = UserCommandMessage(text)
        await self._mount_and_scroll(widget)
        if self._chat_input_container:
            self._chat_input_container.set_custom_border("Remote session ended")

    async def on_remote_finalize_streaming(self) -> None:
        if self.event_handler:
            await self.event_handler.finalize_streaming()

    async def remove_loading(self) -> None:
        await self._remove_loading_widget()

    async def ensure_loading(self, status: str = DEFAULT_LOADING_STATUS) -> None:
        await self._ensure_loading_widget(status)

    @property
    def loading_widget(self) -> LoadingWidget | None:
        return self._loading_widget

    async def _reload_config(self, **kwargs: Any) -> None:
        try:
            self._reset_ui_state()
            await self._load_more.hide()
            base_config = VibeConfig.load()

            await self.agent_loop.reload_with_initial_messages(base_config=base_config)
            await self._resolve_plan()
            self._narrator_manager.sync()

            if self._banner:
                cc, ct = compute_connector_counts(
                    base_config, self.agent_loop.connector_registry
                )
                self._banner.set_state(
                    base_config,
                    self.agent_loop.skill_manager,
                    connectors_connected=cc,
                    connectors_total=ct,
                    hooks_count=self.agent_loop.hooks_count,
                    plan_description=plan_title(self._plan_info),
                )
            self._show_config_issues()
            await self._mount_and_scroll(
                UserCommandMessage(
                    "Configuration reloaded (includes agent instructions and skills)."
                )
            )
            stripped_count = (
                self.agent_loop.count_history_images_unsupported_by_active_model()
            )
            if stripped_count > 0:
                try:
                    model_alias = self.agent_loop.config.get_active_model().alias
                except ValueError:
                    model_alias = "the active model"
                noun = "image" if stripped_count == 1 else "images"
                await self._mount_and_scroll(
                    WarningMessage(
                        f"{stripped_count} {noun} from earlier turns will be omitted "
                        f"when sending to {model_alias} (no vision support)."
                    )
                )
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to reload config: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _install_lean(self, **kwargs: Any) -> None:
        current = list(self.agent_loop.base_config.installed_agents)
        if "lean" in current:
            await self._mount_and_scroll(
                UserCommandMessage("Lean agent is already installed.")
            )
            return
        VibeConfig.save_updates({"installed_agents": sorted([*current, "lean"])})
        await self._reload_config()

    async def _uninstall_lean(self, **kwargs: Any) -> None:
        current = list(self.agent_loop.base_config.installed_agents)
        if "lean" not in current:
            await self._mount_and_scroll(
                UserCommandMessage("Lean agent is not installed.")
            )
            return
        VibeConfig.save_updates({
            "installed_agents": [a for a in current if a != "lean"]
        })
        await self._reload_config()

    async def _clear_history(self, **kwargs: Any) -> None:
        try:
            self._reset_ui_state()
            if self._remote_manager.is_active:
                await self._remote_manager.detach()
                self._refresh_profile_widgets()
                if self.event_handler:
                    self.event_handler.is_remote = False
            if self._chat_input_container:
                self._chat_input_container.set_custom_border(None)
            await self.agent_loop.clear_history()
            if self.event_handler:
                await self.event_handler.finalize_streaming()
            await self._messages_area.remove_children()

            await self._messages_area.mount(SlashCommandMessage("clear"))
            await self._mount_and_scroll(
                UserCommandMessage("Conversation history cleared!")
            )
            self._chat_widget.scroll_home(animate=False)

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to clear history: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _show_log_path(self, **kwargs: Any) -> None:
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

    async def _loop_command(self, cmd_args: str = "", **kwargs: Any) -> None:
        widget = await self._loop_runner.handle_command(cmd_args)
        await self._mount_and_scroll(widget)

    async def _compact_history(self, cmd_args: str = "", **kwargs: Any) -> None:
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

        old_session_id = self.agent_loop.session_id
        compact_msg = CompactMessage()
        self.event_handler.current_compact = compact_msg
        await self._mount_and_scroll(compact_msg)

        self._agent_task = asyncio.create_task(
            self._run_compact(compact_msg, old_session_id, cmd_args.strip())
        )

    async def _run_compact(
        self,
        compact_msg: CompactMessage,
        old_session_id: str,
        extra_instructions: str = "",
    ) -> None:
        self._agent_running = True
        try:
            await self.agent_loop.compact(extra_instructions=extra_instructions)
            compact_msg.set_complete(
                old_session_id=old_session_id, new_session_id=self.agent_loop.session_id
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
        if self._remote_manager.is_active:
            return None
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
        return short_session_id(self.agent_loop.session_logger.session_id)

    async def _exit_app(self, **kwargs: Any) -> None:
        self._emit_session_closed_for_active_session()
        await self._loop_runner.stop()
        self._log_reader.shutdown()
        await self._voice_manager.close()
        await self._narrator_manager.close()
        await self._close_remote_resume()
        await self.agent_loop.aclose()
        try:
            await self.agent_loop.telemetry_client.aclose()
        except Exception as exc:
            logger.error("Failed to close telemetry client during exit", exc_info=exc)
        finally:
            self.exit(result=self._get_session_resume_info())

    def _make_default_voice_manager(self) -> VoiceManager:
        try:
            model = self.config.get_active_transcribe_model()
            provider = self.config.get_transcribe_provider_for_model(model)
            transcribe_client = make_transcribe_client(provider, model)
        except (ValueError, KeyError) as exc:
            logger.error(
                "Failed to initialize transcription, check transcribe model configuration",
                exc_info=exc,
            )
            transcribe_client = None

        return VoiceManager(
            lambda: self.config,
            audio_recorder=AudioRecorder(),
            transcribe_client=transcribe_client,
            telemetry_client=self.agent_loop.telemetry_client,
        )

    async def _show_voice_settings(self, **kwargs: Any) -> None:
        if self._current_bottom_app == BottomApp.Voice:
            return
        await self._switch_to_voice_app()

    async def _switch_from_input(self, widget: Widget, scroll: bool = False) -> None:
        bottom_container = self.query_one("#bottom-app-container")
        chat = self._chat_widget
        should_scroll = scroll and chat.is_at_bottom

        with self.batch_update():
            if self._chat_input_container:
                self._chat_input_container.display = False
                self._chat_input_container.disabled = True

            self._feedback_bar.hide()

            self._current_bottom_app = BottomApp[
                type(widget).__name__.removesuffix("App")
            ]
            await bottom_container.mount(widget)

        self.call_after_refresh(widget.focus)
        if should_scroll:
            self.call_after_refresh(chat.anchor)

    async def _switch_to_config_app(self) -> None:
        if self._current_bottom_app == BottomApp.Config:
            return

        await self._mount_and_scroll(UserCommandMessage("Configuration opened..."))
        await self._switch_from_input(ConfigApp(self.config))

    async def _switch_to_voice_app(self) -> None:
        if self._current_bottom_app == BottomApp.Voice:
            return

        await self._mount_and_scroll(UserCommandMessage("Voice settings opened..."))
        await self._switch_from_input(VoiceApp(self.config))

    async def _switch_to_model_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ModelPicker:
            return

        model_aliases = [m.alias for m in self.config.models]
        current_model = str(self.config.active_model)
        await self._switch_from_input(
            ModelPickerApp(model_aliases=model_aliases, current_model=current_model)
        )

    async def _switch_to_thinking_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ThinkingPicker:
            return

        from vibe.core.config import THINKING_LEVELS

        current_thinking = self.config.get_active_model().thinking
        await self._switch_from_input(
            ThinkingPickerApp(
                thinking_levels=THINKING_LEVELS, current_thinking=current_thinking
            )
        )

    async def _switch_to_theme_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ThemePicker:
            return

        await self._switch_from_input(
            ThemePickerApp(
                theme_names=sorted_theme_names(), current_theme=self.config.theme
            )
        )

    def _apply_theme(self, theme: str) -> None:
        if theme not in BUILTIN_THEMES:
            logger.warning("Unknown theme=%s; falling back to %s", theme, DEFAULT_THEME)
            self.theme = DEFAULT_THEME
            return
        self.theme = theme

    async def _switch_to_proxy_setup_app(self) -> None:
        if self._current_bottom_app == BottomApp.ProxySetup:
            return

        await self._mount_and_scroll(UserCommandMessage("Proxy setup opened..."))
        await self._switch_from_input(ProxySetupApp())

    async def _switch_to_approval_app(
        self,
        tool_name: str,
        tool_args: BaseModel,
        required_permissions: list[RequiredPermission] | None = None,
    ) -> None:
        approval_app = ApprovalApp(
            tool_name=tool_name,
            tool_args=tool_args,
            config=self.config,
            required_permissions=required_permissions,
        )
        await self._switch_from_input(approval_app, scroll=True)

    async def _switch_to_question_app(self, args: AskUserQuestionArgs) -> None:
        await self._switch_from_input(QuestionApp(args=args), scroll=True)

    async def _switch_to_input_app(self) -> None:
        if self._chat_input_container:
            self._chat_input_container.disabled = False
            self._chat_input_container.display = True
            self._current_bottom_app = BottomApp.Input
            self._refresh_profile_widgets()

        for app in BottomApp:
            if app != BottomApp.Input:
                try:
                    await self.query_one(f"#{app.value}-app").remove()
                except Exception:
                    pass

        if self._chat_input_container:
            self.call_after_refresh(self._chat_input_container.focus_input)
            if self._chat_widget.is_at_bottom:
                self.call_after_refresh(self._chat_widget.anchor)

    def _focus_current_bottom_app(self) -> None:
        try:
            match self._current_bottom_app:
                case BottomApp.Input:
                    self.query_one(ChatInputContainer).focus_input()
                case BottomApp.Config:
                    self.query_one(ConfigApp).focus()
                case BottomApp.ModelPicker:
                    self.query_one(ModelPickerApp).focus()
                case BottomApp.ThemePicker:
                    self.query_one(ThemePickerApp).focus()
                case BottomApp.ThinkingPicker:
                    self.query_one(ThinkingPickerApp).focus()
                case BottomApp.ProxySetup:
                    self.query_one(ProxySetupApp).focus()
                case BottomApp.Approval:
                    self.query_one(ApprovalApp).focus()
                case BottomApp.Question:
                    self.query_one(QuestionApp).focus()
                case BottomApp.SessionPicker:
                    self.query_one(SessionPickerApp).focus()
                case BottomApp.MCP:
                    self.query_one(MCPApp).focus()
                case BottomApp.ConnectorAuth:
                    self.query_one(ConnectorAuthApp).focus()
                case BottomApp.Rewind:
                    self.query_one(RewindApp).focus()
                case BottomApp.Voice:
                    self.query_one(VoiceApp).focus()
                case app:
                    assert_never(app)
        except Exception:
            pass

    def _handle_config_app_escape(self) -> None:
        try:
            config_app = self.query_one(ConfigApp)
            config_app.action_close()
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_voice_app_escape(self) -> None:
        try:
            voice_app = self.query_one(VoiceApp)
            voice_app.action_close()
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_approval_app_escape(self) -> None:
        try:
            approval_app = self.query_one(ApprovalApp)
            if not approval_app.is_within_grace_period():
                approval_app.action_reject()
                self.agent_loop.telemetry_client.send_user_cancelled_action(
                    "reject_approval"
                )
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_question_app_escape(self) -> None:
        try:
            question_app = self.query_one(QuestionApp)
            if not question_app.is_within_grace_period():
                question_app.action_cancel()
                self.agent_loop.telemetry_client.send_user_cancelled_action(
                    "cancel_question"
                )
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_model_picker_app_escape(self) -> None:
        try:
            model_picker = self.query_one(ModelPickerApp)
            model_picker.post_message(ModelPickerApp.Cancelled())
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_theme_picker_app_escape(self) -> None:
        try:
            theme_picker = self.query_one(ThemePickerApp)
            theme_picker.post_message(
                ThemePickerApp.Cancelled(original_theme=self.config.theme)
            )
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_thinking_picker_app_escape(self) -> None:
        try:
            thinking_picker = self.query_one(ThinkingPickerApp)
            thinking_picker.post_message(ThinkingPickerApp.Cancelled())
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_session_picker_app_escape(self) -> None:
        try:
            session_picker = self.query_one(SessionPickerApp)
            session_picker.action_cancel()
        except Exception:
            pass
        self._last_escape_time = None

    # --- Rewind mode ---

    def _get_user_message_widgets(self) -> list[UserMessage]:
        """Return all UserMessage widgets currently visible in #messages.

        Only includes messages with a valid message_index (i.e. real user
        messages, not slash-command echo messages).
        """
        return [
            child
            for child in self._messages_area.children
            if isinstance(child, UserMessage) and child.message_index is not None
        ]

    def _start_rewind_mode(self, **kwargs: Any) -> None:
        self.action_rewind_prev()

    def action_rewind_prev(self) -> None:
        if self._agent_running:
            return

        user_widgets = self._get_user_message_widgets()
        if not user_widgets:
            return

        if not self._rewind_mode:
            self._rewind_mode = True
            target = user_widgets[-1]
        elif self._rewind_highlighted_widget is not None:
            try:
                idx = user_widgets.index(self._rewind_highlighted_widget)
            except ValueError:
                idx = len(user_widgets)
            if idx <= 0:
                self.run_worker(self._rewind_prev_at_top(), exclusive=False)
                return
            target = user_widgets[idx - 1]
        else:
            target = user_widgets[-1]

        self.run_worker(self._select_rewind_widget(target), exclusive=False)

    async def _rewind_prev_at_top(self) -> None:
        """Handle alt+up when already at the topmost visible user message."""
        if self._load_more.widget is not None and self._windowing.has_backfill:
            await self.on_history_load_more_requested(HistoryLoadMoreRequested())
            user_widgets = self._get_user_message_widgets()
            if user_widgets and self._rewind_highlighted_widget is not None:
                # Find the current highlighted widget in the refreshed list
                # and select the one above it
                try:
                    idx = user_widgets.index(self._rewind_highlighted_widget)
                except ValueError:
                    idx = 0
                if idx > 0:
                    await self._select_rewind_widget(user_widgets[idx - 1])
                    return
        # No load more or already first message: scroll to top
        self.call_after_refresh(self._chat_widget.scroll_home, animate=False)

    def action_rewind_next(self) -> None:
        if not self._rewind_mode:
            return

        if self._rewind_highlighted_widget is None:
            return

        user_widgets = self._get_user_message_widgets()
        try:
            idx = user_widgets.index(self._rewind_highlighted_widget)
        except ValueError:
            return
        if idx >= len(user_widgets) - 1:
            return

        self.run_worker(
            self._select_rewind_widget(user_widgets[idx + 1]), exclusive=False
        )

    async def _select_rewind_widget(self, widget: UserMessage) -> None:
        """Highlight the given user message widget and show the rewind panel."""
        if self._rewind_highlighted_widget is not None:
            self._rewind_highlighted_widget.remove_class("rewind-selected")

        widget.add_class("rewind-selected")
        self._rewind_highlighted_widget = widget

        msg_index = widget.message_index
        has_file_changes = (
            msg_index is not None
            and self.agent_loop.rewind_manager.has_file_changes_at(msg_index)
        )

        await self._switch_to_rewind_app(
            widget.get_content(), has_file_changes=has_file_changes
        )

        chat = self._chat_widget
        self.call_after_refresh(chat.scroll_to_widget, widget, animate=False, top=True)

    async def _switch_to_rewind_app(
        self, message_preview: str, *, has_file_changes: bool
    ) -> None:
        """Show the rewind action panel at the bottom."""
        if self._current_bottom_app == BottomApp.Rewind:
            # Reuse existing widget if the option set hasn't changed
            try:
                existing = self.query_one(RewindApp)
                if existing.has_file_changes == has_file_changes:
                    existing.update_preview(message_preview)
                    return
                await existing.remove()
            except Exception:
                pass

            rewind_app = RewindApp(
                message_preview=message_preview, has_file_changes=has_file_changes
            )
            bottom_container = self.query_one("#bottom-app-container")
            self._current_bottom_app = BottomApp.Rewind
            await bottom_container.mount(rewind_app)
            self.call_after_refresh(rewind_app.focus)
        else:
            rewind_app = RewindApp(
                message_preview=message_preview, has_file_changes=has_file_changes
            )
            await self._switch_from_input(rewind_app)

    def _clear_rewind_state(self) -> None:
        if self._rewind_highlighted_widget is not None:
            self._rewind_highlighted_widget.remove_class("rewind-selected")
            self._rewind_highlighted_widget = None
        self._rewind_mode = False

    async def _exit_rewind_mode(self) -> None:
        """Exit rewind mode and restore the input panel."""
        self._clear_rewind_state()
        await self._switch_to_input_app()

    async def on_rewind_app_rewind_with_restore(
        self, message: RewindApp.RewindWithRestore
    ) -> None:
        await self._execute_rewind(restore_files=True)

    async def on_rewind_app_rewind_without_restore(
        self, message: RewindApp.RewindWithoutRestore
    ) -> None:
        await self._execute_rewind(restore_files=False)

    async def _execute_rewind(self, *, restore_files: bool) -> None:
        """Fork the session at the selected user message."""
        if not self._rewind_mode or self._rewind_highlighted_widget is None:
            return

        target_widget = self._rewind_highlighted_widget
        msg_index = target_widget.message_index

        if msg_index is None:
            return

        if msg_index < len(self.agent_loop.messages):
            try:
                (
                    message_content,
                    restore_errors,
                ) = await self.agent_loop.rewind_manager.rewind_to_message(
                    msg_index, restore_files=restore_files
                )
            except RewindError as exc:
                self.notify(str(exc), severity="error")
                return
        else:
            message_content = target_widget.get_content()
            restore_errors = []

        for error in restore_errors:
            self.notify(error, severity="warning")

        # Remove UI widgets from the selected message onward
        children = list(self._messages_area.children)
        try:
            target_idx = children.index(target_widget)
        except ValueError:
            target_idx = len(children)
        to_remove = children[target_idx:]
        if to_remove:
            await self._messages_area.remove_children(to_remove)

        self._clear_rewind_state()

        # Switch back to input and pre-fill with the original message
        await self._switch_to_input_app()
        if self._chat_input_container:
            self._chat_input_container.value = message_content

    # --- End rewind mode ---

    def _handle_input_app_escape(self) -> None:
        try:
            input_widget = self.query_one(ChatInputContainer)
            input_widget.value = ""
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_agent_running_escape(self) -> None:
        self.agent_loop.telemetry_client.send_user_cancelled_action("interrupt_agent")
        self.run_worker(self._interrupt_agent_loop(), exclusive=False)

    def _handle_bottom_app_close_escape(
        self, widget_type: type[MCPApp] | type[ProxySetupApp] | type[ConnectorAuthApp]
    ) -> None:
        try:
            self.query_one(widget_type).action_close()
        except Exception:
            pass
        self._last_escape_time = None

    def _try_interrupt_bottom_app_escape(self) -> bool:
        if self._current_bottom_app == BottomApp.Config:
            self._handle_config_app_escape()
        elif self._current_bottom_app == BottomApp.Voice:
            self._handle_voice_app_escape()
        elif self._current_bottom_app == BottomApp.MCP:
            self._handle_bottom_app_close_escape(MCPApp)
        elif self._current_bottom_app == BottomApp.ConnectorAuth:
            self._handle_bottom_app_close_escape(ConnectorAuthApp)
        elif self._current_bottom_app == BottomApp.ProxySetup:
            self._handle_bottom_app_close_escape(ProxySetupApp)
        elif self._current_bottom_app == BottomApp.Approval:
            self._handle_approval_app_escape()
        elif self._current_bottom_app == BottomApp.Question:
            self._handle_question_app_escape()
        elif self._current_bottom_app == BottomApp.ModelPicker:
            self._handle_model_picker_app_escape()
        elif self._current_bottom_app == BottomApp.ThemePicker:
            self._handle_theme_picker_app_escape()
        elif self._current_bottom_app == BottomApp.ThinkingPicker:
            self._handle_thinking_picker_app_escape()
        elif self._current_bottom_app == BottomApp.SessionPicker:
            self._handle_session_picker_app_escape()
        elif self._current_bottom_app == BottomApp.Rewind:
            self.run_worker(self._exit_rewind_mode(), exclusive=False)
            self._last_escape_time = None
        elif (
            self._current_bottom_app == BottomApp.Input
            and self._last_escape_time is not None
            and (time.monotonic() - self._last_escape_time) < DOUBLE_ESC_DELAY
        ):
            self._handle_input_app_escape()
        else:
            return False
        return True

    def _try_interrupt_no_job_steps(self) -> bool:
        if self._voice_manager.transcribe_state != TranscribeState.IDLE:
            self._voice_manager.cancel_recording()
            return True

        if (
            self._chat_input_container
            and self._chat_input_container.dismiss_completion()
        ):
            if self._chat_input_container.value.startswith("/"):
                self._chat_input_container.value = ""
            self._last_escape_time = None
            return True

        if self._try_interrupt_bottom_app_escape():
            return True

        if (
            self._narrator_manager.is_playing
            or self._narrator_manager.state != NarratorState.IDLE
        ):
            self._narrator_manager.cancel()
            return True

        return False

    def _try_interrupt_running_job(self) -> bool:
        interrupted = False
        if self._bash_task and not self._bash_task.done():
            self._bash_task.cancel()
            interrupted = True
        if self._agent_running:
            self._handle_agent_running_escape()
            interrupted = True
        return interrupted

    def _try_interrupt(self) -> bool:
        if self._try_interrupt_no_job_steps():
            return True

        interrupted = self._try_interrupt_running_job()
        if interrupted and self._input_queue:
            self._queue.set_paused(True)

        if not interrupted and self._input_queue:
            self._queue.set_paused(True)
            interrupted = True

        self._last_escape_time = time.monotonic()
        if self._chat_widget.is_at_bottom:
            self.call_after_refresh(self._chat_widget.anchor)
        self._focus_current_bottom_app()
        return interrupted

    def action_interrupt(self) -> None:
        self._try_interrupt()

    async def on_history_load_more_requested(self, _: HistoryLoadMoreRequested) -> None:
        self._load_more.set_enabled(False)
        try:
            if not self._windowing.has_backfill:
                await self._load_more.hide()
                return
            if (batch := self._windowing.next_load_more_batch()) is None:
                await self._load_more.hide()
                return
            messages_area = self._messages_area
            if self._tool_call_map is None:
                self._tool_call_map = {}
            if self._load_more.widget:
                before: Widget | int | None = None
                after: Widget | None = self._load_more.widget
            else:
                before = 0
                after = None
            await self._mount_history_batch(
                batch.messages,
                messages_area,
                self._tool_call_map,
                start_index=batch.start_index,
                before=before,
                after=after,
            )
            if not self._windowing.has_backfill:
                await self._load_more.hide()
            else:
                await self._load_more.show(messages_area, self._windowing.remaining)
        finally:
            self._load_more.set_enabled(True)

    async def action_toggle_tool(self) -> None:
        self._tools_collapsed = not self._tools_collapsed
        for section in self.query(CollapsibleSection):
            section.set_collapsed(self._tools_collapsed)

    def action_cycle_mode(self) -> None:
        if self._current_bottom_app != BottomApp.Input:
            return
        self._refresh_profile_widgets()
        self._focus_current_bottom_app()
        self.run_worker(self._cycle_agent(), group="mode_switch", exclusive=True)

    def _refresh_profile_widgets(self) -> None:
        self._update_profile_widgets(self.agent_loop.agent_profile)

    def _on_profile_changed(self) -> None:
        self._refresh_profile_widgets()
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        if self._banner:
            cc, ct = compute_connector_counts(
                self.config, self.agent_loop.connector_registry
            )
            self._banner.set_state(
                self.config,
                self.agent_loop.skill_manager,
                connectors_connected=cc,
                connectors_total=ct,
                hooks_count=self.agent_loop.hooks_count,
                plan_description=plan_title(self._plan_info),
            )

    def _update_profile_widgets(self, profile: AgentProfile) -> None:
        if self._chat_input_container:
            self._chat_input_container.set_safety(profile.safety)
            self._chat_input_container.set_agent_name(profile.display_name.lower())
            if self._remote_manager.is_active:
                session_id = self._remote_manager.session_id
                self._chat_input_container.set_custom_border(
                    f"Remote session {short_session_id(session_id, source='remote') if session_id else ''}",
                    ChatInputContainer.REMOTE_BORDER_CLASS,
                )
            else:
                self._chat_input_container.set_custom_border(None)

    async def _cycle_agent(self) -> None:
        new_profile = self.agent_loop.agent_manager.next_agent(
            self.agent_loop.agent_profile
        )
        self._update_profile_widgets(new_profile)
        if self._chat_input_container:
            self._chat_input_container.switching_mode = True

        loop = asyncio.get_running_loop()

        def schedule_switch() -> None:
            self._switch_agent_generation += 1
            my_gen = self._switch_agent_generation

            def switch_agent_sync() -> None:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self.agent_loop.switch_agent(new_profile.name), loop
                    )
                    future.result()
                    self.agent_loop.set_approval_callback(self._approval_callback)
                    self.agent_loop.set_user_input_callback(self._user_input_callback)
                finally:
                    if (
                        self._chat_input_container
                        and self._switch_agent_generation == my_gen
                    ):
                        self.call_from_thread(self._refresh_banner)
                        self.call_from_thread(
                            setattr, self._chat_input_container, "switching_mode", False
                        )

            self.run_worker(
                switch_agent_sync, group="switch_agent", exclusive=True, thread=True
            )

        self.call_after_refresh(schedule_switch)

    async def action_toggle_debug_console(self, **kwargs: Any) -> None:
        if self._debug_console is not None:
            await self._debug_console.remove()
            self._debug_console = None
        else:
            self._debug_console = DebugConsole(log_reader=self._log_reader)
            await self.mount(self._debug_console)

    def _get_chat_input(self) -> ChatInputContainer | None:
        input_widgets = self.query(ChatInputContainer)
        if input_widgets:
            return input_widgets.first()
        return None

    def action_interrupt_or_quit(self) -> None:
        # Ctrl+C priority ladder: clear input → second-press quit → bottom-app/voice/etc
        # no-op steps → pop last queued item (LIFO) → cancel running job → request quit.
        if (container := self._get_chat_input()) and container.value:
            container.value = ""
            return
        if self._quit_manager.is_confirmed("Ctrl+C"):
            self._force_quit()
            return
        if self._try_interrupt_no_job_steps():
            return
        if self._input_queue:
            self.run_worker(self._queue.pop_last(), exclusive=False)
            return
        if self._try_interrupt_running_job():
            return
        self._quit_manager.request_confirmation(
            "Ctrl+C", self._queue.quit_warning_extra()
        )

    def action_delete_right_or_quit(self) -> None:
        if (container := self._get_chat_input()) and container.value:
            if container.input_widget:
                container.input_widget.action_delete_right()
            return

        if self._quit_manager.is_confirmed("Ctrl+D"):
            self._force_quit()
            return
        self._quit_manager.request_confirmation(
            "Ctrl+D", self._queue.quit_warning_extra()
        )

    def _emit_session_closed_for_active_session(self) -> None:
        self.agent_loop.emit_session_closed_telemetry()

    def _force_quit(self) -> None:
        if self._force_quit_task is not None and not self._force_quit_task.done():
            return
        self._force_quit_task = asyncio.create_task(self._force_quit_async())

    async def _force_quit_async(self) -> None:
        self._emit_session_closed_for_active_session()
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        if self._bash_task and not self._bash_task.done():
            self._bash_task.cancel()
        self._remote_manager.cancel_stream_task()

        self._log_reader.shutdown()
        self._narrator_manager.cancel()
        await self._close_remote_resume()
        await self.agent_loop.aclose()
        try:
            await self.agent_loop.telemetry_client.aclose()
        except Exception as exc:
            logger.error(
                "Failed to close telemetry client during force quit", exc_info=exc
            )
        finally:
            self.exit(result=self._get_session_resume_info())

    def action_scroll_chat_up(self) -> None:
        try:
            self._chat_widget.scroll_relative(y=-5, animate=False)
        except Exception:
            pass

    def action_scroll_chat_down(self) -> None:
        try:
            self._chat_widget.scroll_relative(y=5, animate=False)
        except Exception:
            pass

    async def _show_dangerous_directory_warning(self) -> None:
        is_dangerous, reason = is_dangerous_directory()
        if is_dangerous:
            warning = (
                f"⚠ WARNING: {reason}\n\nRunning in this location is not recommended."
            )
            await self._mount_and_scroll(WarningMessage(warning, show_border=False))

    async def _record_vscode_extension_promo_shown(self) -> None:
        if self._vscode_extension_promo is None:
            return
        previous_count = (
            self._vscode_extension_promo.initial_state.shown_count
            if self._vscode_extension_promo.initial_state is not None
            else 0
        )
        try:
            await self._vscode_extension_promo.repository.set(
                VscodeExtensionPromoState(shown_count=previous_count + 1)
            )
        except Exception:
            logger.warning(
                "Failed to persist VSCode extension promo shown count", exc_info=True
            )

    async def _check_and_show_whats_new(self) -> None:
        if self._update_cache_repository is None:
            await self._maybe_show_vscode_extension_promo()
            return

        if not await should_show_whats_new(
            self._current_version, self._update_cache_repository
        ):
            await self._maybe_show_vscode_extension_promo()
            return

        content = load_whats_new_content()
        if content is not None:
            body = content
            plan_offer = plan_offer_cta(
                self._plan_info, vibe_base_url=self.config.vibe_base_url
            )
            if plan_offer is not None:
                body = f"{body}\n\n{plan_offer}"
            if self._show_vscode_extension_promo:
                body = f"{body}{VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX}"
            whats_new_message = WhatsNewMessage(body)
            if self._history_widget_indices:
                whats_new_message.add_class("after-history")
            chat = self._chat_widget
            should_anchor = chat.is_at_bottom
            await chat.mount(whats_new_message, after=self._messages_area)
            self._whats_new_message = whats_new_message
            if should_anchor:
                chat.anchor()
            if self._show_vscode_extension_promo:
                self.run_worker(
                    self._record_vscode_extension_promo_shown(), exclusive=False
                )
        else:
            await self._maybe_show_vscode_extension_promo()
        await mark_version_as_seen(self._current_version, self._update_cache_repository)

    async def _maybe_show_vscode_extension_promo(self) -> None:
        if not self._show_vscode_extension_promo:
            return
        promo_message = VscodeExtensionPromoMessage()
        chat = self._chat_widget
        should_anchor = chat.is_at_bottom
        await chat.mount(promo_message, before=self._messages_area)
        if should_anchor:
            chat.anchor()
        self.run_worker(self._record_vscode_extension_promo_shown(), exclusive=False)

    async def _resolve_plan(self) -> None:
        if self._plan_offer_gateway is None:
            self._plan_info = None
            self._refresh_command_registry()
            return

        try:
            if not self.config.is_active_model_mistral():
                self._plan_info = None
                return

            provider = self.config.get_active_provider()
            api_key = resolve_api_key_for_plan(provider)
            self._plan_info = await decide_plan_offer(api_key, self._plan_offer_gateway)
        except Exception as exc:
            logger.warning(
                "Plan-offer check failed (%s).", type(exc).__name__, exc_info=True
            )
            self._plan_info = None
        finally:
            self._refresh_command_registry()

    async def _mount_and_scroll(
        self, widget: Widget, after: Widget | None = None, before: Widget | None = None
    ) -> None:
        messages_area = self._messages_area
        is_user_initiated = isinstance(widget, (UserMessage, UserCommandMessage))
        should_anchor = is_user_initiated or self._chat_widget.is_at_bottom

        pin_anchor: Widget | None = None
        if after is None:
            pin_anchor = self._queue.pin_target(messages_area)

        with self.batch_update():
            if before is not None and before.parent is messages_area:
                await messages_area.mount(widget, before=before)
            elif after is not None and after.parent is messages_area:
                await messages_area.mount(widget, after=after)
            elif pin_anchor is not None:
                await messages_area.mount(widget, before=pin_anchor)
            else:
                await messages_area.mount(widget)
            if isinstance(widget, StreamingMessageBase):
                await widget.write_initial_content()

        self.call_after_refresh(self._try_prune)
        if should_anchor:
            self._chat_widget.anchor()

    async def _try_prune(self) -> None:
        pruned = await prune_oldest_children(
            self._messages_area, PRUNE_LOW_MARK, PRUNE_HIGH_MARK
        )
        if self._load_more.widget and not self._load_more.widget.parent:
            self._load_more.widget = None
        if pruned:
            if self._chat_widget.is_at_bottom:
                self.call_later(self._chat_widget.anchor)

    async def _refresh_windowing_from_history(self) -> None:
        if self._load_more.widget is None:
            return
        messages_area = self._messages_area
        has_backfill, tool_call_map = sync_backfill_state(
            history_messages=non_system_history_messages(self.agent_loop.messages),
            messages_children=list(messages_area.children),
            history_widget_indices=self._history_widget_indices,
            windowing=self._windowing,
        )
        self._tool_call_map = tool_call_map
        await self._load_more.set_visible(
            messages_area, visible=has_backfill, remaining=self._windowing.remaining
        )

    def _schedule_update_notification(self) -> None:
        if self._update_notifier is None or not self.config.enable_update_checks:
            return

        asyncio.create_task(self._check_update(), name="version-update-check")

    async def _check_update(self) -> None:
        if self._update_notifier is None or self._update_cache_repository is None:
            return

        try:
            await get_update_if_available(
                update_notifier=self._update_notifier,
                current_version=self._current_version,
                update_cache_repository=self._update_cache_repository,
            )
        except UpdateError as exc:
            logger.warning("Update check failed", exc_info=exc)
        except Exception as exc:
            logger.debug("Update check failed", exc_info=exc)

    def action_copy_selection(self) -> None:
        copied_text = copy_selection_to_clipboard(self, show_toast=False)
        if copied_text is not None:
            self.agent_loop.telemetry_client.send_user_copied_text(copied_text)

    def on_mouse_up(self, event: MouseUp) -> None:
        if self.config.autocopy_to_clipboard:
            copied_text = copy_selection_to_clipboard(self, show_toast=False)
            if copied_text is not None:
                self._clipboard_notice.update("Selection copied to clipboard")
                self._clipboard_notice.display = True
                if self._clipboard_hide_timer is not None:
                    self._clipboard_hide_timer.stop()
                self._clipboard_hide_timer = self.set_timer(
                    2.0, lambda: setattr(self._clipboard_notice, "display", False)
                )
                self.agent_loop.telemetry_client.send_user_copied_text(copied_text)

    def on_app_blur(self, event: AppBlur) -> None:
        self._terminal_notifier.on_blur()
        if self._chat_input_container and self._chat_input_container.input_widget:
            self._chat_input_container.input_widget.set_app_focus(False)

    def on_app_focus(self, event: AppFocus) -> None:
        self._terminal_notifier.on_focus()
        if self._chat_input_container and self._chat_input_container.input_widget:
            self._chat_input_container.input_widget.set_app_focus(True)

    def action_open_plan_in_editor(self) -> None:
        if self.event_handler is None:
            return

        if plan_file_message := self.event_handler.plan_file_message:
            plan_file_message.open_in_editor()

    def action_suspend_with_message(self) -> None:
        if WINDOWS or self._driver is None or not self._driver.can_suspend:
            return
        with self.suspend():
            rprint(
                "Mistral Vibe has been suspended. Run [bold cyan]fg[/bold cyan] to bring Mistral Vibe back."
            )
            os.kill(os.getpid(), signal.SIGTSTP)

    def _on_driver_signal_resume(self, event: Driver.SignalResume) -> None:
        # Textual doesn't repaint after resuming from Ctrl+Z (SIGTSTP);
        # force a full layout refresh so the UI isn't garbled.
        self.refresh(layout=True)

    def _make_default_narrator_manager(self) -> NarratorManager:
        return NarratorManager(
            config_getter=lambda: self.config,
            audio_player=AudioPlayer(),
            telemetry_client=self.agent_loop.telemetry_client,
        )


def run_textual_ui(
    agent_loop: AgentLoop,
    update_cache_repository: UpdateCacheRepository,
    startup: StartupOptions | None = None,
) -> None:
    from vibe.cli.stderr_guard import stderr_guard

    update_notifier = PyPIUpdateGateway(project_name="mistral-vibe")
    plan_offer_gateway = HttpWhoAmIGateway(base_url=agent_loop.config.console_base_url)
    vscode_extension_promo_repository = FileSystemVscodeExtensionPromoRepository()
    vscode_extension_promo = VscodeExtensionPromo(
        repository=vscode_extension_promo_repository,
        initial_state=asyncio.run(vscode_extension_promo_repository.get()),
    )

    with stderr_guard():
        app = VibeApp(
            agent_loop=agent_loop,
            startup=startup,
            update_notifier=update_notifier,
            update_cache_repository=update_cache_repository,
            plan_offer_gateway=plan_offer_gateway,
            vscode_extension_promo=vscode_extension_promo,
        )
        session_id = app.run()

    print_session_resume_message(
        session_id, agent_loop.stats, agent_loop.config.session_logging
    )
