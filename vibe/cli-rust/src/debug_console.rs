//! The `/debug` console: a right-docked live
//! view of the app-server's session logs, toggled by `/debug` or Ctrl+\.

mod paging;
mod parse;
mod scroll;

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::Arc;
use std::time::Duration;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use tokio::sync::mpsc::Sender;
use tokio::sync::watch;

use crate::ui::theme;

pub use paging::{
    apply_event, apply_page, apply_polled, at_top, clear_session, load_page, toggle, total_visual,
    view,
};
pub use parse::{parse_entries, parse_page, timestamp_ms};
pub use scroll::{
    copy_entry_at, entry_at, maybe_load_previous, scroll, scroll_down, scroll_for_key, scroll_home,
    scroll_up, wheel, Scroll,
};

/// Entries per older-page request.
pub const PAGE_SIZE: u64 = 30;
/// Entries per poll.
pub const POLL_LIMIT: u64 = 500;
/// ADR 0016 bound on retained entries: past this the oldest are dropped
/// (`paging::trim`).
pub const MAX_ENTRIES: usize = 4_096;
/// ADR 0016 bound on retained wrapped lines: past it the oldest entries are
/// dropped (`paging::trim`) so the u16 viewport in `view()` never clamps.
pub const MAX_VISUAL_LINES: usize = u16::MAX as usize;
/// Delay between two log polls.
pub const POLL_INTERVAL: Duration = Duration::from_millis(500);

/// One `diagnostics/logs/read` entry, decoded for display.
#[derive(Clone)]
pub struct LogEntry {
    pub id: String,
    /// Local `%Y-%m-%d %H:%M:%S` stamp, formatted once at parse time.
    pub timestamp: String,
    pub level: String,
    /// The decoded message.
    pub message: String,
}

/// A parsed `diagnostics/logs/read` page (entries newest-first, as served).
pub struct LogPage {
    pub entries: Vec<LogEntry>,
    pub has_more: bool,
    pub cursor: Option<u64>,
}

/// An answer applied on the main thread, tagged with the generation of the
/// toggle that spawned it so a stale answer (the console closed or reopened
/// mid-request) is dropped instead of corrupting the fresh state.
pub enum Event {
    /// An older-page answer; `page: None` marks a failed request (paging stops).
    Page {
        generation: u64,
        /// The session the page was requested under; `/clear`, `/compact`,
        /// `/rewind` and `/resume` switch it mid-request, so a mismatch is
        /// stale like the generation.
        session: String,
        /// The offset the page was requested with.
        requested: u64,
        page: Option<LogPage>,
    },
    /// The newest-entries poll answer.
    Polled(u64, Vec<LogEntry>),
}

/// The console's state.
#[derive(Default)]
pub struct DebugConsole {
    /// While set the panel docks on the right and polls for new logs.
    pub open: bool,
    /// Whether the last draw actually docked the panel; a terminal too narrow
    /// for the dock hides it while `open` stays set, and a hidden console must
    /// not take the navigation keys.
    pub visible: bool,
    /// Parsed entries in display order (oldest first).
    pub entries: Vec<LogEntry>,
    /// Visual lines per entry at `wrap_width`, parallel to `entries`.
    pub wrap_counts: Vec<usize>,
    /// The width `wrap_counts` were computed at; 0 until the first draw.
    pub wrap_width: u16,
    /// Server paging cursor: how many newest entries to skip.
    pub cursor: Option<u64>,
    /// Whether the server has older entries past `cursor`.
    pub has_more: bool,
    /// Entry ids already rendered, so pages and polls never duplicate a line.
    pub seen: HashSet<String>,
    /// Lines lifted off the bottom; 0 pins to the newest entry.
    pub scroll: u16,
    /// Body height of the last render; the fill-viewport target.
    pub body_height: u16,
    /// Sender carrying page and poll answers to the main thread.
    pub tx: Option<Sender<Event>>,
    /// The session id the poll task reads on every tick; `set_session_id`
    /// updates it so polling survives a session switch.
    pub session_tx: Option<watch::Sender<String>>,
    /// A page load or poll is in flight, shared with the
    /// tasks so a poll never overlaps a page request. Cleared when the request
    /// finishes, whether or not its answer is still relevant.
    pub page_in_flight: Arc<AtomicBool>,
    /// Bumped on every toggle; a poll task exits when its copy goes stale.
    pub generation: Arc<AtomicU64>,
    /// Per-frame `(top, bottom, entry index)` of painted body rows, for click-copy.
    pub hitmap: Vec<(u16, u16, usize)>,
    /// The body area of the last render, published for mouse routing.
    pub body_area: Rect,
}

impl LogEntry {
    /// The plain text a click copies (the rendered line without its styling).
    pub fn plain_line(&self) -> String {
        format!("{} {:<8} {}", self.timestamp, self.level, self.message)
    }
}

/// Decode a log message: `\\n` is a newline, `\\<char>` is the char.
pub fn decode_log_message(encoded: &str) -> String {
    let mut decoded = String::with_capacity(encoded.len());
    let mut chars = encoded.chars();
    while let Some(character) = chars.next() {
        if character != '\\' {
            decoded.push(character);
            continue;
        }
        match chars.next() {
            Some('n') => decoded.push('\n'),
            Some(escaped) => decoded.push(escaped),
            None => decoded.push('\\'),
        }
    }
    decoded
}

/// The level's style, through theme colors.
pub fn level_style(level: &str) -> Style {
    match level {
        "INFO" => theme::text(theme::secondary()),
        "WARNING" => theme::text(theme::warning()),
        "ERROR" => theme::text(theme::error()),
        "CRITICAL" => theme::text(theme::error()).add_modifier(Modifier::BOLD),
        _ => theme::dim(theme::foreground()),
    }
}
