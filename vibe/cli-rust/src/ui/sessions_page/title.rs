//! The page title with how the list is loading on its right, and the filters row under the search.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::super::{hint_line, theme};
use crate::app::App;
use crate::hints::{action, Hint};
use crate::sessions_page::Sort;

/// The title's note when the list stops at `MAX_SESSIONS`.
const TRUNCATED: &str = "Latest 5000 only";

pub(super) fn draw(app: &App, f: &mut Frame, area: Rect) {
    let page = &app.sessions_page;
    let bold = theme::text(theme::foreground()).add_modifier(Modifier::BOLD);
    f.buffer_mut().set_string(area.x, area.y, "Sessions", bold);
    let mut notes = Vec::new();
    if page.truncated {
        notes.push(TRUNCATED);
    }
    // An empty list says it loads itself; once rows show, later pages still
    // load, or the kept listing is being refreshed.
    if page.loading && !page.view.rows.is_empty() {
        notes.push(match page.reloading {
            true => "Refreshing…",
            false => "Loading more…",
        });
    }
    let notes = notes.join(" · ");
    let width = (unicode_width::UnicodeWidthStr::width(notes.as_str()) as u16).min(area.width);
    let x = area.right().saturating_sub(width);
    f.buffer_mut()
        .set_stringn(x, area.y, notes, width.into(), theme::muted_style());
}

/// The sort, then every filter, each with its key: the sort and the filters
/// that are on in the warning color, the others muted, so the row says what
/// the list shows and how to change it.
pub(super) fn draw_filters(app: &App, f: &mut Frame, area: Rect) {
    let browse = &app.sessions_page.browse;
    let created = browse.sort == Sort::Created;
    let sort = match created {
        true => action::BY_CREATION_DATE,
        false => action::BY_ACTIVITY,
    };
    let filters: [(Hint, bool); 4] = [
        (("s", sort), true),
        ((".", action::ARCHIVED), browse.archived),
        (("c", action::THIS_FOLDER), browse.folder_only),
        (("g", action::GONE_FOLDERS), browse.gone),
    ];
    let on = theme::text(theme::warning()).add_modifier(Modifier::BOLD);
    let mut spans: Vec<Span<'static>> = Vec::new();
    for ((key, name), active) in filters {
        if !spans.is_empty() {
            spans.push(Span::raw("  "));
        }
        let label = if active { on } else { theme::muted_style() };
        spans.extend([
            Span::styled(key, hint_line::key_style()),
            Span::raw(" "),
            Span::styled(name, label),
        ]);
    }
    f.buffer_mut()
        .set_line(area.x, area.y, &Line::from(spans), area.width);
}
