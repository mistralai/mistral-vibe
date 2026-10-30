//! `/release-notes` (`/whatsnew`): a full-screen, searchable pager over every release's notes.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use ratatui::text::Line;

use crate::app::App;
use crate::search_field::{self, Outcome, Search};
use crate::whats_new::{self, Entry};

/// The full changelog in the public repository, every entry beyond the in-app highlights.
pub const FULL_CHANGELOG_URL: &str =
    "https://github.com/mistralai/mistral-vibe/blob/main/CHANGELOG.md";
/// Rows one wheel notch scrolls.
const WHEEL_STEP: usize = 2;

#[derive(Default)]
pub struct State {
    pub open: bool,
    pub search: Search,
    /// The first body row shown.
    pub scroll: usize,
    /// Body rows from the last render: the page size and the scroll bound.
    pub page: usize,
    pub total: usize,
    /// The full-changelog link from the last render, for clicks.
    pub link_area: Rect,
    entries: Vec<Entry>,
    /// The body rendered for `(query, width)`, reused until either changes.
    rendered: Option<(String, u16, Vec<Line<'static>>)>,
}

impl State {
    /// The body lines for the current query at `width`, rendered once per change.
    pub fn lines(&mut self, width: u16) -> &[Line<'static>] {
        let query = self.search.query.trim().to_owned();
        let stale = !matches!(&self.rendered, Some((q, w, _)) if *q == query && *w == width);
        if stale {
            let text = whats_new::release_notes(&self.entries, &query);
            let mut lines = crate::ui::markdown::command_result(&text, width);
            let leading = lines.iter().take_while(|line| line.width() == 0).count();
            lines.drain(..leading);
            self.rendered = Some((query, width, lines));
        }
        self.rendered.as_ref().map_or(&[], |(_, _, lines)| lines)
    }

    fn max_scroll(&self) -> usize {
        self.total.saturating_sub(self.page)
    }

    /// Scroll by `delta` rows, clamped to the document.
    pub fn scroll_by(&mut self, delta: isize) {
        self.scroll = self
            .scroll
            .saturating_add_signed(delta)
            .min(self.max_scroll());
    }
}

pub fn open(app: &mut App) {
    app.release_notes = State {
        open: true,
        entries: whats_new::entries(),
        ..State::default()
    };
}

fn close(app: &mut App) {
    app.release_notes = State::default();
    crate::selection::scrolled(app);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    let state = &mut app.release_notes;
    match search_field::handle_key(&mut state.search, &key) {
        Outcome::Pass => {}
        Outcome::Consumed => return,
        Outcome::Filtered => {
            state.scroll = 0;
            return;
        }
    }
    let plain = !key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    let page = state.page.max(1) as isize;
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Up => state.scroll_by(-1),
        KeyCode::Down => state.scroll_by(1),
        KeyCode::Char('k') if plain => state.scroll_by(-1),
        KeyCode::Char('j') if plain => state.scroll_by(1),
        KeyCode::PageUp => state.scroll_by(-page),
        KeyCode::PageDown | KeyCode::Char(' ') if plain => state.scroll_by(page),
        KeyCode::Home => state.scroll = 0,
        KeyCode::End => state.scroll = state.max_scroll(),
        KeyCode::Enter => crate::external_url::open(FULL_CHANGELOG_URL),
        _ => {}
    }
}

pub fn paste(app: &mut App, text: &str) {
    if search_field::paste(&mut app.release_notes.search, text) {
        app.release_notes.scroll = 0;
    }
}

pub fn wheel(app: &mut App, down: bool) {
    let step = WHEEL_STEP as isize;
    app.release_notes.scroll_by(if down { step } else { -step });
}

/// A press on the search field focuses it, one on the full-changelog link opens it.
pub fn mouse(app: &mut App, event: MouseEvent) {
    if event.kind != MouseEventKind::Down(MouseButton::Left) {
        return;
    }
    let at = (event.column, event.row).into();
    let state = &mut app.release_notes;
    state.search.focused = state.search.area.contains(at);
    if state.link_area.contains(at) {
        crate::external_url::open(FULL_CHANGELOG_URL);
    }
}
