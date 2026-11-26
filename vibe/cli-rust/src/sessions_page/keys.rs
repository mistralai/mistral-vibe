//! The page's keys: the list, its search, the rename field and the help.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{actions, folders, preview, preview_view, Column, Sort};
use crate::app::App;
use crate::option_list::Click;
use crate::search_field::{self, Outcome};
use crate::server::Client;

/// Rows PageUp/PageDown move by.
const PAGE: usize = 10;

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    // A key can move or rewrite the text a screen-anchored selection covers.
    super::clear_selections(app);
    if is_ctrl_c(&key) {
        return super::close(app);
    }
    if app.sessions_page.busy.is_some() {
        return;
    }
    // The unfolded hints leave every key working; Esc folds them back first.
    if app.sessions_page.help && key.code == KeyCode::Esc {
        app.sessions_page.help = false;
        return;
    }
    if app.sessions_page.rename.is_some() {
        return super::rename::key(app, client, key);
    }
    if preview_key(app, &key) {
        return;
    }
    let was_searching = app.sessions_page.browse.search.focused;
    let outcome = search_field::handle_key(&mut app.sessions_page.browse.search, &key);
    // The search filters the sessions: typing in it gives them the keys.
    if !was_searching && app.sessions_page.browse.search.focused {
        app.sessions_page.browse.focus = Column::Sessions;
    }
    match outcome {
        Outcome::Consumed => return,
        Outcome::Filtered => {
            app.sessions_page.browse.list.reset();
            return super::relayout(app);
        }
        Outcome::Pass => {}
    }
    let chorded = key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    let searching = app.sessions_page.browse.search.focused;
    if !chorded && !searching && folders::focused(app) && folder_key(app, key.code) {
        return;
    }
    match key.code {
        KeyCode::Esc => super::close(app),
        KeyCode::Up => step(app, false),
        KeyCode::Down => step(app, true),
        KeyCode::PageUp | KeyCode::PageDown | KeyCode::Home | KeyCode::End => page(app, key.code),
        KeyCode::Enter | KeyCode::Right if !chorded => actions::open(app, client),
        KeyCode::Left if !chorded => folders::focus(app, Column::Folders),
        _ if chorded || searching => {}
        KeyCode::Char('k') => step(app, false),
        KeyCode::Char('j') => step(app, true),
        KeyCode::Char('n') => actions::new(app, client),
        KeyCode::Char('a') => actions::archive(app, client),
        KeyCode::Char('r') => super::rename::start(app),
        KeyCode::Char(' ') => fold(app),
        KeyCode::Char('?') => app.sessions_page.help ^= true,
        KeyCode::Char('.') => toggle(app, "archived_view", |page| page.archived ^= true),
        KeyCode::Char('c') => toggle(app, "folder_filter", |page| page.folder_only ^= true),
        KeyCode::Char('g') => toggle(app, "gone_folders", |page| page.gone ^= true),
        KeyCode::Char('s') => toggle(app, "sort", |page| {
            page.sort = match page.sort {
                Sort::Activity => Sort::Created,
                Sort::Created => Sort::Activity,
            }
        }),
        _ => {}
    }
}

/// A key the focused folders column takes: ↑↓/jk move the folder, PageUp/Home
/// and PageDown/End go to All and the last folder, →/Enter hand the keys to
/// the sessions. The others act as in the sessions column.
fn folder_key(app: &mut App, code: KeyCode) -> bool {
    match code {
        KeyCode::Up => folders::step(app, false),
        KeyCode::Down => folders::step(app, true),
        KeyCode::Char('k') => folders::step(app, false),
        KeyCode::Char('j') => folders::step(app, true),
        KeyCode::Right | KeyCode::Enter => folders::focus(app, Column::Sessions),
        KeyCode::PageUp | KeyCode::Home => folders::pick(app, 0),
        KeyCode::PageDown | KeyCode::End => {
            let last = app.sessions_page.view.folders.len();
            folders::pick(app, last)
        }
        KeyCode::Left => {}
        _ => return false,
    }
    true
}

/// Shift+↑↓ scroll the preview's conversation and Ctrl+O shows every tool call in it, as in the conversation.
fn preview_key(app: &mut App, key: &KeyEvent) -> bool {
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        KeyCode::Up if shift => preview_view::scroll(app, true, preview_view::STEP),
        KeyCode::Down if shift => preview_view::scroll(app, false, preview_view::STEP),
        KeyCode::Char('o') if ctrl => preview_view::toggle_tools(app),
        _ => return false,
    }
    true
}

fn is_ctrl_c(key: &KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c')
}

fn step(app: &mut App, down: bool) {
    let len = app.sessions_page.view.rows.len();
    if len == 0 {
        return;
    }
    app.sessions_page
        .browse
        .list
        .step(down, len, true, |_| true);
    preview::request(app);
}

fn page(app: &mut App, code: KeyCode) {
    let len = app.sessions_page.view.rows.len();
    let list = &mut app.sessions_page.browse.list;
    match code {
        KeyCode::PageUp => list.page(false, len, PAGE),
        KeyCode::PageDown => list.page(true, len, PAGE),
        KeyCode::Home => list.select(0),
        _ => list.select(len.saturating_sub(1)),
    }
    preview::request(app);
}

/// Flip a view setting and lay the list out again.
fn toggle(app: &mut App, action: &'static str, flip: impl FnOnce(&mut super::Browse)) {
    flip(&mut app.sessions_page.browse);
    super::relayout(app);
    let search_active = !app.sessions_page.browse.search.query.is_empty();
    crate::telemetry::session_search_selected(app, action, None, search_active, None);
}

/// Space: fold or unfold the highlighted row's started sessions. A child the
/// fold hides hands the highlight to its family's top row.
fn fold(app: &mut App) {
    let page = &mut app.sessions_page;
    let Some(family) = page.view.families.get(page.browse.list.selected()).cloned() else {
        return;
    };
    let toggled = &mut page.browse.toggled;
    if !toggled.remove(&family) {
        toggled.insert(family.clone());
    }
    let highlighted = super::highlighted_id(app);
    super::relayout_on(app, highlighted.clone());
    if super::highlighted_id(app) != highlighted {
        super::relayout_on(app, Some(family));
    }
}

/// Scroll the list without moving the highlight; nothing to scroll while it loads.
pub fn wheel(app: &mut App, down: bool) {
    if !app.sessions_page.view.rows.is_empty() {
        app.sessions_page.browse.list.wheel(down, 2);
    }
}

/// Paste goes to the rename modal or the focused search.
pub fn paste(app: &mut App, text: &str) {
    let page = &mut app.sessions_page;
    if let Some(rename) = page.rename.as_mut() {
        return super::rename::paste(rename, text);
    }
    if search_field::paste(&mut page.browse.search, text) {
        page.browse.list.reset();
        super::relayout(app);
    }
}

/// A click highlights a session; a click on the highlight opens it.
pub fn click(app: &mut App, client: &Arc<Client>, click: Click) {
    if app.sessions_page.busy.is_some() {
        return;
    }
    app.sessions_page.browse.focus = Column::Sessions;
    match click {
        Click::Highlight(_) => preview::request(app),
        Click::Confirm(_) => actions::open(app, client),
    }
}
