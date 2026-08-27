from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import aclosing, suppress
from dataclasses import dataclass, replace
from enum import StrEnum, auto
from functools import partial
import gc
import os
from pathlib import Path
import signal
import sys
import time
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import uuid4
from weakref import WeakKeyDictionary
import webbrowser

from rich import print as rprint
from textual.app import WINDOWS, App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalGroup, VerticalScroll
from textual.dom import NoScreen
from textual.driver import Driver
from textual.events import AppBlur, AppFocus, MouseScrollDown, MouseScrollUp, MouseUp
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static
from textual.worker import Worker, WorkerFailed, WorkerState

from vibe import __version__ as CORE_VERSION
from vibe.app_server import AppServerHost, AppServerSession, SessionExitSummary
from vibe.app_server.config import (
    THINKING_LEVELS,
    ConfigView,
    ModelConfigView,
    ThinkingLevel,
)
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    ServerError,
    ServerWarning,
    StatsUpdated,
    TurnCompleted,
    TurnStarted,
)
from vibe.app_server.models import (
    AgentSafety,
    AgentSummary,
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    EffectDetail,
    ImageAttachment,
    MCPSourceKind,
    MentionStats,
    PreparedPrompt,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    PublicReasoningEntry,
    PublicSession,
    PublicTurnStatus,
    QuestionChoice,
    RequiredPermission,
    TeleportCheckingGit,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
    TokenUsage,
    TurnErrorCode,
    UserInputCallbackDetail,
    UserInputCallbackOutput,
    UserQuestion,
    UserQuestionRequest,
    UserQuestionResult,
    WaitingForInputNoticeDetail,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConfigReadResponse,
    ConfigWriteOpWire,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerTurnError
from vibe.cli._process_title import process_id_label
from vibe.cli.audio_request_metadata import build_audio_request_metadata
from vibe.cli.clipboard import (
    NATIVE_COPY_HINT,
    ClipboardCopyResult,
    copy_selection_to_clipboard,
    copy_text_to_clipboard,
)
from vibe.cli.commands import Command, CommandContext, CommandRegistry
from vibe.cli.lazy_audio_managers import (
    check_audio_available,
    create_default_narrator_manager,
    create_default_voice_manager,
)
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorManagerPort,
    NarratorState,
)
from vibe.cli.plan_offer.presentation import plan_offer_cta, plan_title
from vibe.cli.process_start import PROCESS_START_MONOTONIC, PROCESS_START_WALLCLOCK
from vibe.cli.terminal_detect import Terminal, detect_terminal
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.mcp_commands import (
    MCP_ADD_HELP,
    is_mcp_add_help_request,
    parse_mcp_add_args,
    parse_mcp_subcommand,
)
from vibe.cli.textual_ui.message_queue import (
    MessageQueue,
    QueueController,
    QueuePorts,
    SideChannelController,
    SideChannelPorts,
)
from vibe.cli.textual_ui.notifications import (
    NotificationContext,
    NotificationPort,
    TextualNotificationAdapter,
)
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.quit_manager import QuitManager
from vibe.cli.textual_ui.scheduled_loop_runner import ScheduledLoopCommands
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.banner.banner import Banner
from vibe.cli.textual_ui.widgets.chat_input import ChatInputBody, ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.input_kinds import (
    Bash,
    EmptyBash,
    Prompt,
    Skill,
    SlashCommand,
    Teleport,
    classify,
)
from vibe.cli.textual_ui.widgets.chat_input.paste_image import (
    handle_clipboard_image_paste,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.debug_console import DebugConsole
from vibe.cli.textual_ui.widgets.feedback_bar import FeedbackBar
from vibe.cli.textual_ui.widgets.inline_notice import InlineNotice
from vibe.cli.textual_ui.widgets.links import normalize_url
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreRequested
from vibe.cli.textual_ui.widgets.loading import (
    DEFAULT_LOADING_STATUS,
    LoadingWidget,
    paused_timer,
)
from vibe.cli.textual_ui.widgets.log_level_picker import LogLevelPickerApp
from vibe.cli.textual_ui.widgets.messages import (
    VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX,
    AssistantMessage,
    BashOutputMessage,
    CustomToolsDeprecationMessage,
    ErrorMessage,
    GreetingMessage,
    InterruptMessage,
    PlanFileMessage,
    ReasoningMessage,
    SlashCommandMessage,
    StreamingMessageBase,
    TeleportUserMessage,
    UserCommandMessage,
    UserMessage,
    VscodeExtensionPromoMessage,
    WarningMessage,
    WhatsNewMessage,
)
from vibe.cli.textual_ui.widgets.model_picker import ModelOption, ModelPickerApp
from vibe.cli.textual_ui.widgets.narrator_status import NarratorStatus
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.path_display import PathDisplay
from vibe.cli.textual_ui.widgets.proxy_setup_app import ProxySetupApp
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.cli.textual_ui.widgets.rewind_app import RewindApp
from vibe.cli.textual_ui.widgets.rewind_fork_message import RewindForkMessage
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp
from vibe.cli.textual_ui.widgets.teleport_message import TeleportMessage
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp, sorted_theme_names
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.cli.textual_ui.widgets.tool_widgets import (
    EditApprovalWidget,
    EditResultWidget,
)
from vibe.cli.textual_ui.widgets.tools import (
    ToolCallMessage,
    ToolGroup,
    ToolResultMessage,
)
from vibe.cli.textual_ui.widgets.vibe_code_project import (
    VibeCodeProjectCreateApp,
    VibeCodeProjectPickerApp,
    VibeCodeProjectPickerUiState,
    suggested_default_branch,
)
from vibe.cli.textual_ui.widgets.voice_app import VoiceApp
from vibe.cli.textual_ui.windowing import (
    HISTORY_RESUME_TAIL_MESSAGES,
    LOAD_MORE_BATCH_SIZE,
    HistoryLoadMoreManager,
    SessionWindowing,
    build_history_widgets,
    create_resume_plan,
    shift_history_widget_indices,
    should_resume_history,
    sync_backfill_state,
)
from vibe.cli.textual_ui.word_selection import WordSelectScreen
from vibe.cli.theme import resolve_auto_theme, resolve_theme, resolve_theme_name
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
from vibe.cli.voice_manager import VoiceManagerPort
from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
)
from vibe.cli.vscode_extension_promo import (
    FileSystemVscodeExtensionPromoRepository,
    VscodeExtensionPromo,
    VscodeExtensionPromoState,
    should_show_promo,
)
from vibe.config_values import FALLBACK_THEME
from vibe.observability.logging import (
    get_log_level_chain,
    logger,
    set_config_log_level,
    set_session_override,
)
from vibe.observability.sentry import capture_sentry_exception
from vibe.utils.audio import RecordingMode
from vibe.utils.cache_store import FileSystemCacheStore
from vibe.utils.data_retention import DATA_RETENTION_MESSAGE
from vibe.utils.paths import is_dangerous_directory
from vibe.utils.repository import repo_url_label
from vibe.utils.retry_prompt import build_retry_prompt
from vibe.utils.session_id import shorten_session_id

_VSCODE_FAMILY_TERMINALS = {Terminal.VSCODE, Terminal.VSCODE_INSIDERS, Terminal.CURSOR}

# Expected turn outcomes with bespoke user messages; not worth reporting to Sentry.
_BENIGN_TURN_ERROR_CODES = {
    TurnErrorCode.RATE_LIMIT,
    TurnErrorCode.CONTEXT_TOO_LONG,
    TurnErrorCode.RESPONSE_TOO_LONG,
    TurnErrorCode.REFUSAL,
    TurnErrorCode.INCOMPLETE_STREAM,
    TurnErrorCode.INVALID_MODEL,
}

_RETRYABLE_TURN_ERROR_CODES = {
    TurnErrorCode.BACKEND_ERROR,
    TurnErrorCode.RATE_LIMIT,
    TurnErrorCode.RESPONSE_TOO_LONG,
    TurnErrorCode.INCOMPLETE_STREAM,
}

_MAX_INCOMPLETE_STREAM_RETRIES = 2


if TYPE_CHECKING:
    from vibe.app_server.resources import PluginCatalogDiff
    from vibe.cli.textual_ui.screens.config import ConfigWriteResult
    from vibe.cli.textual_ui.widgets.connector_auth_app import ConnectorAuthApp
    from vibe.cli.textual_ui.widgets.mcp_app import MCPApp
    from vibe.cli.textual_ui.widgets.mcp_oauth_app import MCPOAuthApp
    from vibe.cli.textual_ui.widgets.plugins_app import PluginsApp


def _get_connector_auth_app_class() -> type[ConnectorAuthApp]:
    from vibe.cli.textual_ui.widgets.connector_auth_app import ConnectorAuthApp

    return ConnectorAuthApp


def _get_mcp_app_class() -> type[MCPApp]:
    from vibe.cli.textual_ui.widgets.mcp_app import MCPApp

    return MCPApp


def _get_plugins_app_class() -> type[PluginsApp]:
    from vibe.cli.textual_ui.widgets.plugins_app import PluginsApp

    return PluginsApp


def _get_mcp_oauth_app_class() -> type[MCPOAuthApp]:
    from vibe.cli.textual_ui.widgets.mcp_oauth_app import MCPOAuthApp

    return MCPOAuthApp


def _public_entry(event: AppServerEvent) -> PublicHistoryEntry | None:
    match event:
        case HistoryEntryAdded(entry=entry) | HistoryEntryUpdated(entry=entry):
            return entry
        case _:
            return None


def is_progress_event(event: AppServerEvent) -> bool:
    entry = _public_entry(event)
    return isinstance(
        entry, (PublicMessageEntry, PublicReasoningEntry, PublicEffectEntry)
    )


def _is_vscode_family_terminal() -> bool:
    return detect_terminal() in _VSCODE_FAMILY_TERMINALS


class BottomApp(StrEnum):
    """Bottom panel app types.

    Convention: Each value must match the widget class name with "App" suffix removed.
    E.g., ApprovalApp -> Approval, QuestionApp -> Question.
    This allows dynamic lookup via: BottomApp[type(widget).__name__.removesuffix("App")]
    """

    Approval = auto()
    ConnectorAuth = auto()
    Input = auto()
    LogLevelPicker = auto()
    MCP = auto()
    MCPOAuth = auto()
    ModelPicker = auto()
    Plugins = auto()
    ProxySetup = auto()
    Question = auto()
    ThemePicker = auto()
    ThinkingPicker = auto()
    Rewind = auto()
    VibeCodeProjectPicker = auto()
    VibeCodeProjectCreate = auto()
    SessionPicker = auto()
    Voice = auto()


# Smooth per-notch wheel scroll duration. Kept short so consecutive notches chain
# into continuous motion at the same average speed as an instant jump.
WHEEL_SCROLL_DURATION = 0.1


class ChatScroll(VerticalScroll):
    """Optimized scroll container that skips cascading style recalculations."""

    @property
    def is_at_bottom(self) -> bool:
        return self.scroll_target_y >= self.max_scroll_y

    _reanchor_pending: bool = False
    _scrolling_down: bool = False

    @property
    def _is_selecting(self) -> bool:
        try:
            return self.screen._selecting
        except NoScreen:
            return False

    def anchor(self, anchor: bool = True) -> None:
        if anchor and self._is_selecting:
            return
        super().anchor(anchor)

    def preserve_scroll_position(self) -> None:
        super().release_anchor()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        if self._is_selecting and new_value < old_value:
            self._anchor_released = True
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

    def _on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._smooth_wheel_scroll(event, self._scroll_down_for_pointer)

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._smooth_wheel_scroll(event, self._scroll_up_for_pointer)

    def _smooth_wheel_scroll(
        self, event: MouseScrollDown | MouseScrollUp, scroller: Callable[..., bool]
    ) -> None:
        # Leave ctrl/shift (horizontal) wheel to the base Widget handler, which
        # Textual's MRO dispatch still reaches because we don't prevent_default.
        if event.ctrl or event.shift or not self.allow_vertical_scroll:
            return
        # Cover the same per-notch distance as the default handler, but render it
        # as a smooth linear glide so motion passes through each line one by one
        # instead of jumping the full sensitivity in a single frame. prevent_default
        # breaks the MRO loop so the base handler's instant jump never runs on top.
        event.prevent_default()
        if scroller(animate=True, duration=WHEEL_SCROLL_DURATION, easing="linear"):
            event.stop()


PRUNE_LOW_MARK = 1000
PRUNE_HIGH_MARK = 1500
DOUBLE_ESC_DELAY = 0.2
MODE_SWITCH_SPINNER_DELAY = 0.5

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
    prompt_for_workspace_trust: bool = False
    startup_show_resume_picker: bool | None = None
    startup_prompt_for_workspace_trust: bool | None = None
    resume_session_id: str | None = None
    continue_latest: bool = False


@dataclass(slots=True)
class _PickerState:
    """Mutable state owned by the in-app session picker.

    ``previewing`` is True while a session's history is displayed in the
    transcript area instead of the live session's history. ``preview_session_id``
    is the session whose history is currently shown (None when not previewing).
    """

    previewing: bool = False
    preview_session_id: str | None = None

    def preview_is_current(self, session_id: str) -> bool:
        return self.preview_session_id == session_id

    def clear_preview(self) -> None:
        self.preview_session_id = None

    def exit_preview(self) -> bool:
        """Clear preview state; returns True if a preview was active."""
        was_previewing = self.previewing
        self.previewing = False
        self.preview_session_id = None
        return was_previewing


type AppServerStarter = Callable[[], Awaitable[AppServerSession]]
type AppServerSource = AppServerSession | AppServerStarter
type AppServerBootstrap = Callable[[], Awaitable[AppServerHost | AppServerSession]]


def _split_app_server_source(
    source: AppServerSource,
) -> tuple[AppServerSession | None, AppServerStarter | None]:
    if isinstance(source, AppServerSession):
        return source, None
    return None, source


class _IdleNarratorManager:
    @property
    def state(self) -> NarratorState:
        return NarratorState.IDLE

    @property
    def is_playing(self) -> bool:
        return False

    def on_turn_start(self, user_message: str) -> None: ...
    def on_user_message(self, message_id: str) -> None: ...
    def on_assistant_text(self, content: str) -> None: ...
    def on_turn_error(self, message: str) -> None: ...
    def on_turn_cancel(self) -> None: ...
    def on_turn_end(self) -> None: ...
    def cancel(self) -> None: ...
    def sync(self) -> None: ...
    def add_listener(self, listener: NarratorManagerListener) -> None: ...
    def remove_listener(self, listener: NarratorManagerListener) -> None: ...
    async def close(self) -> None: ...


class _IdleVoiceManager:
    @property
    def is_enabled(self) -> bool:
        return False

    @property
    def transcribe_state(self) -> TranscribeState:
        return TranscribeState.IDLE

    @property
    def peak(self) -> float:
        return 0.0

    def apply_enabled(self, enabled: bool) -> None: ...
    def start_recording(self, mode: RecordingMode = RecordingMode.STREAM) -> None: ...
    async def stop_recording(self) -> None: ...
    def cancel_recording(self) -> None: ...
    def add_listener(self, listener: VoiceManagerListener) -> None: ...
    def remove_listener(self, listener: VoiceManagerListener) -> None: ...
    async def close(self) -> None: ...


def _noop_voice_manager() -> VoiceManagerPort:
    return cast(VoiceManagerPort, _IdleVoiceManager())


def _noop_narrator_manager() -> NarratorManagerPort:
    return cast(NarratorManagerPort, _IdleNarratorManager())


_REJECT_HINT_BUSY = "wait for the current job to finish."
_REJECT_HINT_PAUSED = "clear the queue first or remove this input."

# Greeting interval in seconds (default: 24 hours)
_GREETING_INTERVAL_SECONDS = 24 * 60 * 60
_UNTRUSTED_CONFIG_WARNING_SECTION = "untrusted_config_warning"


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
    ]

    _greeting_message: GreetingMessage | None = None
    _tui_displayed_monotonic: float | None = None
    _startup_telemetry_sent: bool = False
    _main_ui_mounted: bool = False
    _mount_first: bool = False
    _initial_config_response: ConfigReadResponse | None = None

    def get_driver_class(self) -> type[Driver]:
        """Patch the platform driver to strip malformed terminal reports from input."""
        from vibe.cli.textual_ui.terminal_input_filter import patch_driver_parser

        driver_class = super().get_driver_class()
        patch_driver_parser()
        return driver_class

    def open_url(self, url: str, *, new_tab: bool = True) -> None:
        """Normalize URLs before opening so illegal characters (e.g. a literal
        backslash from model output) can't break the platform opener. Both link
        paths — tool-output links and clicked markdown links — funnel here.
        """
        super().open_url(normalize_url(url), new_tab=new_tab)

    def __init__(
        self,
        history_file: Path,
        app_server: AppServerSource,
        *,
        startup: StartupOptions | None = None,
        update_notifier: UpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        current_version: str = CORE_VERSION,
        terminal_notifier: NotificationPort | None = None,
        voice_manager: VoiceManagerPort | None = None,
        narrator_manager: NarratorManagerPort | None = None,
        vscode_extension_promo: VscodeExtensionPromo | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._app_server, self._start_app_server = _split_app_server_source(app_server)
        self._client_dependencies_ready = False
        self._prepare_lock = asyncio.Lock()
        self._provided_voice_manager = voice_manager
        self._provided_narrator_manager = narrator_manager
        self._voice_manager: VoiceManagerPort = _noop_voice_manager()
        self._narrator_manager: NarratorManagerPort = _noop_narrator_manager()
        self.commands: CommandRegistry = CommandRegistry()
        self._loop_commands: ScheduledLoopCommands
        self._terminal_notifier = terminal_notifier or TextualNotificationAdapter(
            self,
            get_enabled=lambda: (
                self._app_server is not None and self.config.enable_notifications
            ),
            default_title="Vibe",
        )
        self._interrupt_requested = False
        self._agent_task: asyncio.Task | None = None
        self._bash_task: asyncio.Task | None = None
        self._init_controllers()

        self._loading_widget: LoadingWidget | None = None
        self._active_callback: PublicCallbackEntry | None = None
        self._pending_callbacks: deque[PublicCallbackEntry] = deque()
        self._pending_local_question: asyncio.Future[UserQuestionResult] | None = None

        self.event_handler: EventHandler | None = None

        self._chat_input_container: ChatInputContainer | None = None
        self._current_bottom_app: BottomApp = BottomApp.Input
        self._vibe_code_project_picker = VibeCodeProjectPickerUiState()

        self.history_file = history_file

        self._tools_collapsed = True
        self._windowing = SessionWindowing(load_more_batch_size=LOAD_MORE_BATCH_SIZE)
        self._load_more = HistoryLoadMoreManager()
        self._history_widget_indices: WeakKeyDictionary[Widget, int] = (
            WeakKeyDictionary()
        )
        self._update_notifier = update_notifier
        self._update_cache_repository = update_cache_repository
        self._current_version = current_version
        self._vscode_extension_promo = vscode_extension_promo
        self._show_vscode_extension_promo = (
            vscode_extension_promo is not None
            and _is_vscode_family_terminal()
            and should_show_promo(vscode_extension_promo.initial_state)
        )
        self._configure_startup_options(startup)
        self._last_escape_time: float | None = None
        self._quit_manager = QuitManager(self)
        self._init_cached_widgets()
        if self._app_server is not None:
            self._initialize_client_dependencies()

    def _init_cached_widgets(self) -> None:
        self._banner: Banner | None = None
        self._whats_new_message: WhatsNewMessage | None = None
        self._cached_messages_area: Widget | None = None
        self._cached_chat: ChatScroll | None = None
        self._cached_loading_area: Widget | None = None
        self._debug_console: DebugConsole | None = None
        self._desired_agent: str | None = None
        self._agent_switch_active = False
        self._rewind_mode = False
        self._rewind_highlighted_widget: UserMessage | None = None
        self._queue_selected_widget: Widget | None = None
        self._fatal_init_error = False
        self._force_quit_task: asyncio.Task[None] | None = None

    def _mark_session_ready(self) -> None:
        self._session_ready.set()
        self._sync_terminal_title()

    def _on_session_title_changed(self, title: str) -> None:
        self._terminal_notifier.set_default_title(title)

    def _sync_terminal_title(self) -> None:
        if self._app_server is None:
            return
        # A blank title resets the tab to the default.
        title = self.app_server.resources.runtime.session_log.title
        self._terminal_notifier.set_default_title(title or "")

    @property
    def app_server(self) -> AppServerSession:
        if self._app_server is None:
            raise RuntimeError("App server has not been started")
        return self._app_server

    async def prepare(self) -> None:
        if self._client_dependencies_ready:
            return
        async with self._prepare_lock:
            if self._client_dependencies_ready:
                return
            if self._app_server is None:
                if self._start_app_server is None:
                    raise RuntimeError("App server starter is unavailable")
                self._app_server = await self._start_app_server()
            self._initialize_client_dependencies()

    def _initialize_client_dependencies(self) -> None:
        if self._client_dependencies_ready:
            return
        self._voice_manager = (
            self._provided_voice_manager or self._make_default_voice_manager()
        )
        self._narrator_manager = (
            self._provided_narrator_manager or self._make_default_narrator_manager()
        )
        self.commands = self._build_command_registry()
        self._loop_commands = ScheduledLoopCommands(
            self.app_server.resources.loops,
            tools_collapsed=lambda: self._tools_collapsed,
        )
        self._teleport_on_start = (
            self._teleport_on_start
            and self.app_server.resources.config.current.vibe_code_enabled
        )
        self._client_dependencies_ready = True

    def _configure_startup_options(self, startup: StartupOptions | None) -> None:
        opts = startup or StartupOptions()
        self._initial_prompt = opts.initial_prompt
        self._teleport_on_start = opts.teleport_on_start
        self._startup_teleport_on_start = opts.teleport_on_start
        self._show_resume_picker = opts.show_resume_picker
        self._startup_show_resume_picker = (
            opts.startup_show_resume_picker
            if opts.startup_show_resume_picker is not None
            else opts.show_resume_picker
        )
        self._is_resuming_session = opts.is_resuming_session
        self._resume_session_id = opts.resume_session_id
        self._continue_latest = opts.continue_latest
        self._startup_prompt_for_workspace_trust = (
            opts.startup_prompt_for_workspace_trust
            if opts.startup_prompt_for_workspace_trust is not None
            else opts.prompt_for_workspace_trust
        )
        self._startup_prompt_processed = False
        self._startup_command_availability_ready = asyncio.Event()
        self._initial_history_loaded = asyncio.Event()
        # Gates the turn path until the session is bound: mount-first renders the
        # input box before app_server exists, so a submit awaits this instead of
        # crashing on the unbound property.
        self._session_ready = asyncio.Event()
        # Set while the chat input (BottomApp.Input) is the active bottom app.
        # Cleared when a picker/modal mounts via _switch_from_input; the queue
        # drain awaits it after a queued command so a picker command (e.g. /mcp,
        # /resume) blocks drainage until the user dismisses it.
        self._input_app_ready = asyncio.Event()
        self._input_app_ready.set()
        self._picker = _PickerState()
        # Guards against double-display of MCP/startup notices across the
        # readiness-watch and finish-resume-notices race; unrelated to picker preview.
        self._post_init_notices_shown: bool = False
        self._custom_tools_deprecation_message: CustomToolsDeprecationMessage | None = (
            None
        )
        self._custom_tools_deprecation_message_lock = asyncio.Lock()

    @property
    def config(self) -> ConfigView:
        return self.app_server.resources.config.current

    @property
    def _input_queue(self) -> MessageQueue:
        return self._queue.queue

    def _build_queue_ports(self) -> QueuePorts:
        return QueuePorts(
            mount_and_scroll=self._mount_and_scroll,
            agent_running=self._agent_job_active,
            bash_task=lambda: self._bash_task,
            active_model=self._active_model_or_none,
            remove_loading_widget=self._remove_loading_widget,
            set_loading_queue_count=self._set_loading_queue_count,
            inject_queued_prompt=self._inject_queued_prompt,
            start_agent_turn=self._start_queued_agent_turn,
            await_agent_turn=self._await_agent_turn,
            run_bash=self._start_queued_bash,
            run_command=self._run_queued_command,
            await_input_app=self._await_input_app,
            maybe_show_feedback_bar=self._maybe_show_feedback_bar,
            send_skill_telemetry=self._send_skill_telemetry,
        )

    def _init_controllers(self) -> None:
        self._queue = QueueController(self._build_queue_ports())
        self._side_channel = SideChannelController(
            SideChannelPorts(invoke_command=self._invoke_resolved_command)
        )
        self._pending_theme: str | None = None
        self._pending_model: str | None = None
        self._pending_thinking: ThinkingLevel | None = None

    @property
    def _effective_theme(self) -> str:
        return self._pending_theme or self.config.theme

    @property
    def _effective_model_alias(self) -> str:
        return self._pending_model or self.config.active_model.alias

    @property
    def _effective_thinking(self) -> ThinkingLevel:
        return self._pending_thinking or self.config.active_model.thinking

    def _active_model_or_none(self) -> ModelConfigView | None:
        return self.config.active_model

    def _model_pending(self) -> bool:
        return self.config.awaiting_experiment_model

    def _agent_job_active(self) -> bool:
        if self._app_server is not None and self._app_server.turn_active:
            return True
        return self._agent_task is not None and not self._agent_task.done()

    def _set_loading_queue_count(self, count: int) -> None:
        if self._loading_widget is not None:
            self._loading_widget.set_queue_count(count)

    async def _inject_queued_prompt(
        self,
        content: str,
        *,
        images: list[ImageAttachment] | None = None,
        client_message_id: str | None = None,
        mention_stats: MentionStats | None = None,
    ) -> None:
        events = await self.app_server.inject_user_context(
            content,
            as_message=True,
            inject_invoked_skill=True,
            images=images,
            client_message_id=client_message_id,
            mention_stats=mention_stats,
        )
        for event in events:
            self._track_narrator_event(event)
            if self.event_handler:
                await self.event_handler.handle_event(
                    event, loading_widget=self._loading_widget
                )

    async def _maybe_show_feedback_bar(self) -> None:
        if await self.app_server.resources.feedback.should_show(
            pending_user_messages=1
        ):
            self._feedback_bar.show()
            await self.app_server.resources.feedback.record("asked")

    def _start_queued_agent_turn(
        self,
        content: str,
        *,
        prepared_prompt: PreparedPrompt | None = None,
        client_message_id: str | None = None,
    ) -> asyncio.Task:
        self._agent_task = asyncio.create_task(
            self._handle_turn(
                content,
                prepared_prompt=prepared_prompt,
                client_message_id=client_message_id,
            )
        )
        return self._agent_task

    async def _await_agent_turn(self) -> None:
        agent_task = self._agent_task
        if agent_task is None:
            return
        await agent_task

    async def _await_input_app(self) -> None:
        # Blocks while a picker/modal opened by a queued command is still on
        # screen; returns immediately when the chat input is the active bottom
        # app. See QueuePorts.await_input_app.
        await self._input_app_ready.wait()

    def _start_queued_bash(self, command: str) -> asyncio.Task:
        self._bash_task = asyncio.create_task(
            self._handle_bash_command(command, start_drain_on_finish=False)
        )
        return self._bash_task

    async def _run_queued_command(
        self, content: str, payload: Callable[[], Awaitable[None]] | None
    ) -> None:
        try:
            if payload is None:
                await self._dispatch_idle_input(content)
            else:
                await payload()
        except Exception as exc:
            logger.warning("Queued command failed: %s", content, exc_info=exc)
            await self._mount_and_scroll(
                ErrorMessage(f"Failed to apply: {content} — {exc}")
            )

    def _build_command_registry(self) -> CommandRegistry:
        return CommandRegistry(context=self._command_context())

    def _command_context(self) -> CommandContext:
        return CommandContext(
            vibe_code_enabled=self.app_server.resources.config.current.vibe_code_enabled,
            plugins_enabled=self.app_server.resources.plugins.supported,
        )

    def _refresh_command_registry(self) -> None:
        self.commands.refresh(self._command_context())

    async def on_load(self) -> None:
        if (
            self._mount_first
            and self._app_server is None
            and self._start_app_server is not None
        ):
            return
        await self.prepare()

    async def _refresh_config_from_disk(self) -> None:
        await self.app_server.resources.config.reload(reload_runtime=False)
        self._narrator_manager.sync()
        self._refresh_command_registry()

    def get_default_screen(self) -> Screen:
        return WordSelectScreen(id="_default")

    def compose(self) -> ComposeResult:
        yield from self._compose_main_ui()

    def _compose_main_ui(self) -> ComposeResult:
        has_session = self._app_server is not None
        init = self._initial_config_response
        with ChatScroll(id="chat"):
            self._banner = Banner(
                config=self.config if has_session else (init.config if init else None),
                skills_count=(
                    self.app_server.resources.runtime.custom_skills_count
                    if has_session
                    else (init.skills_count if init else 0)
                ),
                mcp=self.app_server.resources.runtime.mcp if has_session else None,
                mcp_servers_total=(
                    init.mcp_servers_total if init and not has_session else 0
                ),
                mcp_servers_enabled=(
                    init.mcp_servers_enabled if init and not has_session else 0
                ),
                connectors_connected=(
                    self.app_server.resources.runtime.connectors.connected
                    if has_session
                    else 0
                ),
                connectors_total=(
                    self.app_server.resources.runtime.connectors.total
                    if has_session
                    else None
                ),
                hooks_count=(
                    self.app_server.resources.runtime.hooks_count
                    if has_session
                    else (init.hooks_count if init else 0)
                ),
                model_pending=self._model_pending() if has_session else False,
            )
            yield self._banner
            yield VerticalGroup(id="messages")

        with Horizontal(id="loading-area"):
            yield NarratorStatus(self._narrator_manager)
            yield Static(id="loading-area-content")
            self._inline_notice = InlineNotice(id="inline-notice")
            yield self._inline_notice
            yield FeedbackBar()

        with Static(id="bottom-app-container"):
            yield ChatInputContainer(
                history_file=self.history_file,
                command_registry=self.commands,
                id="input-container",
                safety=(
                    self.app_server.resources.agents.active.safety
                    if has_session
                    else AgentSafety.NEUTRAL
                ),
                agent_name=(
                    self.app_server.resources.agents.active.display_name.lower()
                    if has_session
                    else ""
                ),
                skill_entries_getter=self._get_skill_entries,
                file_watcher_for_autocomplete_getter=self._is_file_watcher_enabled,
                voice_manager=self._voice_manager,
                queue_edit_active_getter=self._is_queue_edit_active,
                queue_items_getter=self._queue.queue_items,
                queue_selected_index_getter=self._queue_selected_queue_index,
            )

        with Horizontal(id="bottom-bar"):
            yield PathDisplay(self.app_server.cwd if has_session else str(Path.cwd()))
            yield NoMarkupStatic(process_id_label(), id="process-title")
            yield NoMarkupStatic(id="spacer")
            context_progress = ContextProgress()
            if has_session:
                stats = self.app_server.resources.runtime.stats
                context_progress.tokens = TokenState(
                    max_tokens=self.app_server.resources.runtime.context_window,
                    current_tokens=stats.context_tokens,
                )
            yield context_progress

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
        if self._app_server is None and self._start_app_server is not None:
            init = self._initial_config_response
            initial_theme = init.config.theme if init is not None else FALLBACK_THEME
            await self._apply_theme(initial_theme)
            self.call_after_refresh(self._record_tui_displayed)
            self.run_worker(self._bootstrap_session(), exclusive=False)
            return
        await self._mount_after_session_ready()

    async def _mount_after_session_ready(self) -> None:
        await self._apply_theme(self.config.theme)
        self.app_server.resources.config.subscribe(self._on_config_changed)
        set_config_log_level(self.config.log_level)
        self._terminal_notifier.restore()
        self._feedback_bar = self.query_one(FeedbackBar)
        if self._chat_input_container is not None:
            self._chat_input_container.replace_command_registry(self.commands)
            self._refresh_command_registry()
        self._refresh_banner()
        self._refresh_context_progress()
        # Ready now unless a resume/continue/picker flow is pending — those mark
        # ready at their own return-to-input points to avoid dispatching against
        # a half-rebound session.
        if not (
            self._show_resume_picker
            or self._resume_session_id is not None
            or self._continue_latest
        ):
            self._mark_session_ready()
        self.run_worker(self._complete_mount(), exclusive=False)

    async def _bootstrap_session(self) -> None:
        if self._start_app_server is None:
            raise RuntimeError("App server starter is unavailable")
        try:
            session = await self._start_app_server()
            self._app_server = session
            self._initialize_client_dependencies()
        except Exception as e:
            self._app_server = None
            await self._show_bootstrap_error(e)
            return
        await self._transition_to_main_ui()

    async def _transition_to_main_ui(self) -> None:
        # _main_ui_mounted now means "session-ready refresh has run" — compose()
        # already mounted the UI. Kept the name to avoid touching call sites.
        if self._main_ui_mounted:
            return
        self._main_ui_mounted = True
        await self._mount_after_session_ready()

    async def _show_bootstrap_error(self, error: Exception) -> None:
        await self._mount_and_scroll(Static(f"Failed to start session: {error}"))
        try:
            container = self.query_one(ChatInputContainer)
        except Exception:
            container = None
        if container is not None:
            container.disabled = True
            container.display = False
        self._fatal_init_error = True

    def _on_config_changed(self, config: ConfigView) -> None:
        resolved_theme = resolve_theme(resolve_theme_name(config.theme))
        if resolved_theme != self.theme:
            self.run_worker(self._apply_theme(config.theme))
        set_config_log_level(config.log_level)
        self._refresh_banner()

    async def _complete_mount(self) -> None:
        self.event_handler = EventHandler(
            mount_callback=self._mount_and_scroll,
            get_tools_collapsed=lambda: self._tools_collapsed,
            on_profile_changed=self._on_profile_changed,
            get_show_thinking=lambda: self.config.show_thinking_nodes,
            on_context_cleared=self._on_context_cleared,
            on_session_title_changed=self._on_session_title_changed,
        )

        self._chat_input_container = self.query_one(ChatInputContainer)
        self._chat_input_container.replace_command_registry(self.commands)
        self._refresh_command_registry()
        # Compose binds idle noop voice/narrator managers on the cold mount-first
        # path; the real managers were created in _initialize_client_dependencies.
        # Re-bind them into the already-mounted widgets so voice input (Ctrl+R)
        # and narrator status actually drive the real managers.
        self._chat_input_container.replace_voice_manager(self._voice_manager)
        self.query_one(NarratorStatus).replace_narrator_manager(self._narrator_manager)

        self._refresh_profile_widgets()

        chat_input_container = self.query_one(ChatInputContainer)
        chat_input_container.focus_input()
        await self._show_dangerous_directory_warning()
        self.run_worker(self._deferred_resume_and_start(), exclusive=False)
        # Non-critical: runs off the mount path so its app-server round-trip
        # never delays history resume into a fast-exit teardown window.
        self.run_worker(self._show_untrusted_config_warning(), exclusive=False)

        self.call_after_refresh(self._start_post_ready_startup)
        self.call_after_refresh(self._record_tui_displayed)
        self._show_config_issues()

        self.run_worker(self._watch_init_completion(), exclusive=False)

        if self._show_resume_picker:
            self.run_worker(self._show_session_picker(), exclusive=False)
        elif self._resume_session_id is not None or self._continue_latest:
            self.run_worker(self._auto_resume_on_startup(), exclusive=False)
        else:
            # Fresh session: no later set-point, so the input is ready now.
            self._mark_session_ready()

        gc.collect()
        gc.freeze()

    def _update_context_progress(self, event: StatsUpdated) -> None:
        context_progress = self.query_one(ContextProgress)
        context_progress.tokens = TokenState(
            max_tokens=event.params.context_window,
            current_tokens=event.params.stats.context_tokens,
        )

    def _start_post_ready_startup(self) -> None:
        self.run_worker(self._complete_post_ready_startup(), exclusive=False)

    def _record_tui_displayed(self) -> None:
        if self._tui_displayed_monotonic is None:
            self._tui_displayed_monotonic = time.monotonic()

    async def _probe_plugin_catalog(self) -> None:
        """Ask once whether this backend resolves plugins, and gate on the answer.

        There is no flag to read: ``--experimental-harness`` is a CLI argument
        the app server never sends back, and the same TUI talks to backends
        that were never given one.
        """
        try:
            await self.app_server.resources.plugins.read()
        except Exception as exc:
            logger.debug("Plugin catalogue probe failed: %s", exc)

    async def _complete_post_ready_startup(self) -> None:
        try:
            await asyncio.gather(
                self._refresh_account(),
                self._refresh_identity(),
                self._probe_plugin_catalog(),
            )
        finally:
            self._refresh_command_registry()
            self._startup_command_availability_ready.set()
        await self._check_and_show_whats_new()
        if (
            not self._show_resume_picker
            and self._resume_session_id is None
            and not self._continue_latest
        ):
            await self._show_custom_tools_deprecation_warning_after_initial_history()
        await self._show_greeting_message()
        self._schedule_update_notification()
        self._refresh_banner()
        if self._show_resume_picker:
            return
        if self._resume_session_id is not None or self._continue_latest:
            return
        self._process_startup_prompt()

    async def _process_startup_prompt_when_available(self) -> None:
        await self._startup_command_availability_ready.wait()
        self._process_startup_prompt()

    def _process_startup_prompt(self) -> None:
        if self._startup_prompt_processed:
            return
        self._startup_prompt_processed = True
        if self._initial_prompt or self._teleport_on_start:
            self._process_initial_prompt()

    def _show_config_issues(self) -> None:
        for issue in self.app_server.resources.runtime.issues:
            self.notify(
                f"{issue.file}\n{issue.message}",
                severity="warning",
                markup=False,
                timeout=10,
            )
        for warning in self.app_server.resources.config.current.validation_warnings:
            self.notify(warning, severity="warning", markup=False, timeout=10)

    async def _watch_init_completion(self) -> None:
        """Show 'Initializing' loading indicator until background init finishes."""
        init_widget = None
        try:
            if not self.app_server.resources.runtime.ready:
                await self._ensure_loading_widget("Initializing", show_hint=False)
                init_widget = self._loading_widget
            await self.app_server.resources.runtime.wait_until_ready()
            await self._show_post_init_notices_once()
        except Exception as e:
            if isinstance(e, AppServerResponseError) and e.error.code in {
                ProtocolErrorCode.CONFLICT,
                ProtocolErrorCode.NOT_FOUND,
            }:
                # The fresh session's readiness watch is superseded by a resume:
                # CONFLICT while the resume holds the lifecycle reservation, or
                # NOT_FOUND once the rebind re-attaches the root to the resumed id.
                # Neither is a real init failure — the resume owns readiness/UI.
                logger.info("Init readiness watch superseded by a session resume")
                return
            logger.exception("Background initialization failed")
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
                self.query_one(_get_mcp_app_class()).refresh_index()
            except Exception:
                pass

    async def _show_post_init_notices_once(self) -> None:
        # Shown by whichever of the readiness watch or a resume gets there first: a
        # resume supersedes the watch, but reuses the same MCP pool, so the notices
        # still apply.
        if self._post_init_notices_shown:
            return
        self._post_init_notices_shown = True
        try:
            self._send_startup_telemetry_once()
        except Exception:
            logger.exception("Failed to send startup telemetry")
        self._show_mcp_discovery_failures()
        await self._show_mcp_auth_required_notice()

    def _is_cold_start(self) -> bool | None:
        """True if this process paid first-run startup cost (cold), False if it
        resumed a warm cache (warm), None when the signal is unavailable.
        """
        if sys.dont_write_bytecode:
            return None
        from importlib.util import cache_from_source

        pyc = Path(cache_from_source(__file__))
        try:
            return pyc.stat().st_mtime >= PROCESS_START_WALLCLOCK
        except OSError:
            return None

    def _send_startup_telemetry_once(self) -> None:
        if self._startup_telemetry_sent:
            return

        self._startup_telemetry_sent = True
        start = PROCESS_START_MONOTONIC
        now: float = time.monotonic()
        tui = self._tui_displayed_monotonic
        session_init_ms = self.app_server.resources.runtime.session_init_duration_ms
        self.app_server.resources.telemetry.record(
            "vibe.startup",
            {
                "first_frame_duration_ms": (
                    int((tui - start) * 1000) if tui is not None and start else None
                ),
                "agent_ready_duration_ms": (
                    int((now - start) * 1000) if start else None
                ),
                "session_init_duration_ms": session_init_ms,
                "has_initial_prompt": bool(self._initial_prompt),
                "teleport_on_start": self._startup_teleport_on_start,
                "show_resume_picker": self._startup_show_resume_picker,
                "is_resuming_session": self._is_resuming_session,
                "prompt_for_workspace_trust": self._startup_prompt_for_workspace_trust,
                "is_cold_start": self._is_cold_start(),  # None for frozen binaries; safe to ignore for python dist
            },
        )

    def _show_mcp_discovery_failures(self) -> None:
        for server_name, error in sorted(
            self.app_server.resources.runtime.mcp.discovery_errors.items()
        ):
            self.notify(
                f"MCP server '{server_name}' failed to connect: {error}",
                severity="warning",
                markup=False,
                timeout=10,
            )

    async def _show_mcp_auth_required_notice(self) -> None:
        """Show a notice if any enabled MCP servers require OAuth authentication."""
        aliases = self.app_server.resources.runtime.mcp.needs_auth
        if not aliases:
            return
        command = f"/mcp login {aliases[0]}"
        if len(aliases) > 1:
            detail = ", ".join(aliases)
            message = (
                "MCP servers need OAuth authentication: "
                f"{detail}. Run `{command}` to start with {aliases[0]!r}."
            )
        else:
            message = (
                f"MCP server {aliases[0]!r} needs OAuth authentication. "
                f"Run `{command}` to authenticate."
            )
        await self._mount_and_scroll(UserCommandMessage(message))

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
        return (
            self._app_server is not None and self.config.file_watcher_for_autocomplete
        )

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
            if not await self._try_side_channel_command(value, input_widget):
                if not await self._handle_paused_submit(value):
                    self._restore_input_if_empty(input_widget, value)
            return

        if self._is_busy():
            if not await self._try_side_channel_command(value, input_widget):
                if not await self._handle_queue_submit(
                    value, reject_hint=_REJECT_HINT_BUSY
                ):
                    self._restore_input_if_empty(input_widget, value)
            return

        await self._dispatch_idle_input(value)

    async def on_chat_input_container_queue_edit_submitted(
        self, event: ChatInputContainer.QueueEditSubmitted
    ) -> None:
        event.stop()
        # Resolve the edited item by widget identity: the drain is FIFO, so
        # older items being consumed shifts the edited item to a lower index
        # while it is still alive — a cached index would either miss it (out
        # of range) or hit the wrong slot.
        current_index = self._queue_selected_queue_index()
        if current_index is None:
            # Consumed by the drain despite the body's guard (race): copy on write.
            await self._enqueue_edited_copy(event.value, event.kind)
            return
        widget = self._queue_selected_widget
        if isinstance(widget, BashOutputMessage):
            await self._queue.update_item(
                current_index, self._bash_command(event.value)
            )
            return
        prepared = await self._prepare_prompt_or_abort(event.value)
        if prepared is None:
            return
        # Re-resolve the index of the item captured at submit time (by widget
        # identity), not the live highlight: the body returns to selection
        # mode before this await, so Up/Down/Esc can move ``_queue_selected_widget``
        # away from the edited item. Tracking the captured widget avoids writing
        # the edit onto a different item; if the drain consumed it, copy-on-write.
        current_index = self._queue_index_of_widget(widget)
        if current_index is None:
            await self._enqueue_edited_copy(event.value, event.kind)
            return
        await self._queue.update_item(
            current_index, event.value, prepared_prompt=prepared
        )

    @staticmethod
    def _bash_command(value: str) -> str:
        """Strip the leading ``!`` mode prefix so the queue stores the raw command
        the shell should run (not the display form the textarea holds it in).
        """
        return value[1:] if value.startswith("!") else value

    async def _enqueue_edited_copy(self, value: str, kind: QueuedItemKind) -> None:
        """Copy-on-write recovery for an edit whose target was consumed: queue
        the edited text as a fresh item, preserving its kind (bash stays bash so
        shell text is never sent to the model as a prompt).
        """
        if kind == QueuedItemKind.BASH:
            await self._queue.enqueue_bash(
                self._bash_command(value), self.app_server.cwd
            )
            return
        await self._enqueue_prompt_with_resources(value)

    async def on_chat_input_container_queue_edit_consumed(
        self, event: ChatInputContainer.QueueEditConsumed
    ) -> None:
        event.stop()
        await self._enqueue_edited_copy(event.value, event.kind)

    async def on_chat_input_container_queue_remove_requested(
        self, event: ChatInputContainer.QueueRemoveRequested
    ) -> None:
        event.stop()
        # Resolve the target by widget identity, like the edit path: the drain
        # is FIFO, so a cached index goes stale if an older item is popped
        # between the body's resync and this handler, deleting the wrong
        # (newer) item. ``_queue_selected_queue_index`` returns None once the
        # highlighted widget is gone (already consumed) — nothing left to remove.
        current_index = self._queue_selected_queue_index()
        if current_index is None:
            return
        self._queue_selected_widget = None
        await self._queue.pop_at(current_index)

    async def on_chat_input_container_queue_selection_scroll(
        self, event: ChatInputContainer.QueueSelectionScroll
    ) -> None:
        event.stop()
        widgets = self._queue.widgets
        if event.queue_index >= len(widgets):
            return
        widget = widgets[event.queue_index]

        if self._queue_selected_widget is not None:
            self._queue_selected_widget.remove_class("queue-selected")
        widget.add_class("queue-selected")
        self._queue_selected_widget = widget

        chat = self._messages_area
        self.call_after_refresh(chat.scroll_to_widget, widget, animate=False, top=True)

    def _queue_selected_queue_index(self) -> int | None:
        """Live queue index of the highlighted widget, or None if consumed.

        Used by the body to detect drain consumption by widget identity — the
        cached queue_index is unreliable once older items shift on FIFO drain.
        """
        return self._queue_index_of_widget(self._queue_selected_widget)

    def _queue_index_of_widget(self, widget: object | None) -> int | None:
        """Live queue index of a specific widget, or None if it's been removed.

        Resolves by identity (``is``) rather than the current highlight, so a
        queued edit can re-resolve the exact item it captured at submit time
        even if the user navigates the highlight during the prepare await.
        """
        if widget is None:
            return None
        for i, w in enumerate(self._queue.widgets):
            if w is widget:
                return i
        return None

    def _clear_queue_selection(self) -> None:
        if self._queue_selected_widget is not None:
            self._queue_selected_widget.remove_class("queue-selected")
            self._queue_selected_widget = None

    async def on_chat_input_container_queue_mode_exited(
        self, _event: ChatInputContainer.QueueModeExited
    ) -> None:
        self._clear_queue_selection()

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

    async def _try_side_channel_command(
        self, value: str, input_widget: ChatInputContainer
    ) -> bool:
        resolved = self.commands.parse_command(value)
        if resolved is None:
            return False
        cmd_name, command, cmd_args = resolved
        if not command.side_channel:
            return False
        display = self._command_display(value, cmd_name)
        if not self._side_channel.enqueue(cmd_name, command, cmd_args, display):
            self._warn_not_queueable(
                "A slash command is already running — wait for it to finish."
            )
            self._restore_input_if_empty(input_widget, value)
        return True

    async def _dispatch_idle_input(self, value: str) -> None:
        # Mount-first renders an interactive input before the session is bound.
        # Every input kind eventually touches session-owned state, including
        # classification of skills, so hold dispatch at this common boundary.
        await self._session_ready.wait()
        match classify(
            value, commands=self.commands, resolve_skill=self._resolve_skill
        ):
            case Teleport(target=target):
                await self._handle_teleport_command(target)
            case SlashCommand():
                await self._handle_command(value)
            case Skill(command=command, name=name):
                self._send_skill_telemetry(name)
                await self._handle_user_message(command)
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
        match classify(
            value, commands=self.commands, resolve_skill=self._resolve_skill
        ):
            case Teleport():
                self._warn_not_queueable(f"Teleport cannot be queued — {reject_hint}")
                return False
            case SlashCommand():
                resolved = self.commands.parse_command(value)
                flushes = bool(resolved is not None and resolved[1].flushes_pending)
                await self._queue.enqueue_command(value, flushes_pending=flushes)
            case Skill(command=command, name=name):
                return await self._enqueue_prompt_with_resources(
                    command, skill_name=name
                )
            case Bash(command=command):
                await self._queue.enqueue_bash(command, self.app_server.cwd)
            case EmptyBash():
                await self._empty_bash_error()
            case Prompt(text=text):
                return await self._enqueue_prompt_with_resources(text)
        return True

    async def _enqueue_prompt_with_resources(
        self, content: str, *, skill_name: str | None = None
    ) -> bool:
        prepared = await self._prepare_prompt_or_abort(content)
        if prepared is None:
            return False
        await self._queue.enqueue_prompt(
            content, skill_name=skill_name, prepared_prompt=prepared
        )
        return True

    def _is_busy(self) -> bool:
        if self._agent_job_active():
            return True
        if self._bash_task is not None and not self._bash_task.done():
            return True
        if self._queue.draining:
            return True
        return False

    def _is_queue_edit_active(self) -> bool:
        if not self._queue:
            return False
        if self._queue.draining:
            return False
        return self._agent_job_active() or (
            self._bash_task is not None and not self._bash_task.done()
        )

    async def on_approval_app_approval_granted(
        self, message: ApprovalApp.ApprovalGranted
    ) -> None:
        await self._respond_to_approval(ApprovalDecisionType.APPROVE)

    async def on_approval_app_approval_granted_always_tool(
        self, message: ApprovalApp.ApprovalGrantedAlwaysTool
    ) -> None:
        await self._respond_to_approval(ApprovalDecisionType.APPROVE_FOR_SESSION)

    async def on_approval_app_approval_granted_always_permanent(
        self, message: ApprovalApp.ApprovalGrantedAlwaysPermanent
    ) -> None:
        await self._respond_to_approval(ApprovalDecisionType.APPROVE_PERMANENTLY)

    async def on_approval_app_approval_rejected(
        self, message: ApprovalApp.ApprovalRejected
    ) -> None:
        await self._respond_to_approval(ApprovalDecisionType.DENY)

        if self._loading_widget and self._loading_widget.parent:
            await self._remove_loading_widget()

    async def on_question_app_answered(self, message: QuestionApp.Answered) -> None:
        result = UserQuestionResult(answers=message.answers, cancelled=False)
        if self._active_callback is not None:
            await self._respond_to_active_callback(
                UserInputCallbackOutput(result=result)
            )
            return
        if self._pending_local_question and not self._pending_local_question.done():
            self._pending_local_question.set_result(result)

    async def on_question_app_cancelled(self, message: QuestionApp.Cancelled) -> None:
        result = UserQuestionResult(answers=[], cancelled=True)
        if self._active_callback is not None:
            await self._respond_to_active_callback(
                UserInputCallbackOutput(result=result)
            )
            return
        if self._pending_local_question and not self._pending_local_question.done():
            self._pending_local_question.set_result(result)

    def on_chat_text_area_feedback_key_pressed(
        self, message: ChatTextArea.FeedbackKeyPressed
    ) -> None:
        self._feedback_bar.handle_feedback_key(message.rating)

    def on_chat_text_area_snooze_key_pressed(
        self, message: ChatTextArea.SnoozeKeyPressed
    ) -> None:
        self._feedback_bar.handle_snooze_key()

    def on_chat_text_area_non_feedback_key_pressed(
        self, message: ChatTextArea.NonFeedbackKeyPressed
    ) -> None:
        self._feedback_bar.hide()

    async def on_feedback_bar_feedback_given(
        self, message: FeedbackBar.FeedbackGiven
    ) -> None:
        self.app_server.resources.telemetry.record(
            "vibe.user_rating_feedback",
            {
                "rating": message.rating,
                "version": CORE_VERSION,
                "model": self.config.active_model.alias,
            },
            correlate_last_request=True,
        )
        await self.app_server.resources.feedback.record("given")

    async def on_feedback_bar_snooze_key_pressed(
        self, message: FeedbackBar.SnoozeKeyPressed
    ) -> None:
        await self.app_server.resources.feedback.record("snoozed")

    async def _remove_loading_widget(self) -> None:
        if self._loading_widget and self._loading_widget.parent:
            await self._loading_widget.remove()
            self._loading_widget = None

    async def _prepare_prompt_or_abort(self, message: str) -> PreparedPrompt | None:
        # Block until the session is bound before touching app_server (instant
        # once ready).
        await self._session_ready.wait()
        try:
            return await self.app_server.resources.workspace.prepare_prompt(message)
        except AppServerResponseError as exc:
            await self._remove_loading_widget()
            await self._mount_and_scroll(
                ErrorMessage(str(exc), collapsed=self._tools_collapsed)
            )
            return None

    def on_chat_text_area_clipboard_image_pasted(
        self, message: ChatTextArea.ClipboardImagePasted
    ) -> None:
        self.run_worker(
            handle_clipboard_image_paste(
                self, notify_when_empty=message.notify_when_empty
            ),
            exclusive=False,
        )

    async def _paste_clipboard_image_command(self, **_kwargs: Any) -> None:
        await handle_clipboard_image_paste(self, notify_when_empty=True)

    async def _persist_config_changes(
        self, changes: Mapping[str, str | bool | None]
    ) -> None:
        await self.app_server.resources.config.update(changes)

    async def _persist_proxy(self, changes: dict[str, str | None]) -> None:
        await self.app_server.resources.config.update_proxy(changes)
        await self._mount_and_scroll(
            UserCommandMessage(
                "Proxy settings saved. Restart the CLI for changes to take effect."
            )
        )

    async def _persist_voice_settings(
        self,
        changes: dict[str, str | bool],
        previous_voice_enabled: bool,
        audio_error: str | None,
    ) -> None:
        await self._persist_config_changes(changes)
        voice_enabled = self.config.voice_mode_enabled
        if voice_enabled != previous_voice_enabled:
            try:
                self._voice_manager.apply_enabled(voice_enabled)
            except Exception as exc:
                logger.warning("Failed to apply voice mode locally", exc_info=exc)
                audio_error = str(exc)
            self.app_server.resources.telemetry.record(
                "vibe.voice_mode_toggled", {"enabled": voice_enabled}
            )
            message = (
                "Voice mode enabled. Press **Ctrl+R** to start recording."
                if voice_enabled
                else "Voice mode disabled."
            )
            await self._mount_and_scroll(UserCommandMessage(message))
        self._narrator_manager.sync()
        self._refresh_command_registry()
        if audio_error:
            self.notify(
                f"Audio setting saved, but audio is unavailable: {audio_error}",
                severity="warning",
                timeout=15,
                markup=False,
            )

    async def _remove_config_field(self, field: str) -> None:
        response = await self.app_server.resources.config.write(
            [ConfigWriteOpWire(op="remove", path=f"/{field}")],
            reason="app-server config update",
        )
        if response.rejected:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="Invalid configuration edit",
                )
            )
        if response.failures:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR,
                    message="; ".join(response.failures),
                )
            )

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

    async def on_voice_app_config_closed(self, message: VoiceApp.ConfigClosed) -> None:
        await self._handle_voice_settings_closed(message.changes)
        await self._switch_to_input_app()

    def _apply_thinking_visibility(self) -> None:
        show = self.config.show_thinking_nodes
        for node in self._messages_area.query(ReasoningMessage):
            node.display = show
        for tc in self._messages_area.query(ToolCallMessage):
            tc.recompute_gap()
        for tr in self._messages_area.query(ToolResultMessage):
            tr.recompute_gap()
        # A group holding only a now-hidden thinking node must collapse so it
        # doesn't leave a stray blank line.
        for group in self._messages_area.query(ToolGroup):
            group.sync_visibility()

    async def _handle_voice_settings_closed(
        self, changes: dict[str, str | bool]
    ) -> None:
        if not changes:
            await self._mount_and_scroll(
                UserCommandMessage("Voice settings closed (no changes saved).")
            )
            return

        previous_voice_enabled = self.config.voice_mode_enabled
        audio_error = (
            check_audio_available()
            if changes.get("voice_mode_enabled") is True
            or changes.get("narrator_enabled") is True
            else None
        )
        await self._queue.enqueue_command(
            "voice settings",
            command_payload=partial(
                self._persist_voice_settings,
                changes,
                previous_voice_enabled,
                audio_error,
            ),
        )

    async def on_model_picker_app_model_selected(
        self, message: ModelPickerApp.ModelSelected
    ) -> None:
        if await self._is_active_model_enforced():
            self.notify(
                "'active_model' is enforced by your administrator. "
                "Contact your admin to change it.",
                severity="warning",
                markup=False,
            )
            await self._switch_to_input_app()
            return
        self._pending_model = message.alias
        await self._queue.enqueue_command(
            f"model {message.alias}",
            command_payload=partial(self._persist_model, message.alias),
            on_discard=partial(self._discard_model),
        )
        await self._switch_to_input_app()

    async def _persist_model(self, alias: str) -> None:
        try:
            await self.app_server.resources.config.update({"active_model": alias})
            await self._reload_config()
        finally:
            self._pending_model = None

    async def _discard_model(self) -> None:
        self._pending_model = None

    async def on_model_picker_app_cancelled(
        self, _event: ModelPickerApp.Cancelled
    ) -> None:
        await self._switch_to_input_app()

    async def on_vibe_code_project_picker_app_project_selected(
        self, message: VibeCodeProjectPickerApp.ProjectSelected
    ) -> None:
        await self._handle_vibe_code_project_selected(project_id=message.project_id)

    async def _handle_vibe_code_project_selected(self, *, project_id: str) -> None:
        if self._vibe_code_project_picker.view is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Vibe Code project picker is not ready.",
                    collapsed=self._tools_collapsed,
                )
            )
            await self._switch_to_input_app()
            return

        teleport_pending = self._vibe_code_project_picker.teleport_pending
        view, project = await self.app_server.resources.vibe_code.select_project(
            project_id
        )
        self._vibe_code_project_picker.view = view
        if teleport_pending:
            await self._continue_pending_teleport(project.project_id)
            return

        await self._mount_and_scroll(
            UserCommandMessage(
                f"Linked this repository to Vibe Code project **{project.name}**."
            )
        )
        await self._switch_to_input_app()

    async def on_vibe_code_project_picker_app_create_requested(
        self, message: VibeCodeProjectPickerApp.CreateRequested
    ) -> None:
        context = self._vibe_code_project_picker.context
        git_info = self._vibe_code_project_picker.git_info
        repo_label = (
            repo_url_label(context.repo_url) if context else "current repository"
        )
        await self._replace_bottom_app(
            VibeCodeProjectCreateApp(
                project_name=message.project_name,
                repo_label=repo_label,
                default_branch=suggested_default_branch(git_info),
            )
        )

    async def on_vibe_code_project_create_app_submitted(
        self, message: VibeCodeProjectCreateApp.Submitted
    ) -> None:
        if self._vibe_code_project_picker.view is None:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Vibe Code project picker is not ready.",
                    collapsed=self._tools_collapsed,
                )
            )
            await self._switch_to_input_app()
            return

        await self._ensure_loading_widget("Creating project", show_hint=False)
        loading_widget = self._loading_widget
        try:
            view, project = await self.app_server.resources.vibe_code.create(
                name=message.project_name, default_branch=message.default_branch
            )
        except AppServerResponseError as e:
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
            return
        finally:
            if self._loading_widget is loading_widget:
                await self._remove_loading_widget()

        self._vibe_code_project_picker.view = view
        await self._handle_vibe_code_project_selected(project_id=project.project_id)

    async def on_vibe_code_project_create_app_cancelled(
        self, _message: VibeCodeProjectCreateApp.Cancelled
    ) -> None:
        await self._show_vibe_code_project_picker()

    async def on_vibe_code_project_picker_app_load_more_requested(
        self, _message: VibeCodeProjectPickerApp.LoadMoreRequested
    ) -> None:
        state = self._vibe_code_project_picker.picker_state
        if state is None or not state.has_more:
            await self._mount_and_scroll(
                UserCommandMessage("No more projects to load.")
            )
            return

        await self._ensure_loading_widget("Loading more projects", show_hint=False)
        loading_widget = self._loading_widget
        try:
            (
                view,
                focus_option_id,
            ) = await self.app_server.resources.vibe_code.load_more()
        except AppServerResponseError as e:
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
            return
        finally:
            if self._loading_widget is loading_widget:
                await self._remove_loading_widget()

        self._vibe_code_project_picker.view = view

        try:
            picker = self.query_one(VibeCodeProjectPickerApp)
        except Exception:
            return
        picker.update_projects(
            projects=view.state.projects, has_more=view.state.has_more
        )
        if focus_option_id is not None:
            picker.focus_option(focus_option_id)

    async def on_vibe_code_project_picker_app_unlink_requested(
        self, _message: VibeCodeProjectPickerApp.UnlinkRequested
    ) -> None:
        self._vibe_code_project_picker.view = (
            await self.app_server.resources.vibe_code.unlink()
        )
        self._vibe_code_project_picker.clear_teleport()
        await self._mount_and_scroll(
            UserCommandMessage("Remote Vibe Code project link cleared.")
        )
        await self._switch_to_input_app()

    async def on_vibe_code_project_picker_app_cancelled(
        self, _event: VibeCodeProjectPickerApp.Cancelled
    ) -> None:
        await self.app_server.resources.vibe_code.cancel_picker()
        self._vibe_code_project_picker.clear_teleport()
        await self._switch_to_input_app()

    async def on_thinking_picker_app_thinking_selected(
        self, message: ThinkingPickerApp.ThinkingSelected
    ) -> None:
        self._pending_thinking = message.level
        await self._queue.enqueue_command(
            f"thinking {message.level}",
            command_payload=partial(self._persist_thinking, message.level),
            on_discard=partial(self._discard_thinking),
        )
        await self._switch_to_input_app()

    async def _persist_thinking(self, level: ThinkingLevel) -> None:
        try:
            await self.app_server.resources.config.set_thinking(level)
            await self._reload_config()
        finally:
            self._pending_thinking = None

    async def _discard_thinking(self) -> None:
        self._pending_thinking = None

    async def on_thinking_picker_app_cancelled(
        self, _event: ThinkingPickerApp.Cancelled
    ) -> None:
        await self._switch_to_input_app()

    async def on_log_level_picker_app_applied(
        self, message: LogLevelPickerApp.Applied
    ) -> None:
        parts: list[str] = []
        previous_chain = get_log_level_chain()
        if message.session_level:
            set_session_override(message.session_level)
            parts.append(f"session override → {message.session_level}")
        else:
            set_session_override(None)
            if previous_chain.session:
                parts.append("session override cleared")
        config_feedback: str | None = None
        deferred = False
        try:
            if message.config_level:
                deferred = await self._defer_or_run_log_level_config(
                    level=message.config_level, clear=False
                )
                config_feedback = f"config.toml → {message.config_level}"
            elif message.config_cleared:
                deferred = await self._defer_or_run_log_level_config(
                    level=None, clear=True
                )
                config_feedback = "config.toml cleared"
        except Exception as exc:
            # The inline (idle) write failed; the deferred (busy) path enqueues
            # and surfaces its own error at drain time via _run_queued_command.
            logger.warning("Failed to persist log-level config", exc_info=exc)
            await self._switch_to_input_app()
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to persist log-level config: {exc}",
                    collapsed=self._tools_collapsed,
                )
            )
            return
        if config_feedback is not None:
            # A deferred write's outcome is unknown until it drains, so don't
            # claim it's done; the session override (already shown) is the live
            # effect meanwhile.
            parts.append(
                f"{config_feedback} (applies when idle)"
                if deferred
                else config_feedback
            )
        chain = get_log_level_chain()
        feedback = (
            "  ".join(parts) + f"  (effective: {chain.effective})"
            if parts
            else f"Log level unchanged  (effective: {chain.effective})"
        )
        await self._switch_to_input_app()
        await self._mount_and_scroll(UserCommandMessage(feedback))

    async def _defer_or_run_log_level_config(
        self, *, level: str | None, clear: bool
    ) -> bool:
        # The session override above is instant and is the effective change for
        # this session. The config.toml write is durability and requires idle
        # (config/write rejects with CONFLICT while a turn is in flight), so when
        # the picker was opened via the side channel while busy, defer the write
        # to the main queue. No on_discard: a discarded durable write leaves the
        # session override in place, which is the intended session-scoped effect.
        # Returns True when the write was deferred (success unknown at call
        # time), False when it ran inline and succeeded.
        payload = partial(self._persist_log_level_config, level=level, clear=clear)
        if self._is_busy() or self._queue:
            await self._queue.enqueue_command(
                "log-level config", command_payload=payload
            )
            return True
        await self._persist_log_level_config(level=level, clear=clear)
        return False

    async def _persist_log_level_config(
        self, *, level: str | None, clear: bool
    ) -> None:
        # Propagates: when run via the queue, _run_queued_command mounts an
        # ErrorMessage; when run directly (idle), the caller in
        # on_log_level_picker_app_applied catches and surfaces it. Either way
        # the user sees the failure instead of a premature success.
        if clear:
            await self._remove_config_field("log_level")
        else:
            await self._persist_config_changes({"log_level": level})

    async def on_theme_picker_app_theme_previewed(
        self, message: ThemePickerApp.ThemePreviewed
    ) -> None:
        await self._apply_theme(message.theme)

    async def on_theme_picker_app_theme_selected(
        self, message: ThemePickerApp.ThemeSelected
    ) -> None:
        await self._apply_theme(message.theme)
        self._pending_theme = message.theme
        await self._queue.enqueue_command(
            f"theme {message.theme}",
            command_payload=partial(self._persist_theme, message.theme),
            on_discard=partial(self._discard_theme, message.theme),
        )
        await self._switch_to_input_app()

    async def _persist_theme(self, theme: str) -> None:
        try:
            await self.app_server.resources.config.update({"theme": theme})
        except Exception:
            # On failure the persisted theme is unchanged, so revert the
            # visual theme that was applied speculatively at selection time.
            logger.exception("Failed to persist theme %s", theme)
            await self._apply_theme(self.config.theme)
        finally:
            self._pending_theme = None

    async def _discard_theme(self, theme: str) -> None:
        # The queued persist was discarded (e.g. Ctrl+C) before it could run,
        # so the speculative apply must be reverted the same way a failed
        # write reverts it.
        self._pending_theme = None
        await self._apply_theme(self.config.theme)

    async def on_theme_picker_app_cancelled(
        self, message: ThemePickerApp.Cancelled
    ) -> None:
        await self._apply_theme(message.original_theme)
        await self._switch_to_input_app()

    def _restyle_diff_widgets(self, *, ansi: bool, dark: bool) -> None:
        # Diff content bakes in ANSI-vs-truecolor styling, so it must be rebuilt.
        for widget in self.query(EditResultWidget):
            widget.request_diff_render(ansi=ansi, dark=dark)
        for widget in self.query(EditApprovalWidget):
            widget.request_diff_render(ansi=ansi, dark=dark)

    async def on_mcpapp_mcpclosed(self, _message: MCPApp.MCPClosed) -> None:
        await self._mount_and_scroll(UserCommandMessage("MCP servers closed."))
        await self._switch_to_input_app()

    async def on_mcpapp_mcptoggled(self, message: MCPApp.MCPToggled) -> None:
        await self.app_server.resources.mcp.toggle(
            name=message.name,
            source=(
                "connector" if message.kind == MCPSourceKind.CONNECTOR else "server"
            ),
            disabled=message.disabled,
            tool_name=message.tool_name,
        )
        self.query_one(_get_mcp_app_class()).refresh_index()
        self._refresh_banner()

    async def on_mcpapp_connector_auth_requested(
        self, message: MCPApp.ConnectorAuthRequested
    ) -> None:
        await self._open_connector_auth(message.connector_name)

    async def _open_connector_auth(self, connector_name: str) -> None:
        connector_auth_app_class = _get_connector_auth_app_class()
        await self._switch_to_input_app()
        await self._switch_from_input(
            connector_auth_app_class(
                connector_name=connector_name, mcp=self.app_server.resources.mcp
            )
        )

    async def on_mcpapp_mcpoauth_requested(
        self, message: MCPApp.MCPOAuthRequested
    ) -> None:
        await self._switch_to_input_app()
        await self._switch_from_input(
            _get_mcp_oauth_app_class()(
                server_name=message.server_name, mcp=self.app_server.resources.mcp
            )
        )

    async def on_connector_auth_app_connector_auth_closed(
        self, message: ConnectorAuthApp.ConnectorAuthClosed
    ) -> None:
        if message.refreshed:
            self._refresh_banner()
        await self._switch_to_input_app()
        await self._show_mcp(cmd_args=message.connector_name)

    async def on_mcpoauth_app_mcpoauth_closed(
        self, message: MCPOAuthApp.MCPOAuthClosed
    ) -> None:
        if message.refreshed:
            await self._refresh_mcp_browser()
        await self._switch_to_input_app()
        await self._show_mcp(cmd_args=message.server_name)

    async def on_proxy_setup_app_proxy_setup_closed(
        self, message: ProxySetupApp.ProxySetupClosed
    ) -> None:
        if not message.saved:
            await self._mount_and_scroll(UserCommandMessage("Proxy setup cancelled."))
        else:
            await self._queue.enqueue_command(
                "proxy settings",
                command_payload=partial(self._persist_proxy, message.changes),
            )

        await self._switch_to_input_app()

    async def _handle_command(self, user_input: str) -> bool:
        resolved = self.commands.parse_command(user_input)
        if not resolved:
            return False
        cmd_name, command, cmd_args = resolved
        display = self._command_display(user_input, cmd_name)
        return await self._invoke_resolved_command(cmd_name, command, cmd_args, display)

    @staticmethod
    def _command_display(user_input: str, cmd_name: str) -> str:
        command_text = user_input.strip()
        return (
            command_text.removeprefix("/") if command_text.startswith("/") else cmd_name
        )

    async def _invoke_resolved_command(
        self, cmd_name: str, command: Command, cmd_args: str, display_text: str
    ) -> bool:
        self.app_server.resources.telemetry.record(
            "vibe.slash_command_used",
            {"command": cmd_name.lstrip("/"), "command_type": "builtin"},
        )
        command_message = SlashCommandMessage(display_text)
        await self._mount_and_scroll(command_message)
        handler = getattr(self, command.handler)
        if asyncio.iscoroutinefunction(handler):
            await handler(cmd_args=cmd_args, command_message=command_message)
        else:
            handler(cmd_args=cmd_args, command_message=command_message)
        return True

    def _get_skill_entries(self) -> list[tuple[str, str]]:
        if self._app_server is None:
            return []
        return [
            (f"/{skill.name}", skill.description)
            for skill in self.app_server.resources.runtime.skills
            if skill.user_invocable
        ]

    def _resolve_skill(self, user_input: str) -> Skill | None:
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped[1:].split(None, 1)
        if not parts:
            return None
        name = parts[0].lower()
        skill = self.app_server.resources.runtime.get_skill(name)
        if skill is None or not skill.user_invocable:
            return None
        return Skill(command=user_input, name=skill.name)

    def _send_skill_telemetry(self, name: str | None) -> None:
        if name is None:
            return
        self.app_server.resources.telemetry.record(
            "vibe.slash_command_used",
            {"command": name.lstrip("/"), "command_type": "skill"},
        )

    async def _handle_bash_command(
        self, command: str, *, start_drain_on_finish: bool = True
    ) -> None:
        try:
            await self._handle_bash_command_inner(command)
        finally:
            current = asyncio.current_task()
            if self._bash_task is current:
                self._bash_task = None
            self._queue.notify_busy_changed()
            if start_drain_on_finish:
                self._queue.start_drain_if_needed()

    async def _handle_bash_command_inner(self, command: str) -> None:
        if not command:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No command provided after '!'", collapsed=self._tools_collapsed
                )
            )
            return

        await self._ensure_loading_widget("Running command")
        bash_loading_widget = self._loading_widget

        try:
            async for event in self.app_server.resources.shell.run(command):
                if self.event_handler is not None:
                    await self.event_handler.handle_event(
                        event, loading_widget=self._loading_widget
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(f"Command failed: {e}", collapsed=self._tools_collapsed)
            )
        finally:
            if self._loading_widget is bash_loading_widget:
                await self._remove_loading_widget()

    async def _handle_user_message(self, message: str) -> None:
        # Not ready: _prepare_prompt_or_abort will block, so show the indicator
        # first for immediate feedback. Ready path is untouched.
        if not self._session_ready.is_set():
            await self._ensure_loading_widget()
        prepared = await self._prepare_prompt_or_abort(message)
        if prepared is None:
            input_widget = self.query_one(ChatInputContainer)
            if not input_widget.value:
                input_widget.value = message
            return

        message_id = str(uuid4())
        user_message = UserMessage(
            message, history_entry_id=message_id, images=prepared.images or None
        )

        messages_area = self._cached_messages_area or self.query_one("#messages")
        last_child = messages_area.children[-1] if messages_area.children else None
        if isinstance(last_child, UserMessage):
            last_child.set_show_separator(False)
            user_message.set_follows_previous(True)

        await self._mount_and_scroll(user_message)
        await self._maybe_show_feedback_bar()

        if not self._agent_job_active():
            await self._remove_loading_widget()
            self._agent_task = asyncio.create_task(
                self._handle_turn(
                    message, prepared_prompt=prepared, client_message_id=message_id
                )
            )
            self._queue.notify_busy_changed()

    def _reset_ui_state(self) -> None:
        self._windowing.reset()
        self._history_widget_indices = WeakKeyDictionary()
        if self.event_handler is not None:
            self.event_handler.cancel_retry_presentation()

    async def _rebuild_transcript_from_current_session(self) -> None:
        # batch_update coalesces the teardown and re-mount into one refresh, so the
        # message area doesn't flash empty mid-rebuild.
        async with self._custom_tools_deprecation_message_lock:
            self._reset_ui_state()
            with self.batch_update():
                await self._load_more.hide()
                await self._messages_area.remove_children()
                await self._resume_history_from_messages()
            self._custom_tools_deprecation_message = None
        await self._show_custom_tools_deprecation_warning()

    async def _deferred_resume_and_start(self) -> None:
        try:
            await self._resume_history_from_messages()
        finally:
            self._initial_history_loaded.set()
        self.run_worker(self._listen_app_server_events(), exclusive=False)

    async def _resume_history_from_messages(self) -> None:
        messages_area = self._messages_area
        if not should_resume_history(list(messages_area.children)):
            return

        if (
            plan := create_resume_plan(
                self.app_server.history, HISTORY_RESUME_TAIL_MESSAGES
            )
        ) is None:
            return
        await self._mount_history_batch(
            plan.tail_entries, messages_area, start_index=plan.tail_start_index
        )
        self.call_after_refresh(self._chat_widget.anchor)
        self._windowing.set_backfill(plan.backfill_entries)
        await self._load_more.set_visible(
            messages_area,
            visible=self._has_older_history,
            remaining=self._history_backfill_remaining,
        )

    async def _mount_history_batch(
        self,
        batch: list[PublicHistoryEntry],
        messages_area: Widget,
        *,
        start_index: int,
        before: Widget | int | None = None,
        after: Widget | None = None,
    ) -> None:
        widgets = build_history_widgets(
            batch=batch,
            start_index=start_index,
            history_widget_indices=self._history_widget_indices,
            tools_collapsed=self._tools_collapsed,
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
        return self.app_server.resources.runtime.has_tool(tool)

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

    async def _request_local_user_input(
        self, request: UserQuestionRequest
    ) -> UserQuestionResult:
        if self._active_callback is not None:
            raise RuntimeError("Cannot open local input while a callback is active")
        self._pending_local_question = asyncio.get_running_loop().create_future()
        try:
            await self._wait_for_typing_pause()
            self._terminal_notifier.notify(NotificationContext.ACTION_REQUIRED)
            with paused_timer(self._loading_widget):
                await self._switch_to_question_app(request)
                return await self._pending_local_question
        finally:
            self._pending_local_question = None
            if self._pending_callbacks and self._active_callback is None:
                await self._show_callback(self._pending_callbacks.popleft())
            else:
                await self._switch_to_input_app()

    async def _show_callback(self, callback: PublicCallbackEntry) -> None:
        if (
            self._active_callback is not None
            or self._pending_local_question is not None
        ):
            if (
                self._active_callback is not None
                and self._active_callback.callback_id == callback.callback_id
            ):
                return
            if any(
                pending.callback_id == callback.callback_id
                for pending in self._pending_callbacks
            ):
                return
            self._pending_callbacks.append(callback)
            return
        self._active_callback = callback
        try:
            await self._wait_for_typing_pause()
            self._terminal_notifier.notify(NotificationContext.ACTION_REQUIRED)
            match callback.detail:
                case ApprovalCallbackDetail() as detail:
                    await self._switch_to_approval_app(
                        detail.effect, detail.required_permissions
                    )
                case UserInputCallbackDetail() as detail:
                    await self._switch_to_question_app(detail.request)
        except BaseException:
            if self._active_callback is callback:
                self._active_callback = None
            raise

    async def _respond_to_approval(
        self, decision: ApprovalDecisionType, feedback: str | None = None
    ) -> None:
        callback = self._active_callback
        if callback is None or not isinstance(callback.detail, ApprovalCallbackDetail):
            return
        await self._respond_to_active_callback(
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=decision), feedback=feedback
            )
        )

    async def _respond_to_active_callback(
        self, output: ApprovalCallbackOutput | UserInputCallbackOutput
    ) -> None:
        callback = self._active_callback
        if callback is None:
            return
        await self.app_server.respond_to_callback(callback.callback_id, output)
        if self._active_callback is callback:
            self._active_callback = None
            if self._pending_callbacks:
                await self._show_callback(self._pending_callbacks.popleft())
                return
            await self._switch_to_input_app()

    async def _handle_turn_error(self, *, cancelled: bool = False) -> None:
        if self._loading_widget and self._loading_widget.parent:
            await self._loading_widget.remove()
        if self.event_handler:
            self.event_handler.stop_current_tool_call(
                success=False, cancelled=cancelled
            )

    async def _ensure_runtime_ready(self) -> None:
        # Must precede any app_server access — the session may still be unbound
        # when a turn is dispatched (only _handle_turn reaches here).
        await self._session_ready.wait()
        show_init_spinner = not self.app_server.resources.runtime.ready
        if show_init_spinner:
            await self._ensure_loading_widget("Initializing", show_hint=False)
        await self.app_server.resources.runtime.wait_until_ready()
        if show_init_spinner:
            await self._remove_loading_widget()
            self._refresh_banner()

    async def _handle_turn_events(
        self, events: AsyncGenerator[AppServerEvent, None]
    ) -> None:
        async for event in events:
            await self._handle_turn_event(event)

    async def _handle_turn_event(self, event: AppServerEvent) -> None:
        self._track_narrator_event(event)
        if isinstance(event, ServerWarning):
            self.notify(event.params.warning.message, severity="warning")
            return
        if isinstance(event, ServerError):
            self.notify(event.params.error.message, severity="error")
            return
        if isinstance(event, CallbackRequested):
            await self._show_callback(event.callback)
            return
        if isinstance(event, StatsUpdated):
            self._update_context_progress(event)
            return
        entry = _public_entry(event)
        if isinstance(entry, PublicNoticeEntry) and isinstance(
            entry.detail, WaitingForInputNoticeDetail
        ):
            await self._remove_loading_widget()
        elif self._loading_widget is None and is_progress_event(event):
            await self._ensure_loading_widget()
        if self.event_handler:
            await self.event_handler.handle_event(
                event, loading_widget=self._loading_widget
            )

    async def _listen_app_server_events(self) -> None:
        async with aclosing(self.app_server.events()) as events:
            async for event in events:
                if isinstance(event, TurnStarted):
                    await self._begin_unsolicited_turn()
                await self._handle_turn_event(event)
                if isinstance(event, TurnCompleted):
                    await self._complete_unsolicited_turn(event)

    async def _begin_unsolicited_turn(self) -> None:
        self._queue.notify_busy_changed()
        await self._remove_loading_widget()
        await self._ensure_loading_widget()
        self._narrator_manager.cancel()
        self._narrator_manager.on_turn_start("")

    async def _complete_unsolicited_turn(self, event: TurnCompleted) -> None:
        if event.turn.status is PublicTurnStatus.FAILED:
            error = AppServerTurnError(event.turn.error)
            await self._handle_turn_error()
            message = self._resolve_turn_error_message(error)
            self._narrator_manager.on_turn_error(message)
            await self._mount_turn_error(error, message)
        elif event.turn.status is PublicTurnStatus.INTERRUPTED:
            await self._handle_turn_error(cancelled=True)
            self._narrator_manager.on_turn_cancel()
        await self._finalize_turn_ui()

    def _track_narrator_event(self, event: AppServerEvent) -> None:
        match event:
            case HistoryEntryAdded(entry=PublicMessageEntry(role="user") as entry):
                self._narrator_manager.on_user_message(entry.id)
            case HistoryEntryAdded(entry=PublicMessageEntry(role="assistant") as entry):
                self._narrator_manager.on_assistant_text(entry.text)
            case HistoryEntryUpdated(
                entry=PublicMessageEntry(role="assistant"), patch=patch
            ):
                for operation in patch:
                    if (
                        operation.op == "append"
                        and operation.path == "/content/0/text"
                        and isinstance(operation.value, str)
                    ):
                        self._narrator_manager.on_assistant_text(operation.value)

    async def _handle_turn(
        self,
        prompt: str,
        *,
        prepared_prompt: PreparedPrompt | None = None,
        client_message_id: str | None = None,
        injected: bool = False,
        incomplete_stream_retries: int = 0,
    ) -> None:
        await self._remove_loading_widget()
        retry_incomplete_stream = False

        try:
            await self._ensure_runtime_ready()
            await self._ensure_loading_widget(
                "Retrying" if incomplete_stream_retries else DEFAULT_LOADING_STATUS
            )
            if injected:
                prompt_text = prompt
                auto_title = None
                images = None
                mentions = None
            else:
                prepared = prepared_prompt or await self._prepare_prompt_or_abort(
                    prompt
                )
                if prepared is None:
                    return
                prompt_text = prepared.prompt_text
                auto_title = prepared.auto_title
                images = prepared.images or None
                mentions = prepared.mentions
            message_id = None if injected else client_message_id or str(uuid4())
            self._narrator_manager.cancel()
            self._narrator_manager.on_turn_start("" if injected else prompt_text)
            async with aclosing(
                self.app_server.act(
                    prompt_text,
                    client_message_id=message_id,
                    auto_title=auto_title,
                    images=images,
                    mention_stats=mentions,
                    injected=injected,
                )
            ) as events:
                await self._handle_turn_events(events)
        except asyncio.CancelledError:
            await self._handle_turn_error(cancelled=True)
            self._narrator_manager.on_turn_cancel()
            raise
        except Exception as e:
            await self._handle_turn_error()

            # _watch_init_completion already rendered the fatal startup error
            # and told the user to exit -- don't duplicate the message.
            if self._fatal_init_error:
                return

            retry_incomplete_stream = (
                isinstance(e, AppServerTurnError)
                and e.error.code == TurnErrorCode.INCOMPLETE_STREAM
                and incomplete_stream_retries < _MAX_INCOMPLETE_STREAM_RETRIES
            )
            if retry_incomplete_stream:
                # Auto-retry silently: don't surface the error while we retry.
                # Arm the retry presentation (without an error widget) so the
                # partial assistant message is reused when the retry streams in.
                if self.event_handler is not None:
                    self.event_handler.offer_retry()
            else:
                public_error = e.error if isinstance(e, AppServerTurnError) else None
                # Reaching here with INCOMPLETE_STREAM means the retry budget is
                # exhausted -- a persistent provider/network regression rather
                # than a transient blip, so keep it observable in Sentry even
                # though the code is otherwise benign.
                exhausted_incomplete_stream = (
                    public_error is not None
                    and public_error.code == TurnErrorCode.INCOMPLETE_STREAM
                )
                if (
                    public_error is None
                    or public_error.code not in _BENIGN_TURN_ERROR_CODES
                    or exhausted_incomplete_stream
                ):
                    capture_sentry_exception(
                        e, fatal=False, tags={"vibe_boundary": "app_server_turn"}
                    )

                message = self._resolve_turn_error_message(e)
                self._narrator_manager.on_turn_error(message)

                await self._mount_turn_error(e, message)
        finally:
            await self._finalize_turn_ui(resume_queue=not retry_incomplete_stream)

        if retry_incomplete_stream:
            await self._auto_retry_incomplete_stream(incomplete_stream_retries + 1)

    async def _auto_retry_incomplete_stream(
        self, incomplete_stream_retries: int
    ) -> None:
        # Run the retry inside the current task rather than a detached one. When
        # this turn was started by the queue drain, the drain is parked awaiting
        # this task; a detached retry would let it complete, so the drain would
        # resume and start the next queued prompt concurrently with the retry --
        # hitting "A turn is already running" and dropping that prompt. Keeping
        # the retry in this task makes the drain await the whole retry chain.
        if self.event_handler is None or not self.event_handler.begin_retry():
            return
        self._agent_task = asyncio.current_task()
        self._queue.notify_busy_changed()
        await self._handle_turn(
            build_retry_prompt(""),
            injected=True,
            incomplete_stream_retries=incomplete_stream_retries,
        )

    async def _mount_turn_error(self, error: Exception, message: str) -> None:
        widget = ErrorMessage(message, collapsed=self._tools_collapsed)
        await self._mount_and_scroll(widget)
        if (
            self.event_handler is not None
            and isinstance(error, AppServerTurnError)
            and error.error.code in _RETRYABLE_TURN_ERROR_CODES
        ):
            self.event_handler.offer_retry(widget)

    async def _retry(
        self,
        cmd_args: str = "",
        command_message: SlashCommandMessage | None = None,
        incomplete_stream_retries: int = 0,
        **_kwargs: Any,
    ) -> None:
        if self._agent_job_active():
            return
        if self.event_handler is None or not self.event_handler.begin_retry(
            command_message
        ):
            return
        self._agent_task = asyncio.create_task(
            self._handle_turn(
                build_retry_prompt(cmd_args),
                injected=True,
                incomplete_stream_retries=incomplete_stream_retries,
            )
        )
        self._queue.notify_busy_changed()

    async def _finalize_turn_ui(self, *, resume_queue: bool = True) -> None:
        self._narrator_manager.on_turn_end()
        self._interrupt_requested = False
        self._agent_task = None
        if self._loading_widget:
            await self._loading_widget.remove()
        self._loading_widget = None
        if self.event_handler:
            await self.event_handler.finalize_streaming()
            self.event_handler.escalate_unresolved_errors()
        self._queue.notify_busy_changed()
        if not resume_queue:
            return
        self._queue.start_drain_if_needed()
        await self._refresh_windowing_from_history()
        self._terminal_notifier.notify(NotificationContext.COMPLETE)

    def _resolve_turn_error_message(self, e: Exception) -> str:
        if not isinstance(e, AppServerTurnError):
            return str(e)
        code = e.error.code
        match code:
            case TurnErrorCode.RATE_LIMIT:
                base = self._rate_limit_message()
            case TurnErrorCode.CONTEXT_TOO_LONG:
                return self._context_too_long_message()
            case TurnErrorCode.REFUSAL:
                return self._refusal_message(e.error)
            case _:
                base = str(e)
        if code in _RETRYABLE_TURN_ERROR_CODES:
            return f"{base}{self._retry_hint()}"
        return base

    @staticmethod
    def _retry_hint() -> str:
        return (
            "\n\nRun /retry [additional instructions] to continue the interrupted "
            "response."
        )

    def _rate_limit_message(self) -> str:
        account = self.app_server.resources.account.current
        upgrade_to_pro = account is not None and account.rate_limit_action is not None
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

    def _refusal_message(self, error: PublicError) -> str:
        details = error.details if isinstance(error.details, dict) else {}
        category = details.get("category")
        explanation = details.get("explanation")
        lead = "The model declined to respond and stopped early (refusal)."
        if isinstance(category, str):
            lead += f"\nCategory: {category}."
        detail = (
            explanation
            if isinstance(explanation, str)
            else (
                "This can happen with certain prompts or content. "
                "Try rephrasing your request or starting a new conversation."
            )
        )
        return f"{lead}\n\n{detail}"

    async def _teleport_command(self, **kwargs: Any) -> None:
        await self._handle_teleport_command(show_message=False)

    async def _vibe_code_project_command(self, **_kwargs: Any) -> None:
        self._vibe_code_project_picker.clear_teleport()
        await self._ensure_loading_widget("Loading Vibe Code projects", show_hint=False)
        loading_widget = self._loading_widget
        try:
            view, _ = await self.app_server.resources.vibe_code.open_projects()
        except AppServerResponseError as e:
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
            return
        finally:
            if self._loading_widget is loading_widget:
                await self._remove_loading_widget()

        self._vibe_code_project_picker.view = view
        await self._show_vibe_code_project_picker()

    async def _resolve_vibe_code_project_for_teleport(
        self, prompt: str | None
    ) -> str | None:
        await self._ensure_loading_widget("Loading Vibe Code projects", show_hint=False)
        loading_widget = self._loading_widget
        try:
            view, project_id = await self.app_server.resources.vibe_code.open_projects(
                for_teleport=True, prompt=prompt
            )
        except AppServerResponseError as e:
            await self._mount_and_scroll(
                ErrorMessage(str(e), collapsed=self._tools_collapsed)
            )
            return None
        finally:
            if self._loading_widget is loading_widget:
                await self._remove_loading_widget()

        self._vibe_code_project_picker.view = view

        if project_id is not None:
            return project_id

        if view.saved_project_link_cleared:
            await self._mount_and_scroll(
                UserCommandMessage(
                    "The saved Vibe Code project link points to a different "
                    "repository remote. Pick the project to use for this repository."
                )
            )

        self._vibe_code_project_picker.teleport_pending = True
        self._vibe_code_project_picker.teleport_prompt = prompt
        await self._show_vibe_code_project_picker()
        return None

    async def _show_vibe_code_project_picker_after_saved_link_failure(
        self, prompt: str | None
    ) -> bool:
        if self._vibe_code_project_picker.view is None:
            return False

        view, recovered = await self.app_server.resources.vibe_code.recover_stale_link()
        self._vibe_code_project_picker.view = view
        if not recovered:
            return False
        self._vibe_code_project_picker.teleport_pending = True
        self._vibe_code_project_picker.teleport_prompt = prompt
        await self._mount_and_scroll(
            UserCommandMessage(
                "Saved Vibe Code project is no longer available. "
                "Pick the project to use for this repository."
            )
        )
        await self._show_vibe_code_project_picker()
        return True

    async def _continue_pending_teleport(self, project_id: str) -> None:
        prompt = self._vibe_code_project_picker.teleport_prompt
        self._vibe_code_project_picker.clear_teleport()
        await self._switch_to_input_app()
        self.run_worker(self._teleport(prompt, project_id=project_id), exclusive=False)

    async def _handle_teleport_command(
        self, value: str | None = None, show_message: bool = True
    ) -> None:
        if show_message:
            await self._mount_and_scroll(
                TeleportUserMessage(value) if value else SlashCommandMessage("teleport")
            )

        project_id = await self._resolve_vibe_code_project_for_teleport(value)
        if project_id is None:
            return

        self.run_worker(self._teleport(value, project_id=project_id), exclusive=False)

    async def _teleport(self, prompt: str | None = None, *, project_id: str) -> None:
        loading = LoadingWidget()
        await self._loading_area.mount(loading)

        teleport_msg = TeleportMessage()
        await self._mount_and_scroll(teleport_msg)

        try:
            async for event in self.app_server.resources.vibe_code.teleport(
                prompt, project_id=project_id
            ):
                if await self._handle_teleport_event(
                    event, prompt=prompt, loading=loading, message=teleport_msg
                ):
                    return
        except AppServerResponseError as e:
            await self._handle_teleport_failure(
                prompt=prompt,
                loading=loading,
                message=teleport_msg,
                code=e.error.code,
                error_message=str(e),
            )
        finally:
            if loading.parent:
                await loading.remove()

    async def _handle_teleport_event(
        self,
        event: TeleportEvent,
        *,
        prompt: str | None,
        loading: LoadingWidget,
        message: TeleportMessage,
    ) -> bool:
        match event:
            case TeleportSummarizingContext():
                message.set_status("Summarizing context...")
            case TeleportCheckingGit():
                message.set_status("Preparing workspace...")
            case TeleportPushRequired(
                operation_id=operation_id,
                unpushed_count=count,
                branch_not_pushed=branch_not_pushed,
            ):
                await loading.remove()
                approved = await self._ask_push_approval(count, branch_not_pushed)
                await self._loading_area.mount(loading)
                message.set_status("Teleporting...")
                await self.app_server.resources.vibe_code.respond_to_push(
                    operation_id, approved=approved
                )
            case TeleportPushing():
                message.set_status("Syncing with remote...")
            case TeleportStartingWorkflow():
                message.set_status("Teleporting...")
            case TeleportComplete(url=url):
                message.set_complete(url)
            case TeleportFailed(error=error):
                return await self._handle_teleport_failure(
                    prompt=prompt,
                    loading=loading,
                    message=message,
                    code=error.code,
                    error_message=error.message,
                )
        return False

    async def _handle_teleport_failure(
        self,
        *,
        prompt: str | None,
        loading: LoadingWidget,
        message: TeleportMessage,
        code: str | None,
        error_message: str,
    ) -> bool:
        if message.parent:
            await message.remove()
        if code == "saved_project_stale":
            if loading.parent:
                await loading.remove()
            if await self._show_vibe_code_project_picker_after_saved_link_failure(
                prompt
            ):
                return True
        await self._mount_and_scroll(
            ErrorMessage(error_message, collapsed=self._tools_collapsed)
        )
        return False

    async def _ask_push_approval(self, count: int, branch_not_pushed: bool) -> bool:
        if branch_not_pushed:
            question = "Your branch doesn't exist on remote. Push to continue?"
        else:
            word = f"commit{'s' if count != 1 else ''}"
            question = f"You have {count} unpushed {word}. Push to continue?"
        push_label = "Push and continue"
        result = await self._request_local_user_input(
            UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question=question,
                        header="Push",
                        options=[
                            QuestionChoice(label=push_label),
                            QuestionChoice(label="Cancel"),
                        ],
                        hide_other=True,
                    )
                ]
            )
        )
        ok = (
            not result.cancelled
            and bool(result.answers)
            and result.answers[0].answer == push_label
        )
        return ok

    async def _interrupt_turn(self) -> None:
        if not self._agent_job_active() or self._interrupt_requested:
            return

        self._interrupt_requested = True

        self._active_callback = None
        self._pending_callbacks.clear()
        if self._pending_local_question and not self._pending_local_question.done():
            self._pending_local_question.set_result(
                UserQuestionResult(answers=[], cancelled=True)
            )

        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass
        elif self.app_server.turn_active:
            await self.app_server.interrupt()

        if self.event_handler:
            self.event_handler.stop_current_tool_call(cancelled=True)
            self.event_handler.stop_current_compact()
            await self.event_handler.finalize_streaming()

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

        copy_result = copy_text_to_clipboard(
            self, content, success_message="Last agent message copied to clipboard"
        )
        if copy_result is not None:
            self.app_server.resources.telemetry.record(
                "vibe.user_copied_text", {"text_length": len(copy_result.text)}
            )

    async def _refresh_mcp_browser(self) -> str:
        # Wait for deferred init before the destructive force-refresh, otherwise
        # clearing the registries mid-initialization briefly empties the list
        # (the panel collapses then expands once discovery repopulates it).
        await self.app_server.resources.runtime.wait_until_ready()
        await self.app_server.resources.mcp.refresh_connectors()
        await self.app_server.resources.mcp.refresh()
        await self.app_server.resources.mcp.read()
        self._refresh_banner()
        return "Refreshed."

    async def _maybe_handle_mcp_subcommand(self, cmd_args: str) -> bool:
        parsed = parse_mcp_subcommand(cmd_args)
        if parsed is None:
            return False

        match parsed.name:
            case "add":
                await self._mcp_add(parsed.args)
            case "status":
                if parsed.args:
                    await self._mount_and_scroll(
                        ErrorMessage("Usage: /mcp status", collapsed=True)
                    )
                    return True
                await self._show_mcp_status()
            case "login":
                await self._mcp_login(parsed.args)
            case "logout":
                await self._mcp_logout(parsed.args)
        return True

    async def _show_mcp_status(self) -> None:
        await self.app_server.resources.runtime.wait_until_ready()
        statuses = (await self.app_server.resources.mcp.read()).statuses
        if not statuses:
            await self._mount_and_scroll(
                UserCommandMessage("No MCP servers configured.")
            )
            return
        lines = ["### MCP auth status", ""]
        for alias, status in sorted(statuses.items()):
            lines.append(f"- `{alias}`: `{status}`")
        await self._mount_and_scroll(UserCommandMessage("\n".join(lines)))

    async def _mcp_login(self, alias: str) -> None:
        if not alias:
            await self._mount_and_scroll(
                ErrorMessage("Usage: /mcp login <alias>", collapsed=True)
            )
            return

        try:
            if await self._maybe_login_connector(alias):
                return

            async for event in self.app_server.resources.mcp.login(alias):
                await self._mount_and_scroll(
                    UserCommandMessage(
                        f"Open this URL in your browser:\n\n  {event.url}"
                    )
                )
                try:
                    webbrowser.open(event.url)
                except Exception as exc:
                    logger.debug("Failed to open MCP OAuth URL in browser: %s", exc)
        except AppServerResponseError as exc:
            await self._mount_and_scroll(
                ErrorMessage(exc.error.message, collapsed=True)
            )
            return

        await self._mount_and_scroll(
            UserCommandMessage(f"MCP server `{alias}` authenticated.")
        )

    async def _maybe_login_connector(self, name: str) -> bool:
        await self.app_server.resources.runtime.wait_until_ready()
        state = await self.app_server.resources.mcp.read()
        # A server and a connector can share an alias; the server OAuth path
        # takes precedence so `/mcp add` auto-login isn't hijacked.
        is_server = any(
            source.name == name and source.kind is MCPSourceKind.SERVER
            for source in state.sources
        )
        is_connector = any(
            source.name == name and source.kind is MCPSourceKind.CONNECTOR
            for source in state.sources
        )
        if is_server or not is_connector:
            return False
        await self._open_connector_auth(name)
        return True

    async def _mcp_logout(self, alias: str) -> None:
        if not alias:
            await self._mount_and_scroll(
                ErrorMessage("Usage: /mcp logout <alias>", collapsed=True)
            )
            return

        try:
            await self.app_server.resources.mcp.logout(alias)
        except AppServerResponseError as exc:
            await self._mount_and_scroll(
                ErrorMessage(exc.error.message, collapsed=True)
            )
            return

        await self._mount_and_scroll(
            UserCommandMessage(f"MCP server `{alias}` logged out.")
        )

    async def _mcp_add(self, raw_args: str) -> None:
        if is_mcp_add_help_request(raw_args):
            await self._mount_and_scroll(UserCommandMessage(MCP_ADD_HELP))
            return

        try:
            args = parse_mcp_add_args(raw_args)
        except ValueError as exc:
            await self._mount_and_scroll(ErrorMessage(str(exc), collapsed=True))
            return

        try:
            result = await self.app_server.resources.mcp.add(
                url=args.url,
                name=args.name,
                scopes=args.scopes,
                transport=args.transport,
            )
        except AppServerResponseError as exc:
            await self._mount_and_scroll(
                ErrorMessage(exc.error.message, collapsed=True)
            )
            return

        head = (
            f"Added OAuth MCP server `{result.name}`."
            if result.created
            else f"OAuth MCP server `{result.name}` is already configured."
        )
        tail = (
            "Starting OAuth login..."
            if args.login
            else (
                f"Run `/mcp login {result.name}` to authenticate, "
                "or `/mcp status` to inspect it."
            )
        )
        await self._mount_and_scroll(UserCommandMessage(f"{head}\n{tail}"))

        if args.login:
            await self._mcp_login(result.name)

    async def _show_mcp(self, cmd_args: str = "", **kwargs: Any) -> None:
        if await self._maybe_handle_mcp_subcommand(cmd_args):
            return

        state = await self.app_server.resources.mcp.read()
        if state.connector_error:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Could not load workspace connectors.\n{state.connector_error}",
                    collapsed=False,
                )
            )
        if not state.sources:
            if not state.connector_error:
                await self._mount_and_scroll(
                    UserCommandMessage("No MCP servers or connectors configured.")
                )
            return

        if self._current_bottom_app == BottomApp.MCP:
            return
        name = cmd_args.strip()
        all_names = [source.name for source in state.sources]
        if name and name not in all_names:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Unknown MCP server or connector: {name}. Known: "
                    + ", ".join(all_names),
                    collapsed=self._tools_collapsed,
                )
            )
            return
        mcp_app_class = _get_mcp_app_class()
        await self._mount_and_scroll(UserCommandMessage("MCP servers opened..."))
        await self._switch_from_input(
            mcp_app_class(
                state=state,
                initial_source=name,
                state_getter=lambda: self.app_server.resources.mcp.state,
                refresh_callback=self._refresh_mcp_browser,
            )
        )

    async def _show_plugins(self, **kwargs: Any) -> None:
        state = await self.app_server.resources.plugins.read()
        if state is None:
            await self._mount_and_scroll(
                UserCommandMessage("This session resolves no plugins.")
            )
            return
        if not state.plugins and not state.dropped:
            await self._mount_and_scroll(
                UserCommandMessage("No plugins are installed for this session.")
            )
            return
        if self._current_bottom_app == BottomApp.Plugins:
            return
        await self._mount_and_scroll(UserCommandMessage("Plugins opened..."))
        await self._switch_from_input(_get_plugins_app_class()(state=state))

    async def _reload_plugins(self, **kwargs: Any) -> PluginCatalogDiff | None:
        from vibe.cli.textual_ui.widgets.plugins_app import plugin_reload_report

        try:
            diff = await self.app_server.resources.plugins.reload()
        except Exception as exc:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to reload plugins: {exc}", collapsed=self._tools_collapsed
                )
            )
            return None
        if diff is None:
            await self._mount_and_scroll(
                UserCommandMessage("This session resolves no plugins.")
            )
            return None
        await self._mount_and_scroll(UserCommandMessage(plugin_reload_report(diff)))
        return diff

    async def on_plugins_app_plugins_closed(
        self, _message: PluginsApp.PluginsClosed
    ) -> None:
        await self._mount_and_scroll(UserCommandMessage("Plugins closed."))
        await self._switch_to_input_app()

    async def on_plugins_app_plugins_reload_requested(
        self, _message: PluginsApp.PluginsReloadRequested
    ) -> None:
        diff = await self._reload_plugins()
        if diff is None:
            return
        with suppress(Exception):
            self.query_one(_get_plugins_app_class()).update_state(diff.state)

    async def _show_status(self, **kwargs: Any) -> None:
        stats = self.app_server.resources.runtime.stats
        session_cached = (
            f" _(including {stats.session_cached_tokens:,} cached)_"
            if stats.session_cached_tokens > 0
            else ""
        )
        last_turn_cached = (
            f" _(including {stats.last_turn_cached_tokens:,} cached)_"
            if stats.last_turn_cached_tokens > 0
            else ""
        )
        status_text = f"""## Agent Statistics

- **Steps**: {stats.steps:,}
- **Session Prompt Tokens**: {stats.session_prompt_tokens:,}{session_cached}
- **Session Completion Tokens**: {stats.session_completion_tokens:,}
- **Session Total LLM Tokens**: {stats.session_total_llm_tokens:,}
- **Last Turn Tokens**: {stats.last_turn_total_tokens:,}{last_turn_cached}
- **Cost**: ${stats.session_cost:.4f}
"""
        await self._mount_and_scroll(UserCommandMessage(status_text))

    async def _show_whoami(self, **kwargs: Any) -> None:
        loading = LoadingWidget(status="Loading", show_hint=False)
        await self._loading_area.mount(loading)
        try:
            identity, account = await asyncio.gather(
                self.app_server.resources.identity.read(),
                self.app_server.resources.account.read(),
                return_exceptions=True,
            )
        finally:
            if loading.parent:
                await loading.remove()
        if isinstance(identity, BaseException):
            logger.warning(
                "Identity check failed (%s).",
                type(identity).__name__,
                exc_info=identity,
            )
            identity = None
        if isinstance(account, BaseException):
            logger.warning(
                "Account check failed (%s).", type(account).__name__, exc_info=account
            )
            account = None
        if identity is None:
            await self._mount_and_scroll(
                UserCommandMessage(
                    "## Who am I\n\nNo identity information is available for the active model."
                )
            )
            return
        lines = ["## Who am I", ""]
        if identity.name and identity.name != identity.email:
            lines.append(f"- **Name**: {identity.name}")
        if identity.email:
            lines.append(f"- **Email**: {identity.email}")
        if identity.workspace:
            lines.append(f"- **Workspace**: {identity.workspace.name}")
        if identity.organization:
            lines.append(f"- **Organization**: {identity.organization.name}")
        if plan := plan_title(account):
            lines.append(f"- **Plan**: {plan}")
        await self._mount_and_scroll(UserCommandMessage("\n".join(lines)))

    async def _show_config(self, **kwargs: Any) -> None:
        """Open the full-screen, searchable settings browser."""
        from vibe.cli.textual_ui.screens.config import ConfigScreen

        def _on_close(dirty: bool | None) -> None:
            if dirty:
                self.run_worker(self._reload_config())

        self.push_screen(
            ConfigScreen(
                self.app_server.resources.config, write_callback=self._patch_config
            ),
            _on_close,
        )

    async def _patch_config(
        self, ops: list[ConfigWriteOpWire], reason: str
    ) -> ConfigWriteResult:
        from vibe.cli.textual_ui.screens.config import ConfigWriteResult

        if self._is_busy() or self._queue:
            await self._queue.enqueue_command(
                f"config patch ({reason})",
                command_payload=partial(self._run_config_patch, ops, reason),
            )
            return ConfigWriteResult.DEFERRED

        response = await self.app_server.resources.config.write(ops, reason=reason)
        if response.rejected:
            return ConfigWriteResult.REJECTED
        if response.failures:
            return ConfigWriteResult.FAILURES
        return ConfigWriteResult.ACCEPTED

    async def _run_config_patch(
        self, ops: list[ConfigWriteOpWire], reason: str
    ) -> None:
        response = await self.app_server.resources.config.write(ops, reason=reason)
        if response.rejected:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="Invalid configuration edit",
                )
            )
        if response.failures:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR,
                    message="; ".join(response.failures),
                )
            )
        await self._reload_config()

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

    async def _rename_session(self, cmd_args: str = "", **kwargs: Any) -> None:
        title = cmd_args.strip()
        if not title:
            await self._mount_and_scroll(
                ErrorMessage("Usage: /rename <title>", collapsed=self._tools_collapsed)
            )
            return

        try:
            renamed_title = await self.app_server.resources.sessions.rename(title)
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to rename session: {e}", collapsed=self._tools_collapsed
                )
            )
            return

        self._on_session_title_changed(renamed_title)
        await self._mount_and_scroll(
            UserCommandMessage(f'Session renamed to "{renamed_title}".')
        )

    async def _log_level_command(self, **kwargs: Any) -> None:
        await self._switch_to_log_level_picker_app()

    def _build_picker(self, sessions: list[PublicSession]) -> SessionPickerApp:
        sessions = sorted(sessions, key=lambda s: s.updated_at, reverse=True)
        return SessionPickerApp(
            sessions=sessions,
            latest_messages={
                session.id: session.title or session.preview for session in sessions
            },
            current_session_id=self.app_server.session_id,
            cwd=self.app_server.cwd,
        )

    async def _show_session_picker(
        self, command_message: SlashCommandMessage | None = None, **kwargs: Any
    ) -> None:
        # Mount the picker and erase the slash command message in one repaint so
        # the transition from command menu → picker is visually instant.
        picker = self._build_picker([])
        with self.batch_update():
            if command_message is not None:
                await command_message.remove()
            await self._switch_from_input(picker)

        local_sessions = await self.app_server.resources.sessions.list(
            self.app_server.cwd
        )
        if (
            not self.app_server.resources.runtime.session_log.enabled
            or not local_sessions
        ):
            await self._switch_to_input_app()
            self._mark_session_ready()
            await self._mount_and_scroll(
                UserCommandMessage("No sessions found for this directory.")
            )
            if self._show_resume_picker:
                self._show_resume_picker = False
                await self._process_startup_prompt_when_available()
            return

        picker.load_sessions(
            local_sessions, {s.id: s.title or s.preview for s in local_sessions}
        )

    async def on_session_picker_app_session_highlighted(
        self, event: SessionPickerApp.SessionHighlighted
    ) -> None:
        session_id = event.session_id
        if session_id is None:
            return
        # Current session is already displayed — don't reload unless we navigated
        # away first (in which case picker.previewing is True).
        if session_id == self.app_server.session_id and not self._picker.previewing:
            return
        # Record the intended preview target so a slower earlier request can't
        # overwrite the screen after the highlight moved on or resume started.
        self._picker.preview_session_id = session_id
        try:
            history = await self.app_server.resources.sessions.get_session_history(
                session_id
            )
        except Exception:
            logger.exception("get_session_history failed for %s", session_id)
            return
        if not self._picker.preview_is_current(session_id):
            return
        await self._apply_picker_preview(session_id, history)

    async def _apply_picker_preview(
        self, session_id: str, history: list[PublicHistoryEntry]
    ) -> None:
        self._picker.previewing = True
        self._reset_ui_state()
        plan = create_resume_plan(history, HISTORY_RESUME_TAIL_MESSAGES)
        with self.batch_update():
            await self._load_more.hide()
            await self._messages_area.remove_children()
            if not self._picker.preview_is_current(session_id):
                return
            if plan is not None:
                await self._mount_history_batch(
                    plan.tail_entries,
                    self._messages_area,
                    start_index=plan.tail_start_index,
                )
        if not self._picker.preview_is_current(session_id):
            return
        if plan is not None:
            self.call_after_refresh(self._chat_widget.anchor)
            self._windowing.set_backfill(plan.backfill_entries)
            await self._load_more.set_visible(
                self._messages_area,
                visible=self._has_older_history,
                remaining=self._history_backfill_remaining,
            )

    async def on_session_picker_app_session_selected(
        self, event: SessionPickerApp.SessionSelected
    ) -> None:
        was_previewing = self._picker.exit_preview()
        await self._switch_to_input_app()
        # Mark ready in the finally (covers both the failure return and success),
        # but only after _resume_local_session so a turn can't dispatch mid-rebind.
        try:
            await self._resume_local_session(event.session_id)
        except Exception as e:
            logger.exception("Failed to resume session %s", event.session_id)
            if self._show_resume_picker:
                self._show_resume_picker = False
                self._startup_prompt_processed = True
            # Resume failed before rebinding, so the session is unchanged; drop a
            # stale preview so it isn't left on screen desynced from session_id.
            if was_previewing:
                await self._rebuild_transcript_from_current_session()
            else:
                self.run_worker(
                    self._show_custom_tools_deprecation_warning_after_initial_history(),
                    exclusive=False,
                )
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to load session: {e}", collapsed=self._tools_collapsed
                )
            )
            return
        else:
            if self._show_resume_picker:
                self._show_resume_picker = False
                await self._process_startup_prompt_when_available()
        finally:
            self._mark_session_ready()

    async def on_session_picker_app_session_delete_requested(
        self, event: SessionPickerApp.SessionDeleteRequested
    ) -> None:
        if event.session_id == self.app_server.session_id:
            self._clear_pending_session_delete(event.option_id)
            await self._mount_and_scroll(
                ErrorMessage(
                    "Deleting the current session is not supported.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        try:
            await self.app_server.resources.sessions.delete(event.session_id)
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
            UserCommandMessage(f"Deleted session `{event.session_id[:8]}`.")
        )

        if picker is not None and not picker.has_sessions:
            await self._exit_picker_to_input()
            await self._mount_and_scroll(
                UserCommandMessage("No saved sessions left for this directory.")
            )

    def _clear_pending_session_delete(self, option_id: str) -> None:
        try:
            self.query_one(SessionPickerApp).clear_pending_delete(option_id)
        except Exception:
            pass

    async def _exit_picker_to_input(self) -> None:
        await self._switch_to_input_app()
        # Both exits (cancel, delete-last) return to the live input.
        self._mark_session_ready()
        if self._show_resume_picker:
            self._show_resume_picker = False
            self._startup_prompt_processed = True
        if self._picker.exit_preview():
            await self._rebuild_transcript_from_current_session()
            return
        self.run_worker(
            self._show_custom_tools_deprecation_warning_after_initial_history(),
            exclusive=False,
        )

    async def on_session_picker_app_cancelled(
        self, event: SessionPickerApp.Cancelled
    ) -> None:
        await self._exit_picker_to_input()
        await self._mount_and_scroll(UserCommandMessage("Resume cancelled."))

    async def _resume_local_session(self, session_id: str) -> None:
        self._picker.exit_preview()
        await self.app_server.resume(session_id)
        if self._chat_input_container:
            self._chat_input_container.set_custom_border(None)
        self._refresh_profile_widgets()
        self._refresh_context_progress()
        # Rebuild the transcript from the resumed session instead of trusting the
        # picker preview, which may have been skipped, may have failed, or may show
        # a different session than the one that was confirmed.
        await self._rebuild_transcript_from_current_session()
        await self._mount_and_scroll(
            UserCommandMessage(f"Resumed session `{session_id[:8]}`")
        )
        # Fast resume returns from the resume RPC before MCP/connector init
        # finishes, so defer post-init notices to the background until the
        # resumed runtime settles instead of racing incomplete state here.
        self.run_worker(self._finish_resume_notices(), exclusive=False)

    async def _finish_resume_notices(self) -> None:
        """Wait for a resumed session's deferred init to settle, then show
        post-init notices — and surface a late init failure.

        Fast resume returns from ``session/resume`` before MCP/connector init
        completes; the server finishes it in ``finish_resume_root`` and then
        emits ``runtime/updated``. Showing notices right after the RPC would
        race that init and read incomplete runtime state, and a background
        failure would go unsurfaced. Re-arm readiness against the now-resumed
        runtime so notices reflect the settled state and failures reach the UI.
        """
        if self._post_init_notices_shown:
            return
        try:
            await self.app_server.resources.runtime.wait_until_ready()
        except Exception as e:
            if isinstance(e, AppServerResponseError) and e.error.code in {
                ProtocolErrorCode.CONFLICT,
                ProtocolErrorCode.NOT_FOUND,
            }:
                # A newer resume replaced the root and now owns readiness/UI;
                # its own watcher will surface notices for the resumed runtime.
                logger.info("Resume readiness watch superseded by a newer resume")
                return
            logger.exception("Background initialization failed after resume")
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Background initialization failed: {e}",
                    collapsed=self._tools_collapsed,
                )
            )
            self._refresh_banner()
            return
        await self._show_post_init_notices_once()
        self._refresh_banner()

    async def _auto_resume_on_startup(self) -> None:
        await self._mount_and_scroll(UserCommandMessage("Resuming session…"))
        session_id: str | None = None
        try:
            session_id = self._resume_session_id
            if session_id is None:
                # --continue: resolve server-side so the tty-scoped last-session
                # pointer is preferred over the most recently updated session,
                # matching the pre-fast-resume _find_session_to_continue behavior.
                session_id = (
                    await self.app_server.resources.sessions.resolve_continue_session(
                        self.app_server.cwd
                    )
                )
                if session_id is None:
                    await self._mount_and_scroll(
                        UserCommandMessage("No previous sessions found.")
                    )
                    await self._show_custom_tools_deprecation_warning_after_initial_history()
                    await self._process_startup_prompt_when_available()
                    return
            await self._resume_local_session(session_id)
        except Exception as e:
            logger.exception(
                "Failed to auto-resume session %s", session_id or "<unknown>"
            )
            await self._rebuild_transcript_from_current_session()
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to resume session: {e}", collapsed=self._tools_collapsed
                )
            )
        finally:
            self._resume_session_id = None
            self._continue_latest = False
            # Covers --resume, --continue, and no-sessions-found (all flow here).
            # Must run before _process_startup_prompt so an injected prompt can
            # dispatch.
            self._mark_session_ready()
            # _resume_local_session spawns _finish_resume_notices as a worker,
            # but the startup prompt must fire regardless of whether notices
            # have settled — _process_startup_prompt_when_available guards on
            # the two flags cleared above, so the sequence is safe.
            await self._process_startup_prompt_when_available()

    async def _apply_config_to_ui(self) -> None:
        await self._apply_theme(self.config.theme)
        await self._refresh_account()
        self._narrator_manager.sync()
        self.run_worker(self._refresh_identity(), exclusive=False)
        self._sync_greeting_message()

        if self._banner:
            connectors = self.app_server.resources.runtime.connectors
            self._banner.set_state(
                self.app_server.resources.config.current,
                self.app_server.resources.runtime.custom_skills_count,
                mcp=self.app_server.resources.runtime.mcp,
                connectors_connected=connectors.connected,
                connectors_total=connectors.total,
                hooks_count=self.app_server.resources.runtime.hooks_count,
                plan_description=plan_title(self.app_server.resources.account.current),
                model_pending=self._model_pending(),
            )
        self._show_config_issues()

    async def _reload_config(self, **kwargs: Any) -> None:
        try:
            self._reset_ui_state()
            await self._load_more.hide()
            stripped_count = await self.app_server.resources.config.reload(
                reload_runtime=True
            )
            await self._apply_config_to_ui()
            await self._mount_and_scroll(
                UserCommandMessage(
                    "Configuration reloaded (includes agent instructions and skills)."
                )
            )
            if stripped_count > 0:
                model_name = self.config.active_model.display_name
                noun = "image" if stripped_count == 1 else "images"
                await self._mount_and_scroll(
                    WarningMessage(
                        f"{stripped_count} {noun} from earlier turns will be omitted "
                        f"when sending to {model_name} (no vision support)."
                    )
                )
        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to reload config: {e}", collapsed=self._tools_collapsed
                )
            )

    async def _install_lean(self, **kwargs: Any) -> None:
        current = {agent.name for agent in self.app_server.resources.agents.all}
        if "lean" in current:
            await self._mount_and_scroll(
                UserCommandMessage("Lean agent is already installed.")
            )
            return
        await self.app_server.resources.agents.set_installed("lean", installed=True)
        await self._reload_config()

    async def _uninstall_lean(self, **kwargs: Any) -> None:
        current = {agent.name for agent in self.app_server.resources.agents.all}
        if "lean" not in current:
            await self._mount_and_scroll(
                UserCommandMessage("Lean agent is not installed.")
            )
            return
        await self.app_server.resources.agents.set_installed("lean", installed=False)
        await self._reload_config()

    async def _reset_message_widgets(self) -> None:
        """Tear down the on-screen conversation widgets and UI state.

        Shared by ``/clear`` and the clear-context-on-plan-accept flow. Does not
        touch the agent loop's message history — callers decide whether the core
        history also needs clearing.
        """
        self._reset_ui_state()
        if self._chat_input_container:
            self._chat_input_container.set_custom_border(None)
        if self.event_handler:
            await self.event_handler.finalize_streaming()
        await self._messages_area.remove_children()

    async def _clear_history(self, cmd_args: str = "", **kwargs: Any) -> None:
        old_session_id = self.app_server.session_id
        session_log = self.app_server.resources.runtime.session_log
        resumable = session_log.enabled and session_log.persisted
        prompt = cmd_args.strip()
        try:
            await self.app_server.clear_history()
            await self._reset_message_widgets()

            await self._messages_area.mount(SlashCommandMessage("clear"))
            if resumable and old_session_id is not None:
                short_old = shorten_session_id(old_session_id)
                await self._mount_and_scroll(
                    UserCommandMessage(
                        "New conversation started.\n\n"
                        f"Previous session: `{short_old}`\n"
                        f"To resume it later, run: `vibe --resume {short_old}`"
                    )
                )
            else:
                await self._mount_and_scroll(
                    UserCommandMessage("New conversation started.")
                )
            self._chat_widget.scroll_home(animate=False)
            self._sync_terminal_title()

        except Exception as e:
            await self._mount_and_scroll(
                ErrorMessage(
                    f"Failed to clear history: {e}", collapsed=self._tools_collapsed
                )
            )
            return

        if prompt:
            await self._handle_user_message(prompt)

    async def _on_context_cleared(self, plan_file_path: Path | None = None) -> None:
        """React to a ContextClearedEvent emitted during plan accept.

        Core already cleared the agent loop's history, so this only resets the
        on-screen widgets and posts a notice that implementation is starting. The
        approved plan is re-mounted so it stays visible in the discussion.
        """
        await self._reset_message_widgets()
        if plan_file_path is not None:
            await self._mount_and_scroll(PlanFileMessage(file_path=plan_file_path))
        await self._mount_and_scroll(
            UserCommandMessage("Context cleared. Implementing the approved plan...")
        )
        self._chat_widget.scroll_home(animate=False)

    async def _show_log_path(self, **kwargs: Any) -> None:
        session_log = await self.app_server.resources.sessions.read_log()
        if not session_log.enabled:
            await self._mount_and_scroll(
                ErrorMessage(
                    "Session logging is disabled in configuration.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not session_log.persisted:
            await self._mount_and_scroll(
                ErrorMessage(
                    "The current session has not been persisted yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        await self._mount_and_scroll(
            UserCommandMessage(
                "## Current Log Directory\n\n"
                f"`{session_log.path}`\n\n"
                "You can send this directory to share your interaction."
            )
        )

    async def _loop_command(self, cmd_args: str = "", **kwargs: Any) -> None:
        widget = await self._loop_commands.handle_command(cmd_args)
        await self._mount_and_scroll(widget)

    async def _compact_history(self, cmd_args: str = "", **kwargs: Any) -> None:
        if self._agent_job_active():
            await self._mount_and_scroll(
                ErrorMessage(
                    "Cannot compact while agent loop is processing. Please wait.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.app_server.history:
            await self._mount_and_scroll(
                ErrorMessage(
                    "No conversation history to compact yet.",
                    collapsed=self._tools_collapsed,
                )
            )
            return

        if not self.event_handler:
            return

        compact_msg = CompactMessage()
        self.event_handler.current_compact = compact_msg
        await self._mount_and_scroll(compact_msg)

        self._agent_task = asyncio.create_task(
            self._run_compact(compact_msg, cmd_args.strip())
        )

    async def _run_compact(
        self, compact_msg: CompactMessage, extra_instructions: str = ""
    ) -> None:
        try:
            await self.app_server.compact(extra_instructions=extra_instructions)
            compact_msg.set_complete()

        except asyncio.CancelledError:
            compact_msg.set_error("Compaction interrupted")
            raise
        except Exception as e:
            compact_msg.set_error(str(e))
        finally:
            self._agent_task = None
            if self.event_handler:
                self.event_handler.current_compact = None

    def _get_session_exit_summary(self) -> SessionExitSummary:
        if self._mount_first and self._app_server is None:
            return SessionExitSummary(session_id=None, usage=TokenUsage())
        return self.app_server.exit_summary()

    async def _exit_app(self, **kwargs: Any) -> None:
        try:
            await self._begin_shutdown()
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            if self._bash_task and not self._bash_task.done():
                self._bash_task.cancel()
        finally:
            self.exit(result=self._get_session_exit_summary())

    def _make_default_voice_manager(self) -> VoiceManagerPort:
        return create_default_voice_manager(
            lambda: self.config,
            self.app_server.resources.telemetry,
            self._get_audio_request_metadata,
        )

    def _get_audio_request_metadata(self) -> dict[str, str]:
        session = self.app_server.state.session
        return build_audio_request_metadata(
            session_id=session.id, parent_session_id=session.parent_session_id
        )

    async def _show_voice_settings(self, **kwargs: Any) -> None:
        if self._current_bottom_app == BottomApp.Voice:
            return
        await self._switch_to_voice_app()

    async def _switch_from_input(self, widget: Widget, scroll: bool = False) -> None:
        bottom_container = self.query_one("#bottom-app-container")
        chat = self._chat_widget
        should_scroll = scroll and chat.is_at_bottom
        stale_bottom_apps = self._mounted_non_input_bottom_apps()

        with self.batch_update():
            if self._chat_input_container:
                self._chat_input_container.display = False
                self._chat_input_container.disabled = True

            self._feedback_bar.hide()

            for stale in stale_bottom_apps:
                await stale.remove()

            self._current_bottom_app = BottomApp[
                type(widget).__name__.removesuffix("App")
            ]
            self._input_app_ready.clear()
            await bottom_container.mount(widget)

        self.call_after_refresh(widget.focus)
        if should_scroll:
            self.call_after_refresh(chat.anchor)

    def _mounted_non_input_bottom_apps(self) -> list[Widget]:
        mounted: list[Widget] = []
        for app in BottomApp:
            if app == BottomApp.Input:
                continue
            try:
                mounted.append(self.query_one(f"#{app.value}-app"))
            except Exception:
                pass
        return mounted

    async def _replace_bottom_app(self, widget: Widget, scroll: bool = False) -> None:
        bottom_container = self.query_one("#bottom-app-container")
        chat = self._chat_widget
        should_anchor = chat.is_at_bottom
        old_widgets: list[Widget] = []
        for app in BottomApp:
            if app == BottomApp.Input:
                continue
            try:
                old_widgets.append(self.query_one(f"#{app.value}-app"))
            except Exception:
                pass

        with self.batch_update():
            if self._chat_input_container:
                self._chat_input_container.display = False
                self._chat_input_container.disabled = True

            self._feedback_bar.hide()

            self._current_bottom_app = BottomApp[
                type(widget).__name__.removesuffix("App")
            ]
            self._input_app_ready.clear()
            await bottom_container.mount(widget)
            for old_widget in old_widgets:
                await old_widget.remove()

        self.call_after_refresh(widget.focus)
        if should_anchor or scroll:
            self.call_after_refresh(chat.anchor)

    async def _show_vibe_code_project_picker(self) -> None:
        context = self._vibe_code_project_picker.context
        state = self._vibe_code_project_picker.picker_state
        if context is None or state is None:
            await self._switch_to_input_app()
            return

        await self._replace_bottom_app(
            VibeCodeProjectPickerApp(
                context=context,
                projects=state.projects,
                has_more=state.has_more,
                include_unlink=context.saved_link is not None,
                title="Vibe Code project",
            )
        )

    async def _switch_to_voice_app(self) -> None:
        if self._current_bottom_app == BottomApp.Voice:
            return

        await self._mount_and_scroll(UserCommandMessage("Voice settings opened..."))
        await self._switch_from_input(VoiceApp(self.config))

    async def _is_active_model_enforced(self) -> bool:
        from vibe.cli.textual_ui.screens.config._common import ADMIN_LAYER

        response = await self.app_server.resources.config.read_fields()
        field = next((f for f in response.fields if f.name == "active_model"), None)
        return field is not None and field.origin == ADMIN_LAYER

    async def _switch_to_model_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ModelPicker:
            return

        models = [
            ModelOption(alias=model.alias, display_name=model.display_name)
            for model in self.config.models
        ]
        current_model = self._effective_model_alias
        await self._switch_from_input(
            ModelPickerApp(
                models=models,
                current_model=current_model,
                is_pinned=self.config.active_model_pinned,
                default_display_name=self.config.default_model_display_name,
            )
        )

    async def _switch_to_thinking_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ThinkingPicker:
            return

        current_thinking = self._effective_thinking
        await self._switch_from_input(
            ThinkingPickerApp(
                thinking_levels=THINKING_LEVELS, current_thinking=current_thinking
            )
        )

    async def _switch_to_log_level_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.LogLevelPicker:
            return
        await self._switch_from_input(LogLevelPickerApp(chain=get_log_level_chain()))

    async def _switch_to_theme_picker_app(self) -> None:
        if self._current_bottom_app == BottomApp.ThemePicker:
            return

        await self._switch_from_input(
            ThemePickerApp(
                theme_names=sorted_theme_names(), current_theme=self._effective_theme
            )
        )

    async def _apply_theme(self, theme: str) -> None:
        resolved_theme = resolve_theme(resolve_theme_name(theme))
        if resolved_theme == self.theme:
            return

        previous_diff_theme = (self.native_ansi_color, self.current_theme.dark)
        self.theme = resolved_theme
        current_diff_theme = (self.native_ansi_color, self.current_theme.dark)
        if current_diff_theme != previous_diff_theme:
            self._restyle_diff_widgets(
                ansi=current_diff_theme[0], dark=current_diff_theme[1]
            )

    async def _switch_to_proxy_setup_app(self) -> None:
        if self._current_bottom_app == BottomApp.ProxySetup:
            return

        try:
            settings = await self.app_server.resources.config.read_proxy()
        except Exception as exc:
            await self._mount_and_scroll(
                ErrorMessage(f"Failed to read proxy settings: {exc}")
            )
            return
        await self._mount_and_scroll(UserCommandMessage("Proxy setup opened..."))
        await self._switch_from_input(ProxySetupApp(settings))

    async def _switch_to_approval_app(
        self,
        effect: EffectDetail,
        required_permissions: list[RequiredPermission] | None = None,
    ) -> None:
        approval_app = ApprovalApp(
            effect=effect, config=self.config, required_permissions=required_permissions
        )
        await self._switch_from_input(approval_app, scroll=True)

    async def _switch_to_question_app(self, args: UserQuestionRequest) -> None:
        await self._switch_from_input(QuestionApp(args=args), scroll=True)

    async def _switch_to_input_app(self) -> None:
        if self._chat_input_container:
            self._chat_input_container.disabled = False
            self._chat_input_container.display = True
            self._current_bottom_app = BottomApp.Input
            self._refresh_profile_widgets()
        self._input_app_ready.set()

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
        focus_widget_by_app: dict[BottomApp, type[Widget]] = {
            BottomApp.LogLevelPicker: LogLevelPickerApp,
            BottomApp.ModelPicker: ModelPickerApp,
            BottomApp.ThemePicker: ThemePickerApp,
            BottomApp.ThinkingPicker: ThinkingPickerApp,
            BottomApp.ProxySetup: ProxySetupApp,
            BottomApp.Approval: ApprovalApp,
            BottomApp.Question: QuestionApp,
            BottomApp.VibeCodeProjectCreate: VibeCodeProjectCreateApp,
            BottomApp.VibeCodeProjectPicker: VibeCodeProjectPickerApp,
            BottomApp.SessionPicker: SessionPickerApp,
            BottomApp.MCP: _get_mcp_app_class(),
            BottomApp.Plugins: _get_plugins_app_class(),
            BottomApp.ConnectorAuth: _get_connector_auth_app_class(),
            BottomApp.MCPOAuth: _get_mcp_oauth_app_class(),
            BottomApp.Rewind: RewindApp,
            BottomApp.Voice: VoiceApp,
        }
        try:
            if self._current_bottom_app == BottomApp.Input:
                self.query_one(ChatInputContainer).focus_input()
                return
            self.query_one(focus_widget_by_app[self._current_bottom_app]).focus()
        except Exception:
            pass

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
                self.app_server.resources.telemetry.record(
                    "vibe.user_cancelled_action", {"action": "reject_approval"}
                )
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_question_app_escape(self) -> None:
        try:
            question_app = self.query_one(QuestionApp)
            if not question_app.is_within_grace_period():
                question_app.action_cancel()
                self.app_server.resources.telemetry.record(
                    "vibe.user_cancelled_action", {"action": "cancel_question"}
                )
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_log_level_picker_app_escape(self) -> None:
        try:
            log_level_picker = self.query_one(LogLevelPickerApp)
            log_level_picker.action_apply()
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

    def _handle_vibe_code_project_picker_app_escape(self) -> None:
        try:
            vibe_code_project_picker = self.query_one(VibeCodeProjectPickerApp)
            vibe_code_project_picker.action_cancel()
        except Exception:
            pass
        self._last_escape_time = None

    def _handle_vibe_code_project_create_app_escape(self) -> None:
        try:
            vibe_code_project_create = self.query_one(VibeCodeProjectCreateApp)
            vibe_code_project_create.action_cancel()
        except Exception:
            pass
        self._last_escape_time = None

    # --- Rewind mode ---

    def _get_user_message_widgets(self) -> list[UserMessage]:
        """Return all UserMessage widgets currently visible in #messages.

        Only includes messages with a public history id (i.e. real user
        messages, not slash-command echo messages).
        """
        return [
            child
            for child in self._messages_area.children
            if isinstance(child, UserMessage) and child.history_entry_id is not None
        ]

    def _start_rewind_mode(self, **kwargs: Any) -> None:
        self.action_rewind_prev()

    def action_rewind_prev(self) -> None:
        if self._agent_job_active():
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
                self.run_worker(
                    self._rewind_prev_at_top(), group="rewind", exclusive=False
                )
                return
            target = user_widgets[idx - 1]
        else:
            target = user_widgets[-1]

        self.run_worker(
            self._select_rewind_widget(target), group="rewind", exclusive=False
        )

    async def _rewind_prev_at_top(self) -> None:
        """Handle navigating past the topmost visible user message."""
        if self._load_more.widget is not None and self._has_older_history:
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
            self._select_rewind_widget(user_widgets[idx + 1]),
            group="rewind",
            exclusive=False,
        )

    async def _select_rewind_widget(self, widget: UserMessage) -> None:
        """Highlight the given user message widget and show the rewind panel."""
        if self._rewind_highlighted_widget is not None:
            self._rewind_highlighted_widget.remove_class("rewind-selected")

        widget.add_class("rewind-selected")
        self._rewind_highlighted_widget = widget

        entry_id = widget.history_entry_id
        if entry_id is None:
            return
        has_file_changes = (
            await self.app_server.resources.sessions.rewind_has_file_changes(entry_id)
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

    def _handle_rewind_app_escape(self) -> None:
        try:
            rewind_app = self.query_one(RewindApp)
        except Exception:
            rewind_app = None
        if rewind_app is not None and rewind_app.go_back():
            return
        self.action_rewind_prev()

    async def on_rewind_app_rewind_confirmed(
        self, message: RewindApp.RewindConfirmed
    ) -> None:
        await self._execute_rewind(
            restore_files=message.restore_files, inplace=message.inplace
        )

    def on_rewind_app_edit_prev(self, message: RewindApp.EditPrev) -> None:
        self.action_rewind_prev()

    def on_rewind_app_edit_next(self, message: RewindApp.EditNext) -> None:
        self.action_rewind_next()

    async def on_rewind_app_quit(self, message: RewindApp.Quit) -> None:
        await self._exit_rewind_mode()

    async def _execute_rewind(self, *, restore_files: bool, inplace: bool) -> None:
        if not self._rewind_mode or self._rewind_highlighted_widget is None:
            return

        target_widget = self._rewind_highlighted_widget
        entry_id = target_widget.history_entry_id
        if entry_id is None:
            return

        old_session_id = self.app_server.session_id
        try:
            result = await self.app_server.resources.sessions.rewind(
                entry_id, restore_files=restore_files, inplace=inplace
            )
            await self.app_server.resources.refresh()
        except AppServerResponseError as exc:
            self.notify(exc.error.message, severity="error")
            return

        message_content = result.message
        restore_errors = result.restore_errors

        for error in restore_errors:
            self.notify(error, severity="warning")

        self._clear_rewind_state()
        await self._switch_to_input_app()
        await self._reset_message_widgets()
        await self._resume_history_from_messages()
        if not inplace:
            await self._mount_and_scroll(
                RewindForkMessage(
                    old_session_id=old_session_id,
                    new_session_id=self.app_server.session_id,
                )
            )
        if self._chat_input_container:
            self._chat_input_container.value = message_content

    # --- End rewind mode ---

    def _clear_input(self) -> None:
        try:
            input_widget = self.query_one(ChatInputContainer)
            input_widget.value = ""
        except Exception:
            pass

    def _handle_input_double_escape(self) -> None:
        """Clear the input when it has content, otherwise enter rewind mode."""
        self._last_escape_time = None
        if self._chat_input_container and self._chat_input_container.value:
            self._clear_input()
        else:
            self._start_rewind_mode()

    def _handle_agent_running_escape(self) -> None:
        self.app_server.resources.telemetry.record(
            "vibe.user_cancelled_action", {"action": "interrupt_agent"}
        )
        self.run_worker(self._interrupt_turn(), exclusive=False)

    def _handle_bottom_app_close_escape(self, widget_type: type[Widget]) -> None:
        try:
            cast(Any, self.query_one(widget_type)).action_close()
        except Exception:
            pass
        self._last_escape_time = None

    def _try_interrupt_bottom_app_escape(self) -> bool:
        handlers = {
            BottomApp.Voice: self._handle_voice_app_escape,
            BottomApp.MCP: lambda: self._handle_bottom_app_close_escape(
                _get_mcp_app_class()
            ),
            BottomApp.Plugins: lambda: self._handle_bottom_app_close_escape(
                _get_plugins_app_class()
            ),
            BottomApp.ConnectorAuth: lambda: self._handle_bottom_app_close_escape(
                _get_connector_auth_app_class()
            ),
            BottomApp.MCPOAuth: lambda: self._handle_bottom_app_close_escape(
                _get_mcp_oauth_app_class()
            ),
            BottomApp.ProxySetup: lambda: self._handle_bottom_app_close_escape(
                ProxySetupApp
            ),
            BottomApp.Approval: self._handle_approval_app_escape,
            BottomApp.Question: self._handle_question_app_escape,
            BottomApp.LogLevelPicker: self._handle_log_level_picker_app_escape,
            BottomApp.ModelPicker: self._handle_model_picker_app_escape,
            BottomApp.ThemePicker: self._handle_theme_picker_app_escape,
            BottomApp.ThinkingPicker: self._handle_thinking_picker_app_escape,
            BottomApp.VibeCodeProjectCreate: self._handle_vibe_code_project_create_app_escape,
            BottomApp.VibeCodeProjectPicker: (
                self._handle_vibe_code_project_picker_app_escape
            ),
            BottomApp.SessionPicker: self._handle_session_picker_app_escape,
        }

        if handler := handlers.get(self._current_bottom_app):
            handler()
        elif self._current_bottom_app == BottomApp.Rewind:
            self._handle_rewind_app_escape()
            self._last_escape_time = None
        elif (
            self._current_bottom_app == BottomApp.Input
            and self._last_escape_time is not None
            and (time.monotonic() - self._last_escape_time) < DOUBLE_ESC_DELAY
        ):
            self._handle_input_double_escape()
        else:
            return False
        return True

    def _try_interrupt_no_job_steps(self) -> bool:
        if self._voice_manager.transcribe_state != TranscribeState.IDLE:
            self._voice_manager.cancel_recording()
            return True

        if self._chat_input_container:
            dismissed = self._chat_input_container.dismiss_completion()
            # A leading-slash input is cleared on Escape regardless of whether a
            # completion popup was visible (independent of completion state).
            clears_slash_input = self._chat_input_container.value.startswith("/")
            if dismissed or clears_slash_input:
                if clears_slash_input:
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
        if self._agent_job_active():
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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable the priority escape->interrupt binding on config modals and queue selection so escape falls through."""
        if action != "interrupt":
            return True
        screen_id = self.screen.id
        if screen_id is not None and screen_id.startswith("config-"):
            return False
        containers = self.query(ChatInputContainer)
        if not containers:
            return True
        body = containers[0]._body
        if body is not None and body.in_queue_mode:
            return False
        return True

    def action_interrupt(self) -> None:
        if self._app_server is None:
            return
        self._try_interrupt()

    async def on_history_load_more_requested(self, _: HistoryLoadMoreRequested) -> None:
        self._load_more.set_enabled(False)
        try:
            if not self._windowing.has_backfill:
                if not await self._load_older_history_page():
                    await self._load_more.hide()
                    return
            if (batch := self._windowing.next_load_more_batch()) is None:
                await self._load_more.hide()
                return
            messages_area = self._messages_area
            if self._load_more.widget:
                before: Widget | int | None = None
                after: Widget | None = self._load_more.widget
            else:
                before = 0
                after = None
            await self._mount_history_batch(
                batch.entries,
                messages_area,
                start_index=batch.start_index,
                before=before,
                after=after,
            )
            await self._load_more.set_visible(
                messages_area,
                visible=self._has_older_history,
                remaining=self._history_backfill_remaining,
            )
        finally:
            self._load_more.set_enabled(True)

    @property
    def _has_older_history(self) -> bool:
        if self._windowing.has_backfill:
            return True
        # During picker preview the live session's server-side cursor is irrelevant:
        # the preview history was fetched in full via session/history/get, so only
        # the local windowing backfill determines whether Load More is available.
        if self._picker.previewing:
            return False
        return self.app_server.resources.sessions.history_before_cursor is not None

    @property
    def _history_backfill_remaining(self) -> int | None:
        if self._picker.previewing:
            return self._windowing.remaining or None
        if self.app_server.resources.sessions.history_before_cursor is not None:
            return None
        return self._windowing.remaining or None

    async def _load_older_history_page(self) -> bool:
        before = self.app_server.resources.sessions.history_before_cursor
        if before is None:
            return False
        page = await self.app_server.resources.sessions.load_before(
            before, LOAD_MORE_BATCH_SIZE
        )
        if not page.data:
            return False
        shift_history_widget_indices(self._history_widget_indices, len(page.data))
        self._windowing.set_backfill(page.data)
        return True

    async def action_toggle_tool(self) -> None:
        self._tools_collapsed = not self._tools_collapsed
        for section in self.query(CollapsibleSection):
            section.set_collapsed(self._tools_collapsed)

    def action_cycle_mode(self) -> None:
        if self._app_server is None or self._current_bottom_app != BottomApp.Input:
            return
        self._refresh_profile_widgets()
        self._focus_current_bottom_app()
        self._request_next_agent()

    def _refresh_profile_widgets(self) -> None:
        self._update_profile_widgets(self.app_server.resources.agents.active)

    def _refresh_context_progress(self) -> None:
        runtime = self.app_server.resources.runtime
        self.query_one(ContextProgress).tokens = TokenState(
            max_tokens=runtime.context_window,
            current_tokens=runtime.stats.context_tokens,
        )

    def _on_profile_changed(self) -> None:
        self._refresh_profile_widgets()
        self._refresh_banner()

    async def _should_show_greeting(self) -> bool:
        if self._whats_new_message:
            return False
        if not self.config.show_greeting:
            return False
        try:
            cache = FileSystemCacheStore()
            greeting_data = await asyncio.to_thread(cache.read_section, "greeting")
            last_shown = greeting_data.get("last_shown_at")
            if last_shown is None:
                return True
            now = time.time()
            return now - last_shown > _GREETING_INTERVAL_SECONDS
        except Exception:
            return True  # Fail open

    async def _mark_greeting_shown(self) -> None:
        """Mark that greeting was shown with current timestamp."""
        try:
            cache = FileSystemCacheStore()
            await asyncio.to_thread(
                cache.write_section, "greeting", {"last_shown_at": int(time.time())}
            )
        except Exception:
            pass

    def _username(self) -> str | None:
        identity = self.app_server.resources.identity.current
        if identity is None or not identity.first_name:
            return None
        return identity.first_name

    def _refresh_banner(self) -> None:
        if self._banner:
            connectors = self.app_server.resources.runtime.connectors
            self._banner.set_state(
                self.config,
                self.app_server.resources.runtime.custom_skills_count,
                mcp=self.app_server.resources.runtime.mcp,
                connectors_connected=connectors.connected,
                connectors_total=connectors.total,
                hooks_count=self.app_server.resources.runtime.hooks_count,
                plan_description=plan_title(self.app_server.resources.account.current),
                model_pending=self._model_pending(),
            )

    def _update_profile_widgets(self, profile: AgentSummary) -> None:
        if self._chat_input_container:
            self._chat_input_container.set_safety(profile.safety)
            self._chat_input_container.set_agent_name(profile.display_name.lower())
            self._chat_input_container.set_custom_border(None)

    def _request_next_agent(self) -> None:
        base = (
            self._desired_agent
            if self._agent_switch_active and self._desired_agent is not None
            else self.app_server.resources.agents.active.name
        )
        target = self.app_server.resources.agents.next(base)
        self._desired_agent = target.name
        self._update_profile_widgets(target)
        if self._chat_input_container:
            self._chat_input_container.set_switching_mode(True, show_indicator=False)
        if not self._agent_switch_active:
            self._agent_switch_active = True
            self.run_worker(
                self._drain_agent_switches(), group="mode_switch", exclusive=True
            )

    async def _drain_agent_switches(self) -> None:
        applied: str | None = None
        try:
            while (target := self._desired_agent) is not None and target != applied:
                try:
                    await self._switch_to_agent(target)
                except Exception as exc:
                    logger.error("Agent switch to %s failed", target, exc_info=exc)
                applied = target
        finally:
            self._agent_switch_active = False
            if self._chat_input_container:
                self._chat_input_container.switching_mode = False

    async def _switch_to_agent(self, target: str) -> None:
        spinner_timer = self.set_timer(
            MODE_SWITCH_SPINNER_DELAY, self._show_switch_spinner
        )
        try:
            await self.app_server.resources.agents.switch(target)
        finally:
            spinner_timer.stop()
        self._refresh_banner()

    def _show_switch_spinner(self) -> None:
        if self._chat_input_container and self._agent_switch_active:
            self._chat_input_container.set_switching_mode(True, show_indicator=True)

    async def action_toggle_debug_console(self, **kwargs: Any) -> None:
        if self._app_server is None:
            return
        if self._debug_console is not None:
            await self._debug_console.remove()
            self._debug_console = None
        else:
            self._debug_console = DebugConsole(
                log_source=self.app_server.resources.runtime
            )
            await self.mount(self._debug_console)

    def _get_chat_input(self) -> ChatInputContainer | None:
        input_widgets = self.query(ChatInputContainer)
        if input_widgets:
            return input_widgets.first()
        return None

    def action_interrupt_or_quit(self) -> None:
        # Ctrl+C priority ladder: clear input → second-press quit → bottom-app/voice/etc
        # no-op steps → pop last queued item (LIFO) → cancel running job → request quit.
        if self._app_server is None:
            self._force_quit()
            return
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
        if self._app_server is None:
            self._force_quit()
            return
        if (container := self._get_chat_input()) and container.value:
            if container.input_widget:
                container.input_widget.action_delete_right()
            return

        if not self.config.ask_confirmation_on_exit:
            self._force_quit()
            return

        if self._quit_manager.is_confirmed("Ctrl+D"):
            self._force_quit()
            return
        self._quit_manager.request_confirmation(
            "Ctrl+D", self._queue.quit_warning_extra()
        )

    async def _begin_shutdown(self) -> None:
        await self._queue.shutdown()
        await self._side_channel.shutdown()

    def _force_quit(self) -> None:
        if self._force_quit_task is not None and not self._force_quit_task.done():
            return
        self._force_quit_task = asyncio.create_task(self._force_quit_async())

    async def _force_quit_async(self) -> None:
        try:
            await self._begin_shutdown()
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            if self._bash_task and not self._bash_task.done():
                self._bash_task.cancel()
            self._narrator_manager.cancel()
        finally:
            self.exit(result=self._get_session_exit_summary())

    async def shutdown_cleanup(self) -> None:
        with suppress(Exception):
            await self._begin_shutdown()
        for task in (self._agent_task, self._bash_task):
            if task is None or task.done():
                continue
            task.cancel()
        for task in (self._agent_task, self._bash_task):
            if task is None or task.done():
                continue
            with suppress(asyncio.CancelledError, Exception):
                await task
        if self._client_dependencies_ready:
            with suppress(Exception):
                await self._voice_manager.close()
            with suppress(Exception):
                await self._narrator_manager.close()
        if self._app_server is not None:
            with suppress(Exception):
                await self._app_server.close()

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

    async def _show_untrusted_config_warning(self) -> None:
        try:
            response = await self.app_server.resources.workspace.untrusted_config_dirs()
            if not response.dirs:
                return
            cache = FileSystemCacheStore()
            section = await asyncio.to_thread(
                cache.read_section, _UNTRUSTED_CONFIG_WARNING_SECTION
            )
            acknowledged = section.get("dirs")
            acknowledged = (
                set(acknowledged) if isinstance(acknowledged, list) else set()
            )
            # A folder can be intentionally left untrusted; warn once per folder
            # instead of nagging on every launch, but still surface new ones.
            if set(response.dirs) <= acknowledged:
                return
            folders = "\n".join(f"  • {d}" for d in response.dirs)
            warning = (
                "⚠ Untrusted local config folders are being ignored:\n\n"
                f"{folders}\n\n"
                f'If you want them loaded, remove them from "untrusted" in '
                f"{response.settings_path}, or ask Vibe to do it."
            )
            await self._mount_and_scroll(WarningMessage(warning, show_border=False))
            await asyncio.to_thread(
                cache.write_section,
                _UNTRUSTED_CONFIG_WARNING_SECTION,
                {"dirs": sorted(acknowledged | set(response.dirs))},
            )
        except Exception:
            logger.warning(
                "Failed to check for untrusted config folders", exc_info=True
            )

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

        should_show = await should_show_whats_new(
            self._current_version, self._update_cache_repository
        )
        if not should_show:
            await self._maybe_show_vscode_extension_promo()
            return

        content = load_whats_new_content()
        if content is not None:
            body = content
            plan_offer = plan_offer_cta(self.app_server.resources.account.current)
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

    async def _show_greeting_message(self) -> None:
        if not await self._should_show_greeting():
            return
        username = self._username()
        if username is None:
            return
        greeting_message = GreetingMessage(username)
        chat = self._chat_widget
        # Mount after banner so it appears below banner and scrolls up with messages
        await chat.mount(greeting_message, after=self._banner)
        self._greeting_message = greeting_message
        await self._mark_greeting_shown()

    async def _show_custom_tools_deprecation_warning_after_initial_history(
        self,
    ) -> None:
        await self._initial_history_loaded.wait()
        await self._show_custom_tools_deprecation_warning()

    async def _show_custom_tools_deprecation_warning(self) -> None:
        async with self._custom_tools_deprecation_message_lock:
            if self._custom_tools_deprecation_message is not None:
                return
            tool_names = [
                tool.name
                for tool in self.app_server.resources.runtime.tools
                if tool.is_custom
            ]
            if not tool_names:
                return
            message = CustomToolsDeprecationMessage(tool_names)
            await self._mount_and_scroll(message)
            self._custom_tools_deprecation_message = message

    def _sync_greeting_message(self) -> None:
        # Config edit can turn show_greeting off mid-session: tear down an
        # already-mounted greeting so the change takes effect immediately.
        if self.config.show_greeting or self._greeting_message is None:
            return
        greeting = self._greeting_message
        self._greeting_message = None
        if greeting.parent:
            greeting.remove()

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

    async def _refresh_account(self) -> None:
        try:
            await self.app_server.resources.account.read()
        except Exception as exc:
            logger.warning(
                "Account check failed (%s).", type(exc).__name__, exc_info=exc
            )
        finally:
            self._refresh_command_registry()
            self._refresh_banner()

    async def _refresh_identity(self) -> None:
        try:
            await self.app_server.resources.identity.read()
        except Exception as exc:
            logger.warning(
                "Identity check failed (%s).", type(exc).__name__, exc_info=exc
            )
        finally:
            self._refresh_banner()

    async def _mount_and_scroll(
        self,
        widget: Widget,
        after: Widget | None = None,
        before: Widget | None = None,
        *,
        container: Widget | None = None,
    ) -> None:
        messages_area = self._messages_area
        is_user_initiated = isinstance(widget, (UserMessage, UserCommandMessage))
        should_anchor = is_user_initiated or self._chat_widget.is_at_bottom

        pin_anchor: Widget | None = None
        if after is None and container is None:
            pin_anchor = self._queue.pin_target(messages_area)

        before_parent = before.parent if before is not None else None
        after_parent = after.parent if after is not None else None
        with self.batch_update():
            if isinstance(before_parent, Widget):
                await before_parent.mount(widget, before=before)
            elif isinstance(after_parent, Widget):
                await after_parent.mount(widget, after=after)
            elif container is not None:
                await container.mount(widget)
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
        has_backfill = sync_backfill_state(
            history=self.app_server.history,
            messages_children=list(messages_area.children),
            history_widget_indices=self._history_widget_indices,
            windowing=self._windowing,
        )
        await self._load_more.set_visible(
            messages_area,
            visible=has_backfill
            or self.app_server.resources.sessions.history_before_cursor is not None,
            remaining=self._history_backfill_remaining,
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

    def _clipboard_notice_message(self, copy_result: ClipboardCopyResult) -> str:
        if copy_result.verified:
            return "Copied to clipboard"
        return f"Copied · {NATIVE_COPY_HINT}"

    def on_chat_input_body_inline_notice_requested(
        self, event: ChatInputBody.InlineNoticeRequested
    ) -> None:
        self._inline_notice.show(event.message, timeout=event.timeout)

    def on_chat_input_body_inline_notice_cleared(
        self, event: ChatInputBody.InlineNoticeCleared
    ) -> None:
        self._inline_notice.hide()

    def action_copy_selection(self) -> None:
        copy_result = copy_selection_to_clipboard(self, show_toast=False)
        if copy_result is None:
            return
        self._inline_notice.show(self._clipboard_notice_message(copy_result))
        if self._app_server is not None:
            self.app_server.resources.telemetry.record(
                "vibe.user_copied_text", {"text_length": len(copy_result.text)}
            )

    def on_mouse_up(self, event: MouseUp) -> None:
        if self._app_server is None or not self.config.autocopy_to_clipboard:
            return
        copy_result = copy_selection_to_clipboard(self, show_toast=False)
        if copy_result is None:
            return
        self._inline_notice.show(self._clipboard_notice_message(copy_result))
        self.app_server.resources.telemetry.record(
            "vibe.user_copied_text", {"text_length": len(copy_result.text)}
        )

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

    def _make_default_narrator_manager(self) -> NarratorManagerPort:
        return create_default_narrator_manager(
            config_getter=lambda: self.config,
            summary_generator=self.app_server.resources.narration,
            telemetry_client=self.app_server.resources.telemetry,
            request_metadata_getter=self._get_audio_request_metadata,
        )

    def _handle_exception(self, error: Exception) -> None:
        if not isinstance(error, WorkerFailed):
            capture_sentry_exception(
                error, fatal=True, tags={"vibe_boundary": "textual_app"}
            )
        return super()._handle_exception(error)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        error = event.worker.error
        if event.state == WorkerState.ERROR and error:
            capture_sentry_exception(
                error,
                fatal=False,
                tags={
                    "vibe_boundary": "textual_worker",
                    "worker_name": event.worker.name or "",
                },
            )


async def _run_app_with_cleanup(app: VibeApp) -> SessionExitSummary | None:
    from vibe.cli.stderr_guard import stderr_guard

    loop = asyncio.get_running_loop()
    if not WINDOWS:
        try:

            def _sigterm_handler() -> None:
                loop.remove_signal_handler(signal.SIGTERM)
                app._force_quit()

            loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)
        except (NotImplementedError, OSError):
            pass

    try:
        with stderr_guard():
            return await app.run_async()
    finally:
        if not WINDOWS:
            try:
                loop.remove_signal_handler(signal.SIGTERM)
            except (NotImplementedError, OSError):
                pass
        sys.stderr.write("Closing\u2026\r")
        sys.stderr.flush()
        try:
            await app.shutdown_cleanup()
        finally:
            sys.stderr.write("\033[2K\r")
            sys.stderr.flush()


def run_textual_ui(
    start_app_server: AppServerBootstrap,
    history_file: Path,
    update_cache_repository: UpdateCacheRepository,
    startup: StartupOptions | None = None,
) -> SessionExitSummary | None:
    resolve_auto_theme()

    async def run() -> SessionExitSummary | None:
        app_server = await start_app_server()
        effective_startup = startup or StartupOptions()
        update_notifier = PyPIUpdateGateway(project_name="mistral-vibe")
        vscode_extension_promo_repository = FileSystemVscodeExtensionPromoRepository()
        vscode_extension_promo = VscodeExtensionPromo(
            repository=vscode_extension_promo_repository,
            initial_state=await vscode_extension_promo_repository.get(),
        )
        if isinstance(app_server, AppServerHost):
            from vibe.cli.textual_ui.startup import (
                _execute_session_open_plan,
                resolve_session_open_plan,
            )

            host = app_server
            plan = await resolve_session_open_plan(
                host,
                prompt_for_workspace_trust=effective_startup.prompt_for_workspace_trust,
                show_resume_picker=effective_startup.show_resume_picker,
                initially_resuming=effective_startup.is_resuming_session,
                resume_session_id=effective_startup.resume_session_id,
            )
            if plan is None:
                return None
            effective_startup = replace(
                effective_startup,
                show_resume_picker=(
                    effective_startup.show_resume_picker and not plan.resumed
                ),
                is_resuming_session=plan.resumed,
                prompt_for_workspace_trust=False,
                startup_show_resume_picker=plan.showed_resume_picker,
                startup_prompt_for_workspace_trust=plan.showed_trust_prompt,
                resume_session_id=plan.resume_session_id,
                continue_latest=plan.continue_latest,
            )

            async def _session_starter() -> AppServerSession:
                try:
                    return await _execute_session_open_plan(host, plan)
                except BaseException:
                    with suppress(BaseException):
                        await host.close()
                    raise

            initial_config_response = await host.read_config()
            app = VibeApp(
                app_server=_session_starter,
                history_file=history_file,
                startup=effective_startup,
                update_notifier=update_notifier,
                update_cache_repository=update_cache_repository,
                vscode_extension_promo=vscode_extension_promo,
            )
            app._initial_config_response = initial_config_response
            app._mount_first = True
        else:
            app = VibeApp(
                app_server=app_server,
                history_file=history_file,
                startup=effective_startup,
                update_notifier=update_notifier,
                update_cache_repository=update_cache_repository,
                vscode_extension_promo=vscode_extension_promo,
            )
        return await _run_app_with_cleanup(app)

    return asyncio.run(run())
