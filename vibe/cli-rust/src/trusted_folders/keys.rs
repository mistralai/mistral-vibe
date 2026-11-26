//! `/trusted-folders` list keys: navigation, trust or untrust, forget, and the click that toggles.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseEvent};
use serde_json::json;

use super::rows::{self, Row};
use super::{add, request, selectable, Event, Target};
use crate::app::App;
use crate::option_list::Click;
use crate::search_field::{self, Press};
use crate::selection::RegionId;
use crate::server::{method, Client};

/// The open browser owns keys; the add view, while open, takes them first.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if app.trusted_folders.add.is_some() {
        return add::handle_key(app, client, key);
    }
    let state = &mut app.trusted_folders;
    if search_field::handle_key(&mut state.filter, &key).handled(|| super::reset_highlight(app)) {
        return;
    }
    // Bindings never fire with a modifier held; Shift is how some layouts type `/`.
    if key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        return;
    }
    match key.code {
        KeyCode::Esc => super::close(app),
        KeyCode::Up | KeyCode::Char('k') => step(app, false),
        KeyCode::Down | KeyCode::Char('j') => step(app, true),
        KeyCode::PageUp => jump(app, -page(app)),
        KeyCode::PageDown => jump(app, page(app)),
        KeyCode::Home => jump(app, isize::MIN / 2),
        KeyCode::End => jump(app, isize::MAX / 2),
        KeyCode::Enter => toggle(app, client),
        KeyCode::Char('f') => update(app, client, Target::Undecided),
        KeyCode::Char('a') => add::open(app, client),
        _ => {}
    }
}

/// Arrows wrap around the folders, skipping headings and notes.
fn step(app: &mut App, down: bool) {
    let rows = rows::rows(&app.trusted_folders);
    let selectable = |index: usize| rows[index].selectable();
    app.trusted_folders
        .list
        .step(down, rows.len(), true, selectable);
}

/// Pages and Home/End move by folders and clamp at the ends.
fn jump(app: &mut App, delta: isize) {
    let selectable = selectable(app);
    let Some(last) = selectable.len().checked_sub(1) else {
        return;
    };
    let current = app.trusted_folders.list.selected();
    let position = selectable.iter().position(|index| *index == current);
    let target = (position.unwrap_or(0) as isize).saturating_add(delta);
    let target = target.clamp(0, last as isize) as usize;
    app.trusted_folders.list.select(selectable[target]);
}

fn page(app: &App) -> isize {
    app.trusted_folders.list_area.height.max(1) as isize
}

/// Enter moves the highlighted folder to the other list.
fn toggle(app: &mut App, client: &Arc<Client>) {
    let rows = rows::rows(&app.trusted_folders);
    let Some(Row::Entry(trusted, _)) = rows.get(app.trusted_folders.list.selected()) else {
        return;
    };
    let target = if *trusted {
        Target::Untrusted
    } else {
        Target::Trusted
    };
    update(app, client, target);
}

fn update(app: &mut App, client: &Arc<Client>, target: Target) {
    let state = &app.trusted_folders;
    if state.updating {
        return;
    }
    let rows = rows::rows(state);
    let Some(path) = rows
        .get(state.list.selected())
        .and_then(|row| rows::path(state, row))
    else {
        return;
    };
    let params = json!({ "path": path, "state": target.wire() });
    let update = method::WORKSPACE_TRUST_FOLDERS_UPDATE;
    let wrap = move |answer| Event::Updated {
        path,
        target,
        answer,
    };
    app.trusted_folders.updating = request::spawn(app, client, update, params, wrap);
}

/// A press on the search field focuses it; a click on the highlighted folder toggles it, like Enter.
pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    if app.trusted_folders.add.is_some() {
        return add::mouse(app, client, event);
    }
    let at = (event.column, event.row);
    let click = crate::mouse::option_gesture(app, event, RegionId::BottomApp, |app| {
        &mut app.trusted_folders.list
    });
    let state = &mut app.trusted_folders;
    let on_option = state.list.index_at(at).is_some();
    if search_field::press(&mut state.filter, &event, true, on_option) == Press::Field {
        return;
    }
    if let Some(Click::Confirm(_)) = click {
        toggle(app, client);
    }
}
