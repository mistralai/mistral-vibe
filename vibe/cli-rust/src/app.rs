//! UI state; the `ui` module renders a pure view over it.

use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::post_ready::AccountReads;
use crate::server::{UserQuestion, UserQuestionRequest};
use ratatui::layout::Rect;
use ratatui::Frame;
use serde_json::Value;
use tokio::sync::mpsc::Sender;

use crate::cli::StartupResume;
use crate::commands::CommandEvent;
use crate::completion_manager::CompletionEntry;
use crate::config::ConfigField;
use crate::config_edit::ConfigEdit;
use crate::feedback::Feedback;
use crate::message_queue::QueueController;
use crate::selection::{
    BottomBarSelection, ClickChain, Granularity, Region, RegionId, RowSpan, ScrollTarget,
    TableCellHit, TableCellSelection,
};
use crate::transcript::Transcript;
use crate::trust_folders::TrustFolders;
use crate::turn_summary::Narrator;
use crate::ui;
use crate::ui::banner::Banner;
use crate::ui::loading::LoadingAnim;
use crate::utils::file_index::FileIndex;
use crate::utils::history_manager::HistoryManager;
use crate::utils::startup_cache::StartupConfig;
use crate::utils::transcript_cache::TranscriptCache;
use crate::voice::{Recording, TranscribeState, TranscriptionConfig, VoiceEvent};

#[derive(Clone, Copy, PartialEq, Default)]
pub enum Status {
    #[default]
    Starting,
    Failed,
    Ready,
    /// A turn is running since `since` (drives the elapsed counter).
    Generating {
        since: Instant,
    },
}

/// A mouse selection in region-document cells (anchor = mouse-down, head = last drag).
#[derive(Clone)]
pub struct Selection {
    pub owner: RegionId,
    pub anchor: (u16, i32),
    pub head: (u16, i32),
    /// Set on release: the next render extracts the selected text and copies it.
    pub pending_copy: bool,
    /// Signed scroll speed while the drag stays near a scrollable-region edge.
    pub edge_scroll: i8,
    /// Scrolling document captured when the gesture began.
    pub scroll_target: ScrollTarget,
    /// Logical markdown table-cell range owning this gesture, if any.
    pub table_cell: Option<TableCellSelection>,
    /// Selected text cached at paint time so a copy key can read it without the
    /// frame buffer.
    pub text: String,
}

/// The surface owning a mouse selection; each maps screen cells its own way.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Surface {
    /// A selectable rendered region, identified independently from its mouse target.
    Region(RegionId),
    Composer,
    /// The bottom bar (working directory + PID), a single selectable row.
    BottomBar,
}

/// Mouse text-selection state, shared by both surfaces (Python `WordSelectScreen`
/// for the transcript and `ChatTextArea`'s own multi-click for the composer).
#[derive(Default)]
pub struct SelectionState {
    /// Multi-click chain deciding `granularity` (Python `_update_click_chain`).
    pub chain: ClickChain,
    /// Granularity of the gesture in flight: char, word, or paragraph.
    pub granularity: Granularity,
    /// Surface owning the drag, or `None` while no button is down.
    pub drag: Option<Surface>,
    /// Active region selection in document cells, or `None`.
    pub region: Option<Selection>,
    /// Active bottom-bar selection (screen columns on the bar row), or `None`.
    pub bottom_bar: Option<BottomBarSelection>,
    /// Composer drag anchor (byte offset), kept so word/paragraph drags snap.
    pub composer_anchor: Option<usize>,
    /// Screen cell of the last press, so a drag over chrome does not toggle it.
    pub press: Option<(u16, u16)>,
    /// Whether the pointer moved since the press (a drag, not a click).
    pub dragged: bool,
}

/// An inline status line (Python `InlineNotice`), shown until `until`; `None`
/// pins it until something clears it, like Python's `timeout=None`.
pub struct Notice {
    pub text: String,
    pub severity: ToastSeverity,
    pub until: Option<Instant>,
}

/// Toast severity, selecting the left-border color (Python `App.notify` severity).
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ToastSeverity {
    Information,
    Warning,
    Error,
}

/// A short-lived warning/error toast overlay (Python `App.notify` → `Toast`).
pub struct Toast {
    /// Identity within this process, so a selection can follow the toast it was
    /// anchored in while the rack shifts it around.
    pub id: u64,
    pub text: String,
    pub severity: ToastSeverity,
    pub duration: Duration,
    pub until: Option<Instant>,
    /// Evictions reported with this toast at overflow time.
    pub omitted: usize,
}

/// A prompt the client still owns end to end, used by the `/clear <prompt>` seed.
pub struct QueuedPrompt {
    pub text: String,
    pub message_id: String,
}

/// Text chat input state: the input buffer, caret, keyboard selection, and history.
#[derive(Default)]
pub struct ChatInput {
    pub input: String,
    pub mode: crate::input_modes::InputMode,
    /// Caret position as a byte offset into `input` (always on a char boundary),
    /// mirroring Textual's TextArea cursor. Keyboard editing moves and edits here.
    pub cursor: usize,
    /// The fixed end of a keyboard text selection (byte offset), or `None` when
    /// nothing is selected. The selection spans `anchor..cursor`; a Shift-move
    /// sets it, a plain move clears it (Textual's TextArea selection).
    pub anchor: Option<usize>,
    /// Explicit composer viewport top row. `None` follows the caret automatically.
    pub scroll: Option<u16>,
    /// Caret byte offset right after a history entry was loaded, or `None` when the
    /// chat input does not hold a recalled entry (Textual's `_cursor_pos_after_load`).
    pub cursor_pos_after_load: Option<usize>,
    /// Whether the caret has moved since a history entry was loaded (Textual's
    /// `_cursor_moved_since_load`). Once moved, Up/Down edit text instead of history.
    pub cursor_moved_since_load: bool,
    /// Input history recalled with Up/Down, persisted to `$VIBE_HOME/vibehistory`.
    pub history: HistoryManager,
    /// When the last key reached the chat input, so a bottom-app that wants to
    /// replace it can wait for a pause first (Textual `_last_keystroke_time`).
    pub last_keystroke: Option<Instant>,
}

impl ChatInput {
    pub fn normalize_positions(&mut self) {
        crate::chat_input::normalize_positions(&self.input, &mut self.cursor, &mut self.anchor);
    }
}

/// Session/turn lifecycle plus the startup projection shown before `ready`.
#[derive(Default)]
pub struct Session {
    pub session_id: Option<String>,
    /// The in-progress turn's id (from `turn/started`), needed to interrupt it.
    pub active_turn_id: Option<String>,
    /// A manual shell command submitted before startup became ready.
    pub pending_shell: Option<crate::commands::shell::PendingShell>,
    /// The in-progress manual shell operation, needed to reject queued input.
    pub shell_operation_id: Option<String>,
    /// When the manual shell operation started, for the loader elapsed time.
    pub shell_started_at: Option<Instant>,
    /// Whether the shell run frame has been handed to the app-server writer.
    pub shell_request_started: bool,
    /// An interrupt pressed before the run frame was sent, replayed once it is safe.
    pub shell_interrupt_requested: bool,
    pub status: Status,
    /// Working directory resolved once at startup; sent on `session/start` and shown in the footer.
    pub cwd: Option<String>,
    /// Context budget `(current_tokens, max_tokens)`; `(_, 0)` renders as empty.
    pub tokens: (u64, u64),
    pub stats: AgentStats,
    pub startup_config: StartupConfig,
    pub startup_error: Option<String>,
    /// The `--continue`/`--resume` intent, consumed once after `ready`.
    pub startup_resume: StartupResume,
    /// The interactive CLI flags (Python `_session_options`), resent on every resume.
    pub agent_config: crate::server::AgentConfig,
    /// The positional `vibe <prompt>` argument (Python `_initial_prompt`), sent
    /// once startup has converged.
    pub initial_prompt: Option<String>,
    /// A `session/resume` has landed, so the handshake's own session is stale.
    pub resumed: bool,
    /// Whether the last turn was interrupted, so `/retry` can continue it
    /// (Python `_retry_presentation is not None`).
    pub can_retry: bool,
    /// The active turn's latest assistant row (Python `_turn_assistant_message`).
    pub turn_assistant_id: Option<String>,
    /// Python `_RetryPresentation`: armed on retryable failures.
    pub retry_presentation: Option<crate::commands::retry::RetryPresentation>,
    /// A retried turn's output merging into the interrupted assistant row.
    pub retry_continuation: Option<crate::commands::retry::RetryContinuation>,
    /// Silent incomplete_stream continuations spent (Python `_incomplete_stream_retries`).
    pub incomplete_stream_retries: u32,
    /// The mounted custom-tools deprecation entry's id, Python's message guard.
    pub custom_tools_deprecation_id: Option<String>,
    /// The mounted what's-new entry's id, dropped on the first submit like
    /// Python's `_whats_new_message`.
    pub whats_new_id: Option<String>,
    /// Token usage snapshot at attach (Python `reset_usage_baseline`); the
    /// exit summary reports the delta against it.
    pub usage_baseline: Option<crate::server::TokenUsage>,
    /// The latest `runtime` value, so a transcript rebuild can re-run the
    /// checks that read it (Python re-shows the deprecation on its rebuild).
    pub runtime: Value,
}

/// Runtime token and cost totals rendered by `/status`.
#[derive(Default)]
pub struct AgentStats {
    pub steps: u64,
    pub session_prompt_tokens: u64,
    pub session_completion_tokens: u64,
    pub session_cached_tokens: u64,
    pub last_turn_total_tokens: u64,
    pub last_turn_cached_tokens: u64,
    pub session_cost: f64,
}

/// Transcript rendering state: content, scroll, per-entry caches, and chrome.
pub struct View {
    pub transcript: Transcript,
    /// Manual scroll-up offset; `0` pins to the newest content. Animated toward `scroll_target`.
    pub scroll: u16,
    /// Desired scroll-up offset; the rendered `scroll` eases toward this each frame.
    pub scroll_target: u16,
    /// Document height of the last overflowing frame; growth is added to the
    /// scroll offset while scrolled up, so streaming never moves the viewport.
    pub last_total: u16,
    /// Per-entry render-height cache, so scrolling and streaming don't re-wrap the whole transcript.
    pub transcript_cache: TranscriptCache,
    /// Bounded prepared Markdown retained across redraws for stable assistant entries.
    pub markdown_cache: ui::markdown::MarkdownCache,
    /// Effect entry ids expanded by a click, showing their result body (Python `CollapsibleSection`).
    /// Folded group keys live here too, as `group:<first member id>`.
    pub expanded: HashSet<String>,
    /// Python `_tools_collapsed`: the Ctrl+O bulk fold state applied to every
    /// group and collapsible result body at once.
    pub tools_collapsed: bool,
    /// Per-frame hit map `(top, bottom, id)` in screen rows, used to route a click to its entry.
    pub entry_hitmap: Vec<(u16, u16, String)>,
    /// Markdown links painted in the current frame, used for hover and click routing.
    pub link_hitmap: Vec<ui::markdown::Link>,
    /// Per-frame `(top, bottom, gutter_width)` of every edit-diff view on screen.
    /// Its gutter is chrome, so selection and copy start after it (Python
    /// `DiffView._gutter_width`).
    pub diff_hitmap: Vec<(u16, u16, u16)>,
    /// Markdown table cells from the current layout, in document coordinates.
    pub table_hitmap: Vec<TableCellHit>,
    /// Latest terminal mouse position, used to paint markdown-link hover state.
    pub mouse_position: Option<(u16, u16)>,
    /// Pointer shape last sent to the terminal, so hover only writes on change.
    pub pointer_shape: crate::pointer::Shape,
    /// The selectable region painted last frame, published by the visible screen.
    pub selection_region: Region,
    /// Text rectangle of each toast painted last frame, keyed by toast id and
    /// ordered newest last. Bounded by the stack the rack fits on screen.
    pub toast_text_areas: Vec<(u64, Rect)>,
    /// The toast text region a selection is anchored in, or the default region
    /// while no toast owns one.
    pub toast_selection_region: Region,
    /// The loading-area row a drag can select, published by whichever screen
    /// paints it (Python's loading widgets are selectable Statics).
    pub loading_selection_region: Region,
    /// The question box content a drag can select while a question is pending.
    pub question_selection_region: Region,
    /// Question-box `(y, x0, x1)` cells painted as option prefixes (cursor,
    /// numbering, checkbox), so a selection never highlights or copies them.
    pub question_selection_chrome: Vec<RowSpan>,
    /// Inclusive `(y, x0, x1)` cells inside the region that belong to no widget,
    /// so Textual never selects them: padding around and between widgets.
    pub selection_chrome: Vec<(u16, u16, u16)>,
    /// Last rendered selectable-region scrollbar geometry for edge auto-scroll.
    pub selection_scrollbar: ui::scrollbar::State,
    /// Current chat input box, used to map mouse drags to the editor document.
    pub input_area: Rect,
    /// Mouse regions from the latest frame, bounded by the painted UI.
    pub mouse_regions: Vec<crate::mouse::MouseRegion>,
    /// Captured mouse gesture and scrollbar state.
    pub mouse: crate::mouse::MouseState,
    pub banner: Banner,
    /// The VS Code extension promo body (Python `VscodeExtensionPromoMessage`),
    /// mounted before the messages area, so it renders under the banner.
    pub promo: Option<String>,
    /// Spinner glyph + label gradient state, ticked every 100ms while thinking.
    pub loading: LoadingAnim,
    /// A background slash command is fetching, so the loading area shows a
    /// hint-free spinner (Python mounts `LoadingWidget(status="Loading")`).
    pub command_loading: bool,
    /// Shared phase for the 100 ms pulse on active reasoning and tool entries.
    pub pulse_frame: usize,
    /// Current phase of the software block cursor's blink (toggled every 0.5s).
    pub cursor_on: bool,
    /// Whether the terminal window holds focus (Python `App.app_focus`); the
    /// caret stops blinking and stays dark while it does not.
    pub app_focus: bool,
    /// Entry index the next frame must bring to the top of the viewport
    /// (Python `scroll_to_widget(..., top=True)`), consumed by the render.
    pub scroll_to_entry: Option<usize>,
}

impl Default for View {
    fn default() -> Self {
        let mut loading = LoadingAnim::default();
        loading.set_label(ui::loading::INITIALIZING_LOADING_STATUS);
        Self {
            transcript: Transcript::default(),
            scroll: 0,
            scroll_target: 0,
            last_total: 0,
            transcript_cache: TranscriptCache::default(),
            markdown_cache: ui::markdown::MarkdownCache::default(),
            expanded: HashSet::new(),
            tools_collapsed: true,
            entry_hitmap: Vec::new(),
            link_hitmap: Vec::new(),
            diff_hitmap: Vec::new(),
            table_hitmap: Vec::new(),
            mouse_position: None,
            pointer_shape: crate::pointer::Shape::default(),
            selection_region: Region::default(),
            toast_text_areas: Vec::new(),
            toast_selection_region: Region::default(),
            loading_selection_region: Region::default(),
            question_selection_region: Region::default(),
            question_selection_chrome: Vec::new(),
            selection_chrome: Vec::new(),
            selection_scrollbar: ui::scrollbar::State::default(),
            input_area: Rect::default(),
            mouse_regions: Vec::new(),
            mouse: crate::mouse::MouseState::default(),
            banner: Banner::default(),
            promo: None,
            loading,
            command_loading: false,
            pulse_frame: 0,
            cursor_on: true,
            app_focus: true,
            scroll_to_entry: None,
        }
    }
}

/// Transient overlays: inline notice, toast, and quit confirm.
#[derive(Default)]
pub struct Overlays {
    /// Active inline notice (e.g. "Copied to clipboard"), self-hiding after its timeout.
    pub notice: Option<Notice>,
    /// Stacked warning/error toasts (Python `App.notify` → `ToastRack`), each self-hiding.
    pub toasts: VecDeque<Toast>,
    /// Identity handed to the next toast; only ever moves forward.
    next_toast_id: u64,
    /// When the last Ctrl+C armed quit confirmation on empty input (Python `QuitManager`).
    pub quit_pending: Option<Instant>,
    /// When the last unhandled Escape landed, so a second one within
    /// `DOUBLE_ESC_DELAY` clears the input or enters rewind mode (Python
    /// `App._last_escape_time`).
    pub last_escape: Option<Instant>,
}

/// Shared `/` and `@` completion popup state.
#[derive(Default)]
pub struct Completion {
    /// User-invocable skills `(name, description)`, shown as `/name` popup rows.
    pub skills: Vec<(String, String)>,
    /// Files below `cwd`, maintained by a background watcher.
    pub files: FileIndex,
    /// Entries currently displayed by the shared `/` and `@` popup.
    pub entries: Vec<CompletionEntry>,
    /// Selected entry index in the completion popup (Up/Down, wrap-around).
    pub selected: usize,
    /// Top visual-line offset of the popup viewport, set by wheel/finger scroll.
    pub scroll: usize,
    /// Set on keyboard selection: the next render scrolls just enough to reveal it.
    pub reveal: bool,
    /// Esc hides the current popup until the input changes again.
    pub dismissed: bool,
}

/// Voice-mode state: config projection, recording lifecycle, and mic level.
#[derive(Default)]
pub struct Voice {
    /// Whether voice mode is on (Python `voice_mode_enabled`); gates Ctrl+R.
    pub mode_enabled: bool,
    /// Projected transcription config, `None` until `config/read` returns.
    pub transcription: Option<TranscriptionConfig>,
    /// Current recording lifecycle (Python `TranscribeState`).
    pub transcribe_state: TranscribeState,
    /// Live mic level in `[0,1]`, written by the audio thread, read by the indicator.
    pub peak: Arc<AtomicU32>,
    /// The active recording handle, or `None` when idle.
    pub recording: Option<Recording>,
    /// Sender the recording pipeline uses to push transcript/state updates to the UI.
    pub tx: Option<Sender<VoiceEvent>>,
    /// Frame counter driving the flushing-indicator animation (advanced per voice tick).
    pub frame: usize,
}

/// `/config` settings screen state (Python's `ConfigScreen` modal).
#[derive(Default)]
pub struct ConfigScreen {
    /// While set, the settings rows overlay the chat and Esc closes it.
    pub open: bool,
    /// The initial `config/fields/read` request is in flight.
    pub loading: bool,
    /// Field rows shown by the config screen, from `config/fields/read`.
    pub fields: Vec<ConfigField>,
    /// Writable persistence layers from `config/fields/read`.
    pub targets: Vec<String>,
    /// Highlighted field index in the config screen (Up/Down navigation).
    pub selected: usize,
    /// Top line offset of the option list, reconciled to keep the selection visible.
    pub scroll: usize,
    /// Mouse-wheel scrolling temporarily detaches the viewport from the highlight.
    pub free_scroll: bool,
    /// Free-text filter owned by the settings modal.
    pub query: String,
    /// Full terminal area from the most recent config render, used for mouse hit testing.
    pub area: Rect,
    /// Nested editor opened by Enter on a writable settings field.
    pub edit: Option<ConfigEdit>,
}

/// `/theme` picker state (Python's `ThemePickerApp` bottom-app).
#[derive(Default)]
pub struct ThemePicker {
    /// While set the picker replaces the input box: title, option list, hint.
    pub open: bool,
    /// Highlighted option index (0 = `auto`, then `theme::ALL`).
    pub selected: usize,
    /// Top line offset of the picker option list (Textual `scroll_to_highlight`).
    pub scroll: usize,
    /// Scrollbar dragging temporarily detaches the viewport from the highlight.
    pub free_scroll: bool,
    /// Option index carrying the current configured name, including `auto`.
    pub current: usize,
    /// `theme::ALL` index to restore if the picker is cancelled (Esc).
    pub original: usize,
    /// Deadline of the pending debounced preview (Python `PREVIEW_DEBOUNCE_SECONDS`).
    pub preview_at: Option<Instant>,
    /// Sender carrying the theme returned by a `config/write` response, so the
    /// commit re-applies the server's config (Python re-applies the written runtime).
    pub tx: Option<Sender<crate::theme_picker::Event>>,
}

/// Agent modes: the runtime snapshot plus the Shift+Tab switch in flight
/// (Python's `AgentResource` and `VibeApp._desired_agent`).
#[derive(Default)]
pub struct Agents {
    /// Every agent from the runtime snapshot, in the server's discovery order.
    pub all: Vec<crate::server::AgentSummary>,
    /// The server's active agent (Python `resources.agents.active`).
    pub active: crate::server::AgentSummary,
    /// Session-wide auto-approve (`--yolo` or config), which survives a switch,
    /// as opposed to the YOLO agent's own (Python `_force_bypass_tool_permissions`).
    pub force_bypass_tool_permissions: bool,
    /// Name the last Shift+Tab asked for, painted before the server answers
    /// and re-used as the base of a coalesced press (Python `_desired_agent`).
    pub desired: Option<String>,
    /// Whether a `session/agent/update` is in flight (Python `_agent_switch_active`).
    pub switch_active: bool,
    /// When the prompt spinner is due, or `None` once fired or cancelled.
    pub spinner_at: Option<Instant>,
    /// Whether the spinner has replaced the prompt marker (Python `switching_mode`
    /// with `show_indicator`).
    pub switching_indicator: bool,
    /// Spinner frame, advanced on the shared 100 ms tick.
    pub frame: usize,
    /// Sender carrying `session/agent/update` answers to the main thread.
    pub tx: Option<Sender<crate::agents::Event>>,
}

/// A selectable model: `alias` is persisted, `display_name` is shown (Python's `ModelOption`).
#[derive(Clone, Default)]
pub struct ModelOption {
    pub alias: String,
    pub display_name: String,
}

/// `/model` picker state (Python's `ModelPickerApp` bottom-app).
#[derive(Default)]
pub struct ModelPicker {
    /// While set the picker replaces the input box: title, option list, hint.
    pub open: bool,
    /// Highlighted option index (0 = Default row, then one per model).
    pub selected: usize,
    /// Top line offset of the picker option list (Textual `scroll_to_highlight`).
    pub scroll: usize,
    /// Scrollbar dragging temporarily detaches the viewport from the highlight.
    pub free_scroll: bool,
    /// Model snapshot from `runtime/read`, refreshed on `runtime/updated`.
    pub models: Vec<ModelOption>,
    /// The active model's alias (`activeModel.alias`).
    pub current_model: String,
    /// Whether the active model is pinned (`activeModelPinned`); unpinned = Default.
    pub is_pinned: bool,
    /// Display name of the default model, shown in the Default row hint.
    pub default_display_name: String,
    /// Sender carrying the `config/write` and `config/reload` answers, so the
    /// commit re-applies the server's config (Python `_reload_config`).
    pub tx: Option<Sender<crate::model_picker::Event>>,
}

/// `/log-level` picker state (Python's `LogLevelPickerApp` bottom-app).
#[derive(Default)]
pub struct LogLevelPicker {
    /// While set the picker replaces the input box.
    pub open: bool,
    /// Highlighted level index into `LOG_LEVELS`.
    pub selected: usize,
    /// Draft session-tier level; `None` means no override.
    pub session: Option<String>,
    /// Draft config-tier level; `None` means the field is unset.
    pub config: Option<String>,
    /// Which badge Enter toggles: `session` or `config`.
    pub focused_badge: &'static str,
    /// The chain as it stood when the picker opened, for the diff on apply.
    pub chain: crate::observability::level::LogLevelChain,
    /// Sender carrying the `config/write` answer.
    pub tx: Option<Sender<crate::log_level_picker::Event>>,
}

/// `/thinking` picker state (Python's `ThinkingPickerApp` bottom-app).
#[derive(Default)]
pub struct ThinkingPicker {
    /// While set the picker replaces the input box.
    pub open: bool,
    /// Highlighted level index into `THINKING_LEVELS`.
    pub selected: usize,
    /// Top line offset of the option list.
    pub scroll: usize,
    /// The active model's current thinking level (`activeModel.thinking`).
    pub current_level: String,
    /// Sender carrying the `config/write` and `config/reload` answers.
    pub tx: Option<Sender<crate::thinking_picker::Event>>,
}

/// `/resume` picker state (Python's `SessionPickerApp`).
#[derive(Default)]
pub struct ResumePicker {
    pub open: bool,
    pub sessions: Vec<crate::resume_picker::Session>,
    pub selected: usize,
    pub scroll: usize,
    /// Mouse-wheel scrolling temporarily detaches the viewport from the highlight.
    pub free_scroll: bool,
    /// Folder the listed sessions belong to, from the server-filtered list. It
    /// is the attached session's cwd too (Python reads it off the session).
    pub cwd: Option<String>,
    pub delete_confirm: Option<String>,
    /// Id of the session whose `session/delete` is in flight; keys are blocked
    /// until it answers (Python `SessionPickerApp` pending delete).
    pub deleting: Option<String>,
    /// Set once a preview replaced the transcript, so returning to the current
    /// session reloads it (Python `SessionPickerApp.previewing`).
    pub previewing: bool,
    pub preview_request: u64,
    /// A `session/resume` is in flight; the app is busy but shows no spinner.
    pub resuming: bool,
    pub transcript: Option<crate::transcript::Snapshot>,
    pub tx: Option<Sender<crate::resume_picker::Event>>,
}

/// Rewind bottom-app state (Python's `RewindApp` plus `App._rewind_mode`).
#[derive(Default)]
pub struct Rewind {
    /// While set the panel replaces the input box and owns every key.
    pub open: bool,
    /// History entry id of the highlighted user message, or `None` when none is.
    pub entry_id: Option<String>,
    /// Text of the highlighted user message, shown in the panel title.
    pub preview: String,
    /// Whether rewinding here would restore files (`session/rewind/read`).
    pub has_file_changes: bool,
    /// Which of the two option sets is shown: the action, then the persistence.
    pub step: crate::rewind::Step,
    /// The action step's answer, carried into the persistence step.
    pub restore_files: bool,
    /// Highlighted option index in the current step.
    pub selected: usize,
    /// Sender carrying server answers, applied on the main thread.
    pub tx: Option<Sender<crate::rewind::Event>>,
}

/// `ask_user_question` bottom-app state (Python's `QuestionApp`).
#[derive(Default)]
pub struct QuestionApp {
    /// While set the app replaces the input box and owns every key.
    pub open: bool,
    /// A delivered callback still waiting for a pause in the user's typing
    /// (Python `_wait_for_typing_pause`), as `(callback id, request)`.
    pub pending: Option<(String, UserQuestionRequest)>,
    /// The `user_input` callback this app answers with `callback/result`.
    pub callback_id: String,
    pub questions: Vec<UserQuestion>,
    pub footer_note: Option<String>,
    pub current_question_idx: usize,
    /// Cursor row: an option, then the free-text row, then the submit row.
    pub selected_option: usize,
    /// Scroll offset and whether the viewport follows the keyboard selection.
    pub viewport: crate::question_app::Viewport,
    /// Per-frame hit map `(row, option index)`, used to route a click to a row.
    pub option_rows: Vec<(u16, usize)>,
    /// Saved answers by question index, as `(answer, is_other)`.
    pub answers: BTreeMap<usize, (String, bool)>,
    /// Ticked option indices per multi-select question.
    pub multi_selections: BTreeMap<usize, BTreeSet<usize>>,
    /// Free-text answers per question, kept while switching questions.
    pub other_texts: BTreeMap<usize, String>,
    /// Caret byte offset into the current question's free-text answer.
    pub other_cursor: usize,
    /// When the app opened, so buffered keys cannot answer it instantly.
    pub mount_time: Option<Instant>,
    /// Screen row of the in-flight mouse press, so a same-row release can
    /// click even when the press started no selection gesture.
    pub mouse_press_row: Option<u16>,
}

/// `/mcp` browser state (Python's `MCPApp` bottom-app).
#[derive(Default)]
pub struct MCPApp {
    pub search: crate::mcp::search::Search,
    /// While set the browser replaces the input box: title, option list, hint.
    pub open: bool,
    /// Latest `mcp/read` projection, refreshed by `mcp/refresh` and `mcp/toggle`.
    pub state: crate::server::MCPState,
    /// Source whose tools are listed, or `None` in the source list view.
    pub viewing_name: Option<String>,
    pub viewing_kind: Option<crate::server::MCPSourceKind>,
    /// Highlighted row index; headers and notes are skipped when navigating.
    pub selected: usize,
    /// Top line offset of the option list (Textual `scroll_to_highlight`).
    pub scroll: usize,
    /// Mouse-wheel scrolling temporarily detaches the viewport from the highlight.
    pub free_scroll: bool,
    /// Option-list viewport from the last render, used to route mouse clicks.
    pub list_area: Rect,
    /// Row index of each laid-out visual line, so a click maps to its option.
    pub line_rows: Vec<usize>,
    /// Sender carrying server answers, applied on the main thread.
    pub tx: Option<Sender<crate::mcp::Event>>,
}

/// The identity and account reads `/whoami` renders, kept from the post-ready
/// fetch so the command answers without a round trip. Keyed on the active
/// model, since that decides which credential (if any) they describe.
#[derive(Default)]
pub struct WhoamiCache {
    model: Option<String>,
    reads: AccountReads,
}

impl WhoamiCache {
    pub fn store(&mut self, model: String, reads: AccountReads) {
        self.model = Some(model);
        self.reads = reads;
    }

    pub fn hit(&self, model: &str) -> Option<&AccountReads> {
        (self.model.as_deref() == Some(model)).then_some(&self.reads)
    }

    /// The account read the rate-limit message consults (Python
    /// `resources.account.current`), independent of the model it was read for.
    pub fn account(&self) -> &serde_json::Value {
        &self.reads.account
    }
}

/// MCP OAuth bottom-app state (Python's `MCPOAuthApp`).
#[derive(Default)]
pub struct MCPOAuthApp {
    /// While set the app replaces the input box and owns every key.
    pub open: bool,
    /// The server being authenticated, shown in the title.
    pub server_name: String,
    /// The URL the running login streamed back, or `None` before it arrives.
    pub auth_url: Option<String>,
    /// Whether "Manually show the URL" revealed it under the options.
    pub auth_url_visible: bool,
    /// Status prefixed to the help line (Python `_status_message`).
    pub status_message: Option<String>,
    /// Whether an `mcp/login` is in flight, so `R` cannot start a second one.
    pub logging_in: bool,
    /// Whether the last login failed; the list then shows the retry note.
    pub failed: bool,
    /// Highlighted action option among the three selectable rows.
    pub selected: usize,
    /// Login attempt counter, so a stale answer cannot settle a retry.
    pub generation: u64,
    /// Option-list viewport from the last render, used to route mouse clicks.
    pub list_area: Rect,
    /// Sender carrying the `mcp/login` answer, applied on the main thread.
    pub tx: Option<Sender<crate::mcp_oauth::Event>>,
}

/// Connector auth bottom-app state (Python's `ConnectorAuthApp`).
#[derive(Default)]
pub struct ConnectorAuthApp {
    /// While set the app replaces the input box and owns every key.
    pub open: bool,
    /// The connector being authenticated, shown in the title.
    pub connector_name: String,
    /// Whether `connectors/auth/read` answered; before that the list is a note.
    pub fetched: bool,
    /// The connector's auth URL, or `None` when it needs no authentication.
    pub auth_url: Option<String>,
    /// Whether "Manually show the URL" revealed it under the options.
    pub auth_url_visible: bool,
    /// Status prefixed to the help line (Python `_status_message`).
    pub status_message: Option<String>,
    /// Highlighted action option among the three selectable rows.
    pub selected: usize,
    /// Open-attempt counter, so a stale worker answer cannot land.
    pub generation: u64,
    /// Option-list viewport from the last render, used to route mouse clicks.
    pub list_area: Rect,
    /// Sender carrying the auth-read and refresh answers, applied on the main thread.
    pub tx: Option<Sender<crate::connector_auth::Event>>,
}

#[derive(Default)]
pub struct App {
    pub terminal_notifier: crate::terminal_notifier::TerminalNotifier,
    pub chat_input: ChatInput,
    pub session: Session,
    pub agents: Agents,
    pub view: View,
    pub selection: SelectionState,
    pub overlays: Overlays,
    pub completion: Completion,
    pub paste_image: crate::paste_image::State,
    pub voice: Voice,
    pub config_screen: ConfigScreen,
    pub theme_picker: ThemePicker,
    pub model_picker: ModelPicker,
    pub log_level_picker: LogLevelPicker,
    pub thinking_picker: ThinkingPicker,
    pub resume_picker: ResumePicker,
    pub vibe_code_project: crate::vibe_code_project::State,
    /// The latest written todo list (Python `TodoTracker`), never seeded from history.
    pub todo_tracker: crate::todo_tracker::TodoTracker,
    pub todo_sidebar: crate::todo_tracker::TodoSidebar,
    pub rewind: Rewind,
    pub approval: crate::approval::State,
    pub question_app: QuestionApp,
    pub mcp: MCPApp,
    pub mcp_oauth: MCPOAuthApp,
    pub connector_auth: ConnectorAuthApp,
    /// The pre-session workspace-trust gate.
    pub trust: TrustFolders,
    /// The server-gated rating prompt shown beside the loading area.
    pub feedback: Feedback,
    /// Narrator read-aloud state and its turn summary tracker.
    pub narrator: Narrator,
    /// The app server's accepted prompt queue and its selection/edit mode.
    pub queue: QueueController,
    /// Async slash-command results, reduced by the main event loop.
    pub command_tx: Option<tokio::sync::mpsc::Sender<CommandEvent>>,
    /// Identity and account reads reused by `/whoami`.
    pub whoami: WhoamiCache,
    /// Handle to the `/stress` firehose task, or `None` when it is off. A second
    /// `/stress` aborts it (mirrors Python's `_stress_task`).
    pub stress: Option<tokio::task::JoinHandle<()>>,
    /// Whether a `/compact` request is in flight, so the UI shows the spinner
    /// and the `session/compacted` notification can settle it.
    pub compacting: bool,
    /// Picker commits and slash commands whose server round-trip is still in
    /// flight. Python keeps the app busy until its queued command finishes, so
    /// the app is not idle while one is pending.
    pub pending_commits: Arc<AtomicU32>,
    /// Slash commands submitted while the session is still starting, replayed
    /// once `apply_ready` settles. Python holds dispatch on `_session_ready.wait()`.
    pub pending_commands: Vec<String>,
    /// Ctrl+Z requested suspend; the event loop performs the SIGTSTP cycle.
    pub suspend_requested: bool,
    /// App-server child exited unexpectedly (stdout EOF during steady state).
    pub server_closed: bool,
}

/// Window in which a second Ctrl+C confirms quit (Python's `QUIT_CONFIRM_DELAY`).
pub const QUIT_CONFIRM_DELAY: Duration = Duration::from_secs(1);

impl App {
    pub fn set_session_id(&mut self, session_id: String) {
        if self.session.session_id.as_deref() != Some(session_id.as_str()) {
            self.vibe_code_project = Default::default();
        }
        self.session.session_id = Some(session_id);
    }

    /// True while a Ctrl+C quit confirmation is still pending.
    pub fn quit_confirm_active(&self) -> bool {
        self.overlays
            .quit_pending
            .is_some_and(|t| t.elapsed() < QUIT_CONFIRM_DELAY)
    }

    /// Transition status; entering `Generating` resets the spinner, re-entering keeps the original start.
    pub fn set_status(&mut self, status: Status) {
        self.terminal_notifier
            .set_running(matches!(status, Status::Generating { .. }));
        match (self.session.status, status) {
            (Status::Generating { .. }, Status::Generating { .. }) => {}
            (_, Status::Generating { .. }) => {
                self.view.loading = LoadingAnim::default();
                self.session.status = status;
            }
            _ => self.session.status = status,
        }
    }

    /// `--resume`'s picker owns the screen: it opens on the session list read
    /// before `session/start`, so the startup spinner above it is only noise.
    pub fn startup_picker_active(&self) -> bool {
        matches!(self.session.startup_resume, StartupResume::Picker) || self.resume_picker.open
    }

    /// True when no turn is running and startup has settled -- the app is waiting
    /// on the user. Drives the replay-mode idle marker (see main.rs); `Failed` is
    /// terminal and counts as settled so a broken startup still emits a marker.
    pub fn is_idle(&self) -> bool {
        // The gate parks startup on the user, so its frame is already final.
        if self.trust.open {
            return true;
        }
        self.is_settled()
            && matches!(self.session.status, Status::Ready | Status::Failed)
            // An accepted prompt the server has not promoted yet is still work
            // in flight (Python `_is_busy` counts `_queue.has_server_work`).
            && self.queue.is_empty()
    }

    /// True once client-owned work has landed: startup finished, no request of
    /// ours in flight, no config screen loading, no armed theme preview (the
    /// frame is stable while its debounce runs, but the theme has not applied).
    /// A running turn does not count, so this is what a `settle_busy` capture
    /// waits for (Python's marker bypasses `_is_busy`, never `_off_thread_work`).
    pub fn is_settled(&self) -> bool {
        // An open gate counts as settled: nothing of ours is in flight.
        (self.trust.open || !matches!(self.session.status, Status::Starting))
            && (!self.config_screen.open || !self.config_screen.loading)
            && self.theme_picker.preview_at.is_none()
            && self.pending_commits.load(Ordering::Relaxed) == 0
            // A debounced theme preview repaints later; stay busy until it lands
            // so the frame matches Python (whose pending timer defers idle).
            && self.theme_picker.preview_at.is_none()
    }

    /// Mark a picker commit as in flight, keeping the app busy until its answer
    /// has been applied here on the main thread.
    pub fn commit_started(&self) -> Arc<AtomicU32> {
        self.pending_commits.fetch_add(1, Ordering::Relaxed);
        self.pending_commits.clone()
    }

    pub fn commit_finished(&self) {
        self.pending_commits.fetch_sub(1, Ordering::Relaxed);
    }

    /// Follow the terminal's focus (Python `TextArea.set_app_focus`): the caret
    /// goes dark while unfocused, and the blink tick only runs while focused.
    pub fn set_app_focus(&mut self, focused: bool) {
        self.view.app_focus = focused;
        self.view.cursor_on = focused;
    }

    /// Show the hint-free `Loading` spinner while a slash command fetches.
    pub fn begin_command_loading(&mut self) {
        if self.session.shell_operation_id.is_some() {
            return;
        }
        self.view.loading = LoadingAnim::default();
        self.view
            .loading
            .set_label(ui::loading::COMMAND_LOADING_STATUS);
        self.view.command_loading = true;
    }

    /// Render the whole UI; layout and per-region drawing live in `ui`.
    pub fn draw(&mut self, f: &mut Frame) {
        self.chat_input.normalize_positions();
        self.view.mouse_regions.clear();
        crate::mouse::register_region(self, f.area(), crate::mouse::MouseTarget::Blocked);
        ui::draw(self, f);
    }
}

/// Seconds a voice error/notice line stays up, matching the copy notice.
const VOICE_NOTICE_SECS: u64 = 4;

/// Seconds a toast stays up (Python `App.NOTIFICATION_TIMEOUT`).
const TOAST_SECS: u64 = 5;

/// Max toasts kept at once; overflow is reported with the newest toast.
const MAX_TOASTS: usize = 64;

impl App {
    /// True whenever a recording is in flight (Python `transcribe_state != IDLE`).
    pub fn recording_active(&self) -> bool {
        self.voice.transcribe_state != TranscribeState::Idle
    }

    /// Latest mic level in `[0,1]` for the recording indicator.
    pub fn current_peak(&self) -> f32 {
        f32::from_bits(self.voice.peak.load(Ordering::Relaxed))
    }

    /// Ctrl+R (Python `_handle_voice_key`): start or stop recording.
    pub fn toggle_recording(&mut self) {
        match self.voice.transcribe_state {
            TranscribeState::Idle => self.start_recording(),
            TranscribeState::Recording => self.stop_recording(),
            TranscribeState::Flushing => {}
        }
    }

    /// Ctrl+O (Python `action_toggle_tool`): flip the global fold state and
    /// apply it to every group and collapsible result body at once.
    pub fn toggle_tools(&mut self) {
        self.view.tools_collapsed = !self.view.tools_collapsed;
        let expand = !self.view.tools_collapsed;
        let mut expanded = std::mem::take(&mut self.view.expanded);
        expanded.clear();
        if expand {
            for id in self.view.transcript.expandable_ids() {
                expanded.insert(id);
            }
        }
        self.view.expanded = expanded;
        self.view.transcript_cache.invalidate_layouts();
    }

    /// Python consults `_tools_collapsed` whenever history (re)builds mount
    /// widgets (`build_history_widgets(tools_collapsed=...)`): while expanded,
    /// every rebuilt group and reasoning row mounts unfolded. Rebuilt tool
    /// results keep their constructor's collapsed default, so only group keys
    /// and reasoning ids join `expanded`. True rebuilds (resume, rewind) remount
    /// every widget fresh, so their callers clear `expanded` first; the
    /// mid-turn snapshot path keeps live widget state and stays insert-only.
    pub fn expand_rebuilt_tools(&mut self) {
        if self.view.tools_collapsed {
            return;
        }
        let mut expanded = std::mem::take(&mut self.view.expanded);
        for value in self.view.transcript.lines() {
            if let Some(group) = value.group.as_ref().filter(|group| group.first) {
                expanded.insert(group.key.clone());
            }
            if self.view.transcript.is_reasoning(value.id) {
                expanded.insert(value.id.to_owned());
            }
        }
        self.view.expanded = expanded;
        self.view.transcript_cache.invalidate_layouts();
    }

    fn start_recording(&mut self) {
        let (Some(cfg), Some(tx)) = (self.voice.transcription.clone(), self.voice.tx.clone())
        else {
            return;
        };
        match crate::voice::start(&cfg, self.voice.peak.clone(), tx) {
            Ok(rec) => {
                self.voice.recording = Some(rec);
                self.voice.transcribe_state = TranscribeState::Recording;
            }
            Err(msg) => self.show_toast(msg, ToastSeverity::Warning, TOAST_SECS),
        }
    }

    /// Signal end-of-audio; the pipeline drains and later returns to idle.
    pub fn stop_recording(&mut self) {
        if self.voice.transcribe_state != TranscribeState::Recording {
            return;
        }
        if let Some(rec) = &self.voice.recording {
            rec.stop();
        }
        self.voice.transcribe_state = TranscribeState::Flushing;
    }

    /// Abort recording without a transcript (Python `cancel_recording`).
    pub fn cancel_recording(&mut self) {
        if self.voice.transcribe_state == TranscribeState::Idle {
            return;
        }
        if let Some(rec) = self.voice.recording.take() {
            rec.cancel();
        }
        self.voice.transcribe_state = TranscribeState::Idle;
    }

    /// Reduce a pipeline update: append transcript, surface notices, settle state.
    pub fn apply_voice_event(&mut self, ev: VoiceEvent) {
        match ev {
            VoiceEvent::TextDelta(text) => self.chat_input.input.push_str(&text),
            VoiceEvent::Notice(msg) => self.show_voice_notice(msg),
            VoiceEvent::Error(msg) => self.show_toast(msg, ToastSeverity::Error, TOAST_SECS),
            VoiceEvent::Finished => {
                self.voice.recording = None;
                self.voice.transcribe_state = TranscribeState::Idle;
            }
        }
    }

    fn show_voice_notice(&mut self, text: String) {
        self.overlays.notice = Some(Notice {
            text,
            severity: ToastSeverity::Information,
            until: Some(Instant::now() + Duration::from_secs(VOICE_NOTICE_SECS)),
        });
    }

    /// Enqueue a toast for `seconds` (Python `App.notify(timeout=...)`), bounded.
    pub fn show_toast(&mut self, text: String, severity: ToastSeverity, seconds: u64) {
        let id = self.overlays.next_toast_id;
        self.overlays.next_toast_id = id.wrapping_add(1);
        self.overlays.toasts.push_back(Toast {
            id,
            text,
            severity,
            duration: Duration::from_secs(seconds),
            until: None,
            omitted: 0,
        });
        if self.overlays.toasts.len() <= MAX_TOASTS {
            return;
        }
        let mut omitted = self
            .overlays
            .toasts
            .iter()
            .map(|toast| toast.omitted)
            .fold(0usize, usize::saturating_add);
        while self.overlays.toasts.len() > MAX_TOASTS {
            self.overlays.toasts.pop_front();
            omitted = omitted.saturating_add(1);
        }
        for toast in &mut self.overlays.toasts {
            toast.omitted = 0;
        }
        if let Some(newest) = self.overlays.toasts.back_mut() {
            newest.omitted = omitted;
        }
    }
}
