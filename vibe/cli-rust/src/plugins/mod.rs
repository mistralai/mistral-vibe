//! `/plugins` browser and `/reload-plugins`.

mod filter;
mod request;
pub mod rows;
pub mod text;

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::option_list::{OptionList, SingleAction};
use crate::search_field::Search;
use crate::selection::RegionId;
use crate::server::{Client, PluginCatalogState};
use crate::transcript::local;
use rows::Row;

pub use filter::paste;
pub use request::Diff;

pub const NO_PLUGIN_BACKEND: &str = "This session resolves no plugins.";
pub const NONE_INSTALLED: &str = "No plugins are installed for this session.";
/// A move far enough to land on the first or last row (Home/End).
const EDGE: isize = isize::MAX / 2;
/// Lines one wheel notch scrolls the option list.
const WHEEL_STEP: usize = 2;

/// The open browser.
#[derive(Default)]
pub struct State {
    /// While set the browser replaces the input box.
    pub open: bool,
    pub catalog: PluginCatalogState,
    /// Plugin whose detail is shown, or `None` in the list view.
    pub viewing: Option<String>,
    pub filter: Search,
    /// Highlighted row index (only plugin rows are selectable) and the viewport.
    pub list: OptionList<SingleAction>,
    /// Option-list viewport from the last render.
    pub list_area: Rect,
    /// A reload is in flight; further ones are dropped so diffs never interleave.
    pub reloading: bool,
}

impl State {
    /// The filter query as matched: stripped and case-folded.
    pub fn query(&self) -> String {
        caseless::default_case_fold_str(self.filter.query.trim())
    }
}

/// A plugin round-trip's answer; `Ok(None)` comes from a backend without plugins.
pub enum Event {
    Read(Result<Option<PluginCatalogState>, String>),
    Reloaded(Result<Diff, String>),
}

/// `/plugins`: read the catalogue, then open the browser on it.
pub fn show(app: &mut App, client: &Arc<Client>) {
    request::spawn(app, client, |client, session_id| async move {
        Event::Read(request::read(&client, &session_id).await)
    });
}

/// `/reload-plugins` and the browser's `r`: re-pin, then report what moved; one at a time.
pub fn reload(app: &mut App, client: &Arc<Client>) {
    if app.plugins.reloading {
        return;
    }
    app.plugins.reloading = request::spawn(app, client, |client, session_id| async move {
        Event::Reloaded(request::reload_diff(&client, &session_id).await)
    });
}

pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Read(Ok(None)) => add_status(app, NO_PLUGIN_BACKEND),
        Event::Read(Ok(Some(catalog)))
            if catalog.plugins.is_empty() && catalog.dropped.is_empty() =>
        {
            add_status(app, NONE_INSTALLED);
        }
        Event::Read(Ok(Some(catalog))) => open(app, catalog),
        Event::Read(Err(error)) => add_error(app, &format!("Failed to read plugins: {error}")),
        Event::Reloaded(result) => {
            app.plugins.reloading = false;
            match result {
                Ok(None) => add_status(app, NO_PLUGIN_BACKEND),
                Ok(Some((changes, catalog))) => {
                    add_status(app, &text::reload_report(&changes, &catalog));
                    if app.plugins.open {
                        app.plugins.catalog = catalog;
                        reset_highlight(app);
                    }
                }
                Err(error) => add_error(app, &format!("Failed to reload plugins: {error}")),
            }
        }
    }
}

fn open(app: &mut App, catalog: PluginCatalogState) {
    if app.plugins.open {
        return;
    }
    app.plugins = State {
        open: true,
        catalog,
        ..fresh(app)
    };
    reset_highlight(app);
}

/// A closed browser; an in-flight reload outlives it.
fn fresh(app: &App) -> State {
    State {
        reloading: app.plugins.reloading,
        ..State::default()
    }
}

/// Rebuild the view: leave a detail a reload removed, highlight the first plugin.
pub(crate) fn reset_highlight(app: &mut App) {
    if rows::viewing_entry(&app.plugins).is_none() {
        app.plugins.viewing = None;
    }
    let first = rows::rows(&app.plugins)
        .iter()
        .position(Row::selectable)
        .unwrap_or(0);
    app.plugins.list.select(first);
    app.plugins.list.reset_viewport();
}

/// The open browser owns keys.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if filter::handle_key(app, key) {
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
        // Esc backs out one level: the detail to the list, then closes.
        KeyCode::Esc if app.plugins.viewing.is_some() => back(app),
        KeyCode::Esc => close(app),
        KeyCode::Up | KeyCode::Char('k') => navigate(app, -1, true),
        KeyCode::Down | KeyCode::Char('j') => navigate(app, 1, true),
        KeyCode::PageUp => navigate(app, -page(app), false),
        KeyCode::PageDown => navigate(app, page(app), false),
        KeyCode::Home => navigate(app, -EDGE, false),
        KeyCode::End => navigate(app, EDGE, false),
        KeyCode::Enter => select(app),
        KeyCode::Char('r') => reload(app, client),
        _ => {}
    }
}

fn close(app: &mut App) {
    app.plugins = fresh(app);
}

fn back(app: &mut App) {
    if app.plugins.viewing.take().is_some() {
        reset_highlight(app);
    }
}

fn select(app: &mut App) {
    let rows = rows::rows(&app.plugins);
    let Some(Row::Entry(index)) = rows.get(app.plugins.list.selected()) else {
        return;
    };
    app.plugins.viewing = Some(app.plugins.catalog.plugins[*index].name.clone());
    app.plugins.filter.focused = false;
    reset_highlight(app);
}

/// Option list moves: arrows wrap around, pages and Home/End clamp; the detail view scrolls instead.
fn navigate(app: &mut App, delta: isize, wrap: bool) {
    let list = &mut app.plugins.list;
    if app.plugins.viewing.is_some() {
        list.detach_at(list.scroll().saturating_add_signed(delta));
        return;
    }
    let selectable: Vec<usize> = rows::rows(&app.plugins)
        .iter()
        .enumerate()
        .filter_map(|(index, row)| row.selectable().then_some(index))
        .collect();
    let Some(last) = selectable.len().checked_sub(1) else {
        return;
    };
    let current = selectable
        .iter()
        .position(|index| *index == app.plugins.list.selected())
        .unwrap_or(0) as isize;
    let target = current.saturating_add(delta);
    let target = if wrap {
        target.rem_euclid(last as isize + 1)
    } else {
        target.clamp(0, last as isize)
    };
    app.plugins.list.select(selectable[target as usize]);
}

/// One page of the option list, as last rendered.
fn page(app: &App) -> isize {
    app.plugins.list_area.height.max(1) as isize
}

/// The wheel scrolls the viewport without moving the highlight.
pub fn wheel(app: &mut App, down: bool) {
    app.plugins.list.wheel(down, WHEEL_STEP);
}

/// A press on the search field focuses it; a click on a plugin opens it, like Enter.
pub fn mouse(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    let pressed = event.kind == MouseEventKind::Down(MouseButton::Left);
    let on_search = app.plugins.viewing.is_none() && app.plugins.filter.area.contains(at.into());
    let click =
        crate::mouse::option_gesture(app, event, RegionId::BottomApp, |app| &mut app.plugins.list);
    if on_search && pressed {
        app.plugins.filter.focused = true;
        return;
    }
    if pressed && app.plugins.list.index_at(at).is_some() {
        app.plugins.filter.focused = false;
    }
    if click.is_some() {
        select(app);
    }
}

fn add_status(app: &mut App, text: &str) {
    local::add_command_status(&mut app.view.transcript, &new_message_id(), text);
}

fn add_error(app: &mut App, text: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), text);
}
