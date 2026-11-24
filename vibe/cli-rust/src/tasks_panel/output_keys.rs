//! Keys of a task's output: its search first, then scrolling, `n`/`N` between matches, `s` to stop the task, and Esc back to the list.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::rail::{self, Item};
use crate::search_field::{self, Outcome};
use crate::selection::RegionId;
use crate::server::Client;

/// Whether the output's search field takes typed keys.
pub fn typing(app: &App) -> bool {
    let shown = app.tasks_panel.shown();
    rail::window(app) == Some(Item::Tasks) && shown.is_some_and(|view| view.search.focused)
}

/// `/` searches, Enter keeps the search, `n`/`N` move between matches, ↑↓/jk, PageUp/PageDown, Home and End scroll, `s` stops the task, Esc goes back to the list.
pub(super) fn output_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    match search_field::handle_key(&mut view.search, &key) {
        Outcome::Consumed => return,
        Outcome::Filtered => return searched(app),
        Outcome::Pass => {}
    }
    let plain = key.modifiers.is_empty();
    let (focused, found) = (view.search.focused, !view.find.query().is_empty());
    let page = app.rail.scroll_page.max(1);
    match key.code {
        KeyCode::Enter if focused => view.search.focused = false,
        KeyCode::Esc => {
            if super::back(app) {
                crate::selection::clear_region(app, RegionId::RailWindow);
            }
        }
        KeyCode::Up => rail::scroll(app, false, 1),
        KeyCode::Down => rail::scroll(app, true, 1),
        KeyCode::Char('k') if plain => rail::scroll(app, false, 1),
        KeyCode::Char('j') if plain => rail::scroll(app, true, 1),
        KeyCode::Char('n') if plain && found => step(app, true),
        KeyCode::Char('N') if found => step(app, false),
        KeyCode::PageUp => rail::scroll(app, false, page),
        KeyCode::PageDown => rail::scroll(app, true, page),
        KeyCode::Home => rail::scroll_to(app, 0),
        KeyCode::End => rail::scroll_to(app, usize::MAX),
        KeyCode::Char('s') if plain => {
            if let Some(index) = super::shown_index(app) {
                super::stop(app, client, index);
            }
        }
        _ => {}
    }
}

/// Insert pasted text into the focused search; false when it is not focused.
pub fn paste(app: &mut App, text: &str) -> bool {
    if !typing(app) {
        return false;
    }
    let pasted = (app.tasks_panel.shown_mut())
        .is_some_and(|view| search_field::paste(&mut view.search, text));
    if pasted {
        searched(app);
    }
    pasted
}

/// Jump to the new query's last match, the latest output; an edit that searches the same keeps the view.
fn searched(app: &mut App) {
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    if view.find.set_query(&view.search.query, &view.output) {
        step(app, false);
    }
}

/// Move to the next or previous match and bring it into view.
fn step(app: &mut App, forward: bool) {
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    let Some(hit) = view.find.step(forward) else {
        return;
    };
    let Some(row) = view.output.row_of(hit) else {
        return;
    };
    let (top, page) = (app.rail.scroll_top, app.rail.scroll_page.max(1));
    if (top..top + page).contains(&row) {
        return;
    }
    // The match lands a third down the view, its lead-in above it.
    app.rail.scroll = rail::Scroll::Row(row.saturating_sub(page / 3));
}
