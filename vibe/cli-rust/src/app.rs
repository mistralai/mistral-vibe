//! UI state; the `ui` module renders a pure view over it.

use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::post_ready::AccountReads;
use crate::server::{UserQuestion, UserQuestionRequest};
use ratatui::layout::{Position, Rect};
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
use crate::utils::transcript_cache::{QueueSpacer, TranscriptCache};
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
    /// The bottom bar (working directory, model and token budget), a single selectable row.
    BottomBar,
}

/// Mouse text-selection state, shared by both surfaces (word selection in the
/// transcript and the composer's own multi-click).
#[derive(Default)]
pub struct SelectionState {
    /// Multi-click chain deciding `granularity`.
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
    /// A selection showed when the button went down.
    pub had_selection_at_press: bool,
}

/// An inline status line, shown until `until`.
pub struct Notice {
    pub text: String,
    pub severity: ToastSeverity,
    pub until: Instant,
}

/// Toast severity, selecting the left-border color.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ToastSeverity {
    Information,
    Warning,
    Error,
}

/// A short-lived warning/error toast overlay.
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
    /// Caret position as a byte offset into `input` (always on a char boundary).
    /// Keyboard editing moves and edits here.
    pub cursor: usize,
    /// The fixed end of a keyboard text selection (byte offset), or `None` when
    /// nothing is selected. The selection spans `anchor..cursor`; a Shift-move
    /// sets it, a plain move clears it.
    pub anchor: Option<usize>,
    /// Explicit composer viewport top row. `None` follows the caret automatically.
    pub scroll: Option<u16>,
    /// Caret byte offset right after a history entry was loaded, or `None` when the
    /// chat input does not hold a recalled entry.
    pub cursor_pos_after_load: Option<usize>,
    /// Whether the caret has moved since a history entry was loaded. Once moved,
    /// Up/Down edit text instead of history.
    pub cursor_moved_since_load: bool,
    /// Input history recalled with Up/Down, persisted to `$VIBE_HOME/vibehistory`.
    pub history: HistoryManager,
    pub edit_history: crate::edit_history::EditHistory,
    /// Accepted `/skill`, `@file`, and image mentions, drawn colored and edited atomically.
    pub mentions: crate::mentions::Mentions,
    /// Pasted images behind the `[Image #N]` placeholders typed or recalled into the input.
    pub pasted_images: crate::image_placeholders::PastedImages,
    /// Long pastes collapsed in the input, so sent prompts can keep them collapsed.
    pub collapsed_pastes: crate::collapsed_pastes::CollapsedPastes,
    /// The paste just collapsed, which pasting it again shows in full until the next edit.
    pub expandable_paste: Option<std::sync::Arc<str>>,
    /// The composer as history recall found it, mentions and mode included,
    /// given back whole when Down leaves the recalled entries.
    pub recall_draft: Option<crate::edit_history::Snapshot>,
    /// When the last key reached the chat input, so a bottom-app that wants to
    /// replace it can wait for a pause first.
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
    /// Completion held while a promoted prompt awaits its local enqueue result.
    pub deferred_completion: Option<(String, crate::terminal_notifier::NotificationContext)>,
    /// A manual shell command submitted before startup became ready.
    pub pending_shell: Option<crate::commands::shell::PendingShell>,
    /// The in-progress manual shell operation, needed to reject queued input.
    pub shell_operation_id: Option<String>,
    /// When the manual shell operation started, for the loader elapsed time.
    pub shell_started_at: Option<Instant>,
    /// Whether the shell run frame has been handed to the app-server writer.
    pub shell_request_started: bool,
    /// RPC settled before the final shell effect reached the transcript.
    pub shell_rpc_completed: bool,
    /// An interrupt pressed before the run frame was sent, replayed once it is safe.
    pub shell_interrupt_requested: bool,
    pub status: Status,
    /// Working directory resolved once at startup; sent on `session/start` and shown in the footer.
    pub cwd: Option<String>,
    /// The app-server-prepared worktree the session moved into (`--worktree`),
    /// tracked from the `worktree` transcript effect; follows the footer.
    pub worktree: Option<crate::worktree::WorktreeInfo>,
    /// The worktree THIS run's `--worktree` prepared, from the move announce:
    /// a resumed session's restored effect carries the original run's
    /// `created` verdict, so the exit cleanup pins this run's own.
    pub prepared_worktree: Option<crate::worktree::WorktreeInfo>,
    /// Context budget `(current_tokens, max_tokens)`; `(_, 0)` renders as empty.
    pub tokens: (u64, u64),
    /// The session's PR/MR artifacts, latest first; the footer shows the
    /// latest as a link, or a modal listing them all when there are several.
    pub artifacts: Vec<crate::server::PublicSessionArtifact>,
    pub stats: crate::server::SessionStats,
    pub startup_config: StartupConfig,
    pub startup_error: Option<String>,
    /// The `--continue`/`--resume` intent, consumed once after `ready`.
    pub startup_resume: StartupResume,
    /// The interactive CLI flags, resent on every resume.
    pub agent_config: crate::server::AgentConfig,
    /// The positional `vibe <prompt>` argument, sent once startup has converged.
    pub initial_prompt: Option<String>,
    /// `--teleport`: teleport the initial prompt instead of sending it.
    pub teleport_on_start: bool,
    /// A `session/resume` has landed, so the handshake's own session is stale.
    pub resumed: bool,
    /// The workspace-trust gate opened during this startup.
    pub trust_prompted: bool,
    /// The `--resume` picker opened during this startup.
    pub resume_picker_shown: bool,
    /// `vibe.startup` properties held until the first frame is painted.
    pub startup_telemetry: Option<std::collections::BTreeMap<String, Value>>,
    /// Telemetry queued before the session id exists, flushed by `set_session_id`.
    pub held_telemetry: Vec<crate::telemetry::TelemetryEvent>,
    /// Whether the last turn was interrupted, so `/retry` can continue it.
    pub can_retry: bool,
    /// The active turn's latest assistant row.
    pub turn_assistant_id: Option<String>,
    /// Armed on retryable failures.
    pub retry_presentation: Option<crate::commands::retry::RetryPresentation>,
    /// A retried turn's output merging into the interrupted assistant row.
    pub retry_continuation: Option<crate::commands::retry::RetryContinuation>,
    /// Silent incomplete_stream continuations spent.
    pub incomplete_stream_retries: u32,
    /// The mounted what's-new entry, dropped on the first submit.
    pub whats_new: Option<crate::startup::banners::WhatsNew>,
    /// Token usage snapshot at attach; the exit summary reports the delta
    /// against it.
    pub usage_baseline: Option<crate::server::TokenUsage>,
    /// The latest `runtime` value, for views that read it after the event.
    pub runtime: Value,
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
    /// Unfolded entry ids and `tool-group:<first member id>` keys, including a lone call's future group.
    pub expanded: HashSet<String>,
    /// The Ctrl+O bulk fold state applied to every group and collapsible
    /// result body at once.
    pub tools_collapsed: bool,
    /// Per-frame hit map `(top, bottom, header, id)` in screen rows, used to route a click to its entry.
    pub entry_hitmap: Vec<(u16, u16, Option<u16>, String)>,
    /// Markdown links painted in the current frame, used for hover and click routing.
    pub link_hitmap: Vec<ui::markdown::Link>,
    /// Per-frame `(top, bottom, gutter_width)` of every edit-diff view on screen.
    /// Its gutter is chrome, so selection and copy start after it.
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
    /// The active model's name in the bottom bar painted last frame, which a click opens `/model` on.
    pub footer_model_area: Rect,
    /// The toast text region a selection is anchored in, or the default region
    /// while no toast owns one.
    pub toast_selection_region: Region,
    /// The loading-area row a drag can select, published by whichever screen
    /// paints it.
    pub loading_selection_region: Region,
    /// The box content a drag can select while a bottom app replaces the input box.
    pub bottom_app_selection_region: Region,
    /// Bottom-app box `(y, x0, x1)` cells painted as chrome (question option
    /// prefixes, scrollbars), so a selection never highlights or copies them.
    pub bottom_app_selection_chrome: Vec<RowSpan>,
    /// Bottom-app box rows that continue a wrapped line, so a copy rejoins them.
    pub bottom_app_selection_folds: crate::selection::Folds,
    /// The bottom app drawn last frame and its box rect, which own the box selection.
    pub bottom_app: Option<(crate::ui::bottom_app::Kind, Rect)>,
    /// The PR/MR link painted in the bottom bar last frame, for click routing.
    pub bottom_bar_link: Option<ui::bottom_bar::BarLink>,
    /// Wrapped rows of each toast painted last frame, keyed by toast id.
    pub toast_folds: Vec<(u64, crate::selection::Folds)>,
    /// Inclusive `(y, x0, x1)` cells inside the region that belong to no widget,
    /// so a drag never selects them: padding around and between widgets.
    pub selection_chrome: Vec<(u16, u16, u16)>,
    /// Hang indent cells of painted transcript rows, cut out of the highlight only.
    pub selection_hangs: Vec<(u16, u16, u16)>,
    /// Last rendered selectable-region scrollbar geometry for edge auto-scroll.
    pub selection_scrollbar: ui::scrollbar::State,
    /// Current chat input box, used to map mouse drags to the editor document.
    pub input_area: Rect,
    /// Mouse regions from the latest frame, bounded by the painted UI.
    pub mouse_regions: Vec<crate::mouse::MouseRegion>,
    /// Captured mouse gesture and scrollbar state.
    pub mouse: crate::mouse::MouseState,
    pub banner: Banner,
    /// The VS Code extension promo body, mounted before the messages area, so
    /// it renders under the banner.
    pub promo: Option<String>,
    /// Spinner glyph + label gradient state, ticked every 100ms while thinking.
    pub loading: LoadingAnim,
    /// A background slash command is fetching, so the loading area shows a
    /// hint-free `Loading` spinner.
    pub command_loading: bool,
    /// Shared phase for the 100 ms pulse on active reasoning and tool entries.
    pub pulse_frame: usize,
    /// Current phase of the software block cursor's blink (toggled every 0.5s).
    pub cursor_on: bool,
    /// Chat input caret cell painted this frame, where dead-key and IME previews should draw.
    pub cursor_position: Option<Position>,
    /// Whether the terminal window holds focus; the caret stops blinking and
    /// stays dark while it does not.
    pub app_focus: bool,
    /// Entry index the next frame must bring to the top of the viewport,
    /// consumed by the render.
    pub scroll_to_entry: Option<usize>,
    /// Entry an expand/collapse toggle holds steady, consumed by the next render.
    pub scroll_anchor: Option<crate::utils::scroll::ScrollAnchor>,
    /// Per-frame `(index, viewport-relative top, height)` of each painted entry, bounded by the viewport.
    pub entry_rows: Vec<(usize, i32, u16)>,
    /// Whether the last transcript frame showed the document's first row.
    pub at_top: bool,
    /// Rows the last transcript frame inserted above the queue to keep it at the bottom.
    pub queue_spacer: QueueSpacer,
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
            footer_model_area: Rect::default(),
            toast_selection_region: Region::default(),
            loading_selection_region: Region::default(),
            bottom_app_selection_region: Region::default(),
            bottom_app_selection_chrome: Vec::new(),
            bottom_app_selection_folds: crate::selection::Folds::default(),
            bottom_app: None,
            bottom_bar_link: None,
            toast_folds: Vec::new(),
            selection_chrome: Vec::new(),
            selection_hangs: Vec::new(),
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
            cursor_position: None,
            app_focus: true,
            scroll_to_entry: None,
            scroll_anchor: None,
            entry_rows: Vec::new(),
            at_top: false,
            queue_spacer: QueueSpacer::default(),
        }
    }
}

/// Transient overlays: inline notice, toast, and quit confirm.
#[derive(Default)]
pub struct Overlays {
    /// Active inline notice (e.g. "Copied to clipboard"), self-hiding after its timeout.
    pub notice: Option<Notice>,
    /// Stacked warning/error toasts, each self-hiding.
    pub toasts: VecDeque<Toast>,
    /// Identity handed to the next toast; only ever moves forward.
    next_toast_id: u64,
    /// When the last unhandled Escape landed, so a second one within
    /// `DOUBLE_ESC_DELAY` clears the input or enters rewind mode.
    pub last_escape: Option<Instant>,
}

/// Shared `/` and `@` completion popup state.
#[derive(Default)]
pub struct Completion {
    /// User-invocable skills, shown as `/name` popup rows.
    pub skills: Vec<crate::skill::Skill>,
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
    /// Whether voice mode is on; gates Ctrl+R.
    pub mode_enabled: bool,
    /// Projected transcription config, `None` until `config/read` returns.
    pub transcription: Option<TranscriptionConfig>,
    /// Current recording lifecycle.
    pub transcribe_state: TranscribeState,
    /// Live mic level in `[0,1]`, written by the audio thread, read by the indicator.
    pub peak: Arc<AtomicU32>,
    /// The active recording handle, or `None` when idle.
    pub recording: Option<Recording>,
    /// Sender the recording pipeline uses to push transcript/state updates to the UI.
    pub tx: Option<Sender<VoiceEvent>>,
    /// Frame counter driving the flushing-indicator animation (advanced per voice tick).
    pub frame: usize,
    /// Per-recording analytics bookkeeping.
    pub tracking: crate::voice::tracking::RecordingTracking,
}

/// `/config` settings screen state (a modal).
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
    /// Highlighted field and the option-list viewport.
    pub list: crate::option_list::OptionList<crate::option_list::MultiAction>,
    /// Search field filtering the settings rows.
    pub search: crate::search_field::Search,
    /// Nested editor opened by Enter on a writable settings field.
    pub edit: Option<ConfigEdit>,
}

/// `/theme` picker state (a bottom-app).
#[derive(Default)]
pub struct ThemePicker {
    /// While set the picker replaces the input box: title, option list, hint.
    pub open: bool,
    /// Highlighted option (0 = `auto`, then `theme::ALL`) and the viewport.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Option index carrying the current configured name, including `auto`.
    pub current: usize,
    /// `theme::ALL` index to restore if the picker is cancelled (Esc).
    pub original: usize,
    /// Deadline of the pending debounced preview.
    pub preview_at: Option<Instant>,
}

/// Agent modes: the runtime snapshot plus the Shift+Tab switch in flight.
#[derive(Default)]
pub struct Agents {
    /// Every agent from the runtime snapshot, in the server's discovery order.
    pub all: Vec<crate::server::AgentSummary>,
    /// The server's active agent.
    pub active: crate::server::AgentSummary,
    /// Session-wide auto-approve (`--yolo` or config), which survives a switch,
    /// as opposed to the YOLO agent's own.
    pub force_bypass_tool_permissions: bool,
    /// Name the last Shift+Tab asked for, painted before the server answers
    /// and re-used as the base of a coalesced press.
    pub desired: Option<String>,
    /// The last target the server refused; the next Shift+Tab cycles from it
    /// so a broken profile does not trap the cycle.
    pub refused: Option<String>,
    /// Whether a `session/agent/update` is in flight.
    pub switch_active: bool,
    /// Whether `/leanstall` or `/unleanstall` is in flight; input is rejected until it finishes.
    pub installing: bool,
    /// When the prompt spinner is due, or `None` once fired or cancelled.
    pub spinner_at: Option<Instant>,
    /// Whether the spinner has replaced the prompt marker.
    pub switching_indicator: bool,
    /// Spinner frame, advanced on the shared 100 ms tick.
    pub frame: usize,
}

/// A selectable model: `alias` is persisted, `display_name` is shown.
#[derive(Clone, Default)]
pub struct ModelOption {
    pub alias: String,
    pub display_name: String,
    /// The model's configured thinking level and the levels it offers.
    pub thinking: String,
    pub thinking_levels: Vec<String>,
}

/// `/model` picker state (a bottom-app).
#[derive(Default)]
pub struct ModelPicker {
    /// While set the picker replaces the input box: title, option list, hint.
    pub open: bool,
    /// Highlighted option (0 = Default row, then one per model) and the viewport.
    pub list: crate::option_list::OptionList<crate::option_list::MultiAction>,
    /// Model snapshot from `runtime/read`, refreshed on `runtime/updated`.
    pub models: Vec<ModelOption>,
    /// The active model's alias (`activeModel.alias`).
    pub current_model: String,
    /// Whether the active model is pinned (`activeModelPinned`); unpinned = Default.
    pub is_pinned: bool,
    /// Display name of the default model, shown in the Default row hint.
    pub default_display_name: String,
    /// Alias the Default row resolves to (`defaultModelAlias`).
    pub default_alias: String,
}

/// The artifacts modal state (a bottom-app): the session's PR/MR links.
#[derive(Default)]
pub struct ArtifactsModal {
    /// While set the modal replaces the input box: title, link rows, hint.
    pub open: bool,
    /// Highlighted artifact (latest first) and the viewport.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
}

/// `/log-level` picker state (a bottom-app).
#[derive(Default)]
pub struct LogLevelPicker {
    /// While set the picker replaces the input box.
    pub open: bool,
    /// Highlighted level index into `LOG_LEVELS`.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Draft session-tier level; `None` means no override.
    pub session: Option<String>,
    /// Draft config-tier level; `None` means the field is unset.
    pub config: Option<String>,
    /// Which badge Enter toggles: `session` or `config`.
    pub focused_badge: &'static str,
    /// Painted badge rectangles from the last frame, for clicks.
    pub badges: Vec<(Rect, &'static str)>,
    /// The chain as it stood when the picker opened, for the diff on apply.
    pub chain: crate::observability::level::LogLevelChain,
}

/// `/thinking` picker state (a bottom-app).
pub struct ThinkingPicker {
    /// While set the picker replaces the input box.
    pub open: bool,
    /// Highlighted level index into `levels`.
    pub list: crate::option_list::OptionList<crate::option_list::MultiAction>,
    /// The active model's current thinking level (`activeModel.thinking`).
    pub current_level: String,
    /// The levels the active model offers (`activeModel.thinkingLevels`).
    pub levels: Vec<String>,
    /// A `/model` pick held until its chained thinking level is chosen or skipped.
    pub model_pick: Option<crate::thinking_picker::ModelPick>,
}

/// `/resume` picker state.
#[derive(Default)]
pub struct ResumePicker {
    pub open: bool,
    pub sessions: Vec<crate::resume_picker::Session>,
    pub list: crate::option_list::OptionList<crate::option_list::MultiAction>,
    /// Folder the listed sessions belong to, from the server-filtered list. It
    /// is the attached session's cwd too.
    pub cwd: Option<String>,
    pub delete_confirm: Option<String>,
    /// Id of the session whose `session/delete` is in flight; keys are blocked
    /// until it answers.
    pub deleting: Option<String>,
    /// Set once a preview replaced the transcript, so returning to the current
    /// session reloads it.
    pub previewing: bool,
    pub preview_request: u64,
    /// A `session/resume` is in flight; the app is busy but shows no spinner.
    pub resuming: bool,
    /// Sessions whose lease another process holds, drawn with a lock.
    pub locked: std::collections::HashSet<String>,
    /// Sessions a lease notification already decided; a late snapshot keeps them.
    pub lease_notified: std::collections::HashSet<String>,
    /// The lease watch ended, so a late snapshot must not restore locks.
    pub lease_ended: bool,
    /// Whether the lease watch subscription already fired this process.
    pub subscribed: bool,
    pub transcript: Option<crate::transcript::Snapshot>,
}

/// Rewind bottom-app state, including whether rewind mode is on.
#[derive(Default)]
pub struct Rewind {
    /// While set the panel replaces the input box and owns every key.
    pub open: bool,
    /// History entry id of the highlighted user message, or `None` when none is.
    pub entry_id: Option<String>,
    /// Text of the highlighted user message, shown in the panel title.
    pub preview: crate::collapsed_pastes::Collapsed,
    /// Whether rewinding here would restore files (`session/rewind/read`).
    pub has_file_changes: bool,
    /// Which of the two option sets is shown: the action, then the persistence.
    pub step: crate::rewind::Step,
    /// The action step's answer, carried into the persistence step.
    pub restore_files: bool,
    /// Highlighted option in the current step.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
}

/// `ask_user_question` bottom-app state.
#[derive(Default)]
pub struct QuestionApp {
    /// While set the app replaces the input box and owns every key.
    pub open: bool,
    /// Delivered questions waiting for the open one to close or for a pause in
    /// the user's typing, oldest first, as `(source, request)`.
    pub pending: VecDeque<(crate::question_app::QuestionSource, UserQuestionRequest)>,
    /// Who the open app answers: a `user_input` callback or a local question.
    pub source: Option<crate::question_app::QuestionSource>,
    pub questions: Vec<UserQuestion>,
    pub footer_note: Option<String>,
    pub current_question_idx: usize,
    /// Cursor row (an option, then the free-text row, then the submit row)
    /// and the box viewport.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Saved answers by question index, as `(answer, is_other)`.
    pub answers: BTreeMap<usize, (String, bool)>,
    /// Ticked option indices per multi-select question.
    pub multi_selections: BTreeMap<usize, BTreeSet<usize>>,
    /// Free-text answers per question, kept while switching questions.
    pub other_texts: BTreeMap<usize, String>,
    /// Caret byte offset into the current question's free-text answer.
    pub other_cursor: usize,
    /// First visible wrapped row of the free-text field once it overflows.
    pub other_scroll: usize,
    /// Per-frame screen geometry of the free-text field, to map a click to the caret.
    pub other_field: Option<crate::question_app::OtherField>,
    /// When the app opened, so buffered keys cannot answer it instantly.
    pub mount_time: Option<Instant>,
}

/// `/mcp` browser state (a bottom-app).
#[derive(Default)]
pub struct MCPApp {
    pub search: crate::search_field::Search,
    /// While set the browser replaces the input box: title, option list, hint.
    pub open: bool,
    /// Latest `mcp/read` projection, refreshed by `mcp/refresh` and `mcp/toggle`.
    pub state: crate::server::MCPState,
    /// Last server-confirmed state, restored when a toggle is rejected.
    pub confirmed: crate::server::MCPState,
    /// A refresh is in flight; further refreshes are coalesced into it.
    pub refreshing: bool,
    /// When the open browser next re-discovers sources in the background.
    pub refresh_at: Option<Instant>,
    /// Source whose tools are listed, or `None` in the source list view.
    pub viewing_name: Option<String>,
    pub viewing_kind: Option<crate::server::MCPSourceKind>,
    /// Highlighted row index; headers and notes are skipped when navigating.
    pub list: crate::option_list::OptionList<crate::option_list::MultiAction>,
    /// The connector whose `connector_catalog/connect` is in flight, if any.
    pub connecting: Option<String>,
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

    /// The account read the rate-limit message consults, independent of the
    /// model it was read for.
    pub fn account(&self) -> &serde_json::Value {
        &self.reads.account
    }
}

/// MCP OAuth bottom-app state.
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
    /// Status prefixed to the help line.
    pub status_message: Option<String>,
    /// Whether an `mcp/login` is in flight, so `R` cannot start a second one.
    pub logging_in: bool,
    /// Whether the last login failed; the list then shows the retry note.
    pub failed: bool,
    /// Highlight among the three selectable action rows.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Login attempt counter, so a stale answer cannot settle a retry.
    pub generation: u64,
}

/// Connector auth bottom-app state.
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
    /// Status prefixed to the help line.
    pub status_message: Option<String>,
    /// Highlight among the three selectable action rows.
    pub list: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Open-attempt counter, so a stale worker answer cannot land.
    pub generation: u64,
}

#[derive(Default)]
pub struct App {
    pub credential_client: Option<Arc<crate::server::Client>>,
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
    pub voice_app: crate::voice_app::VoiceApp,
    pub proxy_setup: crate::proxy_setup::ProxySetupApp,
    pub config_screen: ConfigScreen,
    pub release_notes: crate::release_notes::State,
    pub theme_picker: ThemePicker,
    pub model_picker: ModelPicker,
    pub log_level_picker: LogLevelPicker,
    pub thinking_picker: ThinkingPicker,
    pub artifacts_modal: ArtifactsModal,
    pub resume_picker: ResumePicker,
    pub vibe_code_project: crate::vibe_code_project::State,
    /// The running `/teleport` operation, if any.
    pub teleport: Option<crate::teleport::Operation>,
    /// The latest written todo list, never seeded from history.
    pub todo_tracker: crate::todo_tracker::TodoTracker,
    pub todo_sidebar: crate::todo_tracker::TodoSidebar,
    pub rewind: Rewind,
    pub approval: crate::approval::State,
    pub question_app: QuestionApp,
    pub mcp: MCPApp,
    pub plugins: crate::plugins::State,
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
    /// Ctrl+C / Ctrl+D double-press quit confirmation.
    pub quit: crate::quit_manager::QuitManager,
    /// Widget answers to async requests, applied by the main event loop.
    pub events: Option<crate::ui_event::Sender>,
    /// Async slash-command results, reduced by the main event loop.
    pub command_tx: Option<tokio::sync::mpsc::Sender<CommandEvent>>,
    /// Analytics events queued by the reducer, sent by the main event loop.
    pub telemetry_tx: Option<tokio::sync::mpsc::Sender<crate::telemetry::TelemetryEvent>>,
    /// Identity and account reads reused by `/whoami`.
    pub whoami: WhoamiCache,
    /// Handle to the `/stress` firehose task, or `None` when it is off. A second
    /// `/stress` aborts it.
    pub stress: Option<tokio::task::JoinHandle<()>>,
    /// Whether a `/compact` request is in flight, so the UI shows the spinner
    /// and the `session/compacted` notification can settle it.
    pub compacting: bool,
    /// Picker commits and slash commands whose server round-trip is still in
    /// flight; the app is not idle while one is pending.
    pub pending_commits: Arc<AtomicU32>,
    /// Slash commands submitted while the session is still starting, replayed
    /// once `apply_ready` settles.
    pub pending_commands: Vec<String>,
    /// Ctrl+Z requested suspend; the event loop performs the SIGTSTP cycle.
    pub suspend_requested: bool,
    /// Ctrl+G requested the external editor; the event loop hands it the terminal.
    pub external_editor_requested: bool,
    /// App-server child exited unexpectedly (stdout EOF during steady state).
    pub server_closed: bool,
    /// Sub-agents view state: child sessions, the status list, the viewed child.
    pub subagents: crate::subagents::Subagents,
    /// Older history paged in once the transcript is scrolled to its top.
    pub older_history: crate::older_history::OlderHistory,
    /// Config validation warnings already toasted.
    pub shown_config_warnings: HashSet<String>,
    /// The `/debug` console: a right-docked live view of the session logs.
    pub debug_console: crate::debug_console::DebugConsole,
}

impl App {
    pub fn set_session_id(&mut self, session_id: String) {
        if self.session.session_id.as_deref() != Some(session_id.as_str()) {
            self.session.deferred_completion = None;
            crate::program_status::reset_shell(self);
            self.terminal_notifier.clear_outcome();
            self.vibe_code_project = Default::default();
            crate::teleport::reset(self);
            // A session switch starts a new log: drop the old session's lines
            // and paging offset so the console repopulates from the new one.
            crate::debug_console::clear_session(&mut self.debug_console);
        }
        // The console's poll task reads the current id on every tick.
        if let Some(session_tx) = &self.debug_console.session_tx {
            let _ = session_tx.send(session_id.clone());
        }
        self.session.session_id = Some(session_id);
        // Background emitters can queue before the handshake lands.
        for event in self.session.held_telemetry.drain(..) {
            crate::telemetry::enqueue(self.telemetry_tx.as_ref(), event.name, event.properties);
        }
    }

    /// Transition status; entering `Generating` resets the spinner, re-entering keeps the original start.
    pub fn set_status(&mut self, status: Status) {
        if !matches!(status, Status::Ready) {
            self.session.deferred_completion = None;
        }
        if matches!(status, Status::Generating { .. }) {
            self.terminal_notifier.clear_outcome();
        }
        match (self.session.status, status) {
            (Status::Generating { .. }, Status::Generating { .. }) => {}
            (_, Status::Generating { .. }) => {
                self.restart_loading();
                self.session.status = status;
            }
            _ => self.session.status = status,
        }
    }

    /// Restart the spinner and its timer for a new job.
    pub fn restart_loading(&mut self) {
        self.view.loading = LoadingAnim::default();
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
            // A pending child-transcript refresh owes the frame a re-fetch.
            && self.subagents.refresh_at.is_none()
            && matches!(self.session.status, Status::Ready | Status::Failed)
            // An accepted prompt the server has not promoted yet is still work
            // in flight.
            && self.queue.is_empty()
            // A teleport reports progress by notification, like a turn.
            && !crate::teleport::busy(self)
    }

    /// True once client-owned work has landed: startup finished, no request of
    /// ours in flight, no config screen loading, no armed theme preview (the
    /// frame is stable while its debounce runs, but the theme has not applied).
    /// A running turn does not count, so this is what a `settle_busy` capture
    /// waits for.
    pub fn is_settled(&self) -> bool {
        // An open gate counts as settled: nothing of ours is in flight.
        (self.trust.open || !matches!(self.session.status, Status::Starting))
            && (!self.config_screen.open || !self.config_screen.loading)
            && self.theme_picker.preview_at.is_none()
            && self.pending_commits.load(Ordering::Relaxed) == 0
            // A debounced theme preview repaints later; stay busy until it lands
            // so the captured frame shows the applied theme.
            && self.theme_picker.preview_at.is_none()
            // A scheduled child-transcript refresh owes the frame a re-fetch.
            && self.subagents.refresh_at.is_none()
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

    /// Only the main input ignores the shared blink phase when blinking is disabled.
    pub fn main_input_cursor_on(&self) -> bool {
        self.view.app_focus && (!self.session.startup_config.cursor_blink || self.view.cursor_on)
    }

    /// Follow terminal focus; unfocused carets stay hidden.
    pub fn set_app_focus(&mut self, focused: bool) {
        self.view.app_focus = focused;
        self.view.cursor_on = focused;
    }

    /// Terminal focus regained only lights the input while the composer owns
    /// it; a child view or the focused list keeps it off.
    pub fn set_app_focus_from_terminal(&mut self, focused: bool) {
        if !focused || (self.subagents.viewed_subagent_id.is_none() && !self.subagents.list.focused)
        {
            self.set_app_focus(focused);
        }
    }

    /// Show the hint-free `Loading` spinner while a slash command fetches; false when another job owns it.
    pub fn begin_command_loading(&mut self) -> bool {
        // A running shell command or turn keeps its own loading line.
        if self.session.shell_operation_id.is_some()
            || matches!(self.session.status, Status::Generating { .. })
        {
            return false;
        }
        self.view.loading = LoadingAnim::default();
        self.view
            .loading
            .set_label(ui::loading::COMMAND_LOADING_STATUS);
        self.view.command_loading = true;
        true
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

/// Seconds a toast stays up.
const TOAST_SECS: u64 = 5;

/// Max toasts kept at once; overflow is reported with the newest toast.
const MAX_TOASTS: usize = 64;

impl App {
    /// True whenever a recording is in flight.
    pub fn recording_active(&self) -> bool {
        self.voice.transcribe_state != TranscribeState::Idle
    }

    /// Latest mic level in `[0,1]` for the recording indicator.
    pub fn current_peak(&self) -> f32 {
        f32::from_bits(self.voice.peak.load(Ordering::Relaxed))
    }

    /// Ctrl+R: start or stop recording.
    pub fn toggle_recording(&mut self) {
        match self.voice.transcribe_state {
            TranscribeState::Idle => self.start_recording(),
            TranscribeState::Recording => self.stop_recording(),
            TranscribeState::Flushing => {}
        }
    }

    /// Ctrl+O: flip the global fold state and apply it to every group and
    /// collapsible result body at once.
    pub fn toggle_tools(&mut self) {
        self.view.tools_collapsed = !self.view.tools_collapsed;
        let expand = !self.view.tools_collapsed;
        let viewed_child = self.subagents.viewed_subagent_id.clone();
        let mut expanded = std::mem::take(&mut self.view.expanded);
        expanded.clear();
        if expand {
            for id in self.view.transcript.expandable_ids() {
                expanded.insert(id);
            }
        }
        // Every mounted section flips; the viewed child's transcript is only
        // swapped in at render time, so its ids and cache are handled here — in
        // both directions, or a collapse keeps the cached expanded layout.
        if let Some(child) = viewed_child
            .as_deref()
            .and_then(|child_id| self.subagents.transcripts.child_mut(child_id))
        {
            if expand {
                expanded.extend(child.transcript.expandable_ids());
            }
            child.cache.invalidate_layouts();
        }
        self.view.expanded = expanded;
        self.view.transcript_cache.invalidate_layouts();
        self.view.scroll_anchor = crate::utils::scroll::bulk_toggle_anchor(
            self.view.scroll_target,
            &self.view.entry_rows,
        );
    }

    /// History (re)builds consult the bulk fold state: while expanded,
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
        match crate::voice::start(
            &cfg,
            self.credential_client.clone(),
            self.voice.peak.clone(),
            tx,
        ) {
            Ok(rec) => {
                self.voice.recording = Some(rec);
                self.voice.transcribe_state = TranscribeState::Recording;
                self.voice.tracking.start();
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
        self.voice.tracking.mark_stopped();
        self.voice.transcribe_state = TranscribeState::Flushing;
    }

    /// Abort recording without a transcript.
    pub fn cancel_recording(&mut self) {
        if self.voice.transcribe_state == TranscribeState::Idle {
            return;
        }
        if let Some(rec) = self.voice.recording.take() {
            rec.cancel();
        }
        crate::telemetry::record(
            self,
            crate::telemetry::event::TRANSCRIPTION_CANCEL,
            self.voice.tracking.cancel_properties(),
        );
        self.voice.transcribe_state = TranscribeState::Idle;
    }

    /// Reduce a pipeline update: append transcript, surface notices, settle state.
    pub fn apply_voice_event(&mut self, ev: VoiceEvent) {
        match ev {
            VoiceEvent::TextDelta(text) => {
                self.voice.tracking.add_transcript(&text);
                self.chat_input.normalize_positions();
                let before = crate::edit_history::Snapshot::capture(&self.chat_input);
                self.chat_input.anchor = None;
                self.chat_input.scroll = None;
                crate::input::reset_history_state(self);
                crate::utils::input_edit::insert(
                    &mut self.chat_input.input,
                    &mut self.chat_input.cursor,
                    &text,
                );
                self.chat_input.record_edit(before, true, Instant::now());
                crate::completion_manager::input_changed(self);
            }
            // A cancelled recording already reported; its late session is noise.
            VoiceEvent::SessionCreated(_)
                if self.voice.transcribe_state == TranscribeState::Idle => {}
            VoiceEvent::SessionCreated(request_id) => {
                self.voice.tracking.recording_id = request_id;
                crate::telemetry::record(
                    self,
                    crate::telemetry::event::TRANSCRIPTION_START,
                    self.voice.tracking.start_properties(),
                );
            }
            VoiceEvent::Notice(msg) => self.show_voice_notice(msg),
            VoiceEvent::Error(msg) => {
                crate::telemetry::record(
                    self,
                    crate::telemetry::event::TRANSCRIPTION_ERROR,
                    self.voice.tracking.error_properties(&msg),
                );
                self.voice.recording = None;
                self.voice.transcribe_state = TranscribeState::Idle;
                self.show_toast(
                    format!("Voice transcription failed: {msg}"),
                    ToastSeverity::Error,
                    TOAST_SECS,
                );
            }
            VoiceEvent::Finished => {
                crate::telemetry::record(
                    self,
                    crate::telemetry::event::TRANSCRIPTION_DONE,
                    self.voice.tracking.done_properties(),
                );
                self.voice.recording = None;
                self.voice.transcribe_state = TranscribeState::Idle;
            }
        }
    }

    fn show_voice_notice(&mut self, text: String) {
        self.overlays.notice = Some(Notice {
            text,
            severity: ToastSeverity::Information,
            until: Instant::now() + Duration::from_secs(VOICE_NOTICE_SECS),
        });
    }

    /// Enqueue a toast for `seconds`, bounded.
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
