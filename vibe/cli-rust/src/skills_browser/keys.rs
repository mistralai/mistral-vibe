//! The `/skills` keys and mouse, screen by screen.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::list::Row;
use super::page;
use super::picker::PickerRow;
use super::{bodies, close, files, list, picker, Screen, State};
use crate::app::App;
use crate::mouse::option_gesture;
use crate::option_list::{Click, ClickRule, OptionList, SingleAction};
use crate::search_field::{self, Outcome, Press};
use crate::selection::{self, RegionId};
use crate::server::Client;
use crate::tab_bar;

/// The open browser owns keys: a pending deletion takes them first; a page and a picker ignore chords.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if app.skills_browser.confirm.is_some() {
        return confirm_key(app, client, key);
    }
    match app.skills_browser.screen() {
        Screen::List => list_key(app, client, key),
        _ if chorded(&key) => {}
        Screen::Page(_) => page_key(app, client, key),
        Screen::Picker(..) => picker_key(app, client, key),
    }
    bodies::sync(app, client);
}

/// The deletion confirmation: ←→ or Tab move between Delete and Cancel, Enter does the focused one, Esc cancels.
fn confirm_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let Some(confirm) = app.skills_browser.confirm.as_mut() else {
        return;
    };
    match key.code {
        KeyCode::Esc => app.skills_browser.confirm = None,
        KeyCode::Enter => files::confirm(app, client),
        KeyCode::Left | KeyCode::Right | KeyCode::Tab | KeyCode::BackTab => {
            confirm.delete_focused = !confirm.delete_focused;
        }
        _ => {}
    }
}

/// The keys every screen with a skill on it takes: switch, add, edit, remove, delete.
fn skill_key(app: &mut App, client: &Arc<Client>, code: KeyCode) -> bool {
    match code {
        KeyCode::Char(' ') => files::toggle(app, client),
        KeyCode::Char('a') => files::add(app, client),
        KeyCode::Char('e') => files::edit(app),
        KeyCode::Char('r') => files::remove(app, client),
        KeyCode::Char('x') => files::ask_delete(app),
        _ => return false,
    }
    true
}

/// The list: Esc leaves the search field first, then closes; the skill keys act on the highlighted row.
fn list_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match search_field::handle_key(&mut app.skills_browser.search, &key) {
        Outcome::Consumed => return,
        Outcome::Filtered => return list::reset(&mut app.skills_browser),
        Outcome::Pass if chorded(&key) => return,
        Outcome::Pass => {}
    }
    if skill_key(app, client, key.code) {
        return;
    }
    let state = &mut app.skills_browser;
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Enter => {
            state.search.focused = false;
            list::activate(app, client);
        }
        KeyCode::Char('n') => files::create(app),
        KeyCode::Tab | KeyCode::BackTab | KeyCode::Left | KeyCode::Right => {
            let other = state.tab.other();
            list::open_tab(state, other);
        }
        code => navigate(state, code),
    }
}

/// A page has no search: Esc steps back, Enter acts on the highlighted scope, the skill keys on the page's skill, pages scroll the body; a page with at most one row scrolls the body with the arrows too.
fn page_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if skill_key(app, client, key.code) {
        return;
    }
    let state = &mut app.skills_browser;
    let Some(page) = state.page.as_ref() else {
        return;
    };
    let selected = page.list.selected();
    let movable = page::movable(state, page);
    match key.code {
        KeyCode::Esc => page::back(app),
        KeyCode::Enter => page::activate(app, client, selected),
        KeyCode::PageUp | KeyCode::PageDown => state.pane.page(key.code == KeyCode::PageDown),
        code if !movable => scroll_body(state, code),
        code => navigate(state, code),
    }
}

/// The arrows and j/k scroll the body a line, Home and End to its ends.
fn scroll_body(state: &mut State, code: KeyCode) {
    let pane = &mut state.pane;
    match code {
        KeyCode::Up | KeyCode::Char('k') => pane.scroll_by(false, 1),
        KeyCode::Down | KeyCode::Char('j') => pane.scroll_by(true, 1),
        KeyCode::Home => pane.scroll_to(0),
        KeyCode::End => pane.scroll_to(usize::MAX),
        _ => {}
    }
}

/// A picker previews the version it lands on; pages scroll the preview.
fn picker_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let state = &mut app.skills_browser;
    let Some(open) = state.page.as_ref().and_then(|page| page.picker.as_ref()) else {
        return;
    };
    let selected = open.list.selected();
    match key.code {
        KeyCode::Esc => page::back(app),
        KeyCode::Enter => picker::activate(app, client, selected),
        KeyCode::PageUp | KeyCode::PageDown => state.pane.page(key.code == KeyCode::PageDown),
        code => navigate(state, code),
    }
}

/// Bindings never fire with a modifier held; Shift is how some layouts type `/`.
fn chorded(key: &KeyEvent) -> bool {
    key.modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
}

/// The arrows and j/k wrap around the list on screen; pages of it, as last rendered, and Home/End stop at its ends.
fn navigate(state: &mut State, code: KeyCode) {
    let page = usize::from(state.list_area.height.max(1));
    let (down, steps, wrap) = match code {
        KeyCode::Up | KeyCode::Char('k') => (false, 1, true),
        KeyCode::Down | KeyCode::Char('j') => (true, 1, true),
        KeyCode::PageUp => (false, page, false),
        KeyCode::PageDown => (true, page, false),
        KeyCode::Home => (false, usize::MAX, false),
        KeyCode::End => (true, usize::MAX, false),
        _ => return,
    };
    let takes = takes(state);
    match state.page.as_mut() {
        Some(page) => step(page.visible_list(), &takes, down, steps, wrap),
        None => step(&mut state.list, &takes, down, steps, wrap),
    }
}

/// Which rows of the list on screen take the highlight.
fn takes(state: &State) -> Vec<bool> {
    match state.screen() {
        Screen::List => state.rows().iter().map(Row::selectable).collect(),
        Screen::Page(page) => page::selectable(state, page),
        Screen::Picker(_, open) => open.rows.iter().map(PickerRow::selectable).collect(),
    }
}

/// Move the highlight over `steps` rows that take it, until an end it does not wrap at.
fn step<R: ClickRule>(
    list: &mut OptionList<R>,
    takes: &[bool],
    down: bool,
    steps: usize,
    wrap: bool,
) {
    for _ in 0..steps {
        if !list.step(down, takes.len(), wrap, |index| takes[index]) {
            break;
        }
    }
}

/// The list follows its click rule; a click on a page or picker row acts at once, a pending deletion ignores the mouse, and the second press of a double-click that opened a screen lands nowhere.
pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    if app.skills_browser.confirm.is_some() || dropped(&mut app.skills_browser, &event) {
        return;
    }
    let place = |state: &State| (state.screen().kind(), state.opening.is_some());
    let before = place(&app.skills_browser);
    match app.skills_browser.screen() {
        Screen::List => list_mouse(app, client, event),
        Screen::Page(_) => {
            if let Some(index) = option_gesture(app, event, RegionId::BottomApp, detail_list) {
                page::activate(app, client, index);
            }
        }
        Screen::Picker(..) => {
            if let Some(index) = option_gesture(app, event, RegionId::BottomApp, detail_list) {
                picker::activate(app, client, index);
            }
        }
    }
    if place(&app.skills_browser) != before {
        let cell = (event.column, event.row);
        app.skills_browser.screen_click = Some((cell, crate::clock::now()));
    }
    bodies::sync(app, client);
}

/// Whether `event` belongs to the second press of a double-click whose first click opened a screen: that press and its release are dropped.
fn dropped(state: &mut State, event: &MouseEvent) -> bool {
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            let cell = (event.column, event.row);
            let last = state.screen_click.take();
            state.dropping = last.is_some_and(|(at, when)| {
                at == cell && selection::chained(when, crate::clock::now())
            });
            state.dropping
        }
        MouseEventKind::Drag(MouseButton::Left) => state.dropping,
        MouseEventKind::Up(MouseButton::Left) => std::mem::take(&mut state.dropping),
        _ => false,
    }
}

/// A press on a tab opens it, one on the search field focuses it.
fn list_mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    if let Some(tab) = tab_bar::pressed(&app.skills_browser.tab_areas, &event) {
        let state = &mut app.skills_browser;
        if tab != state.tab {
            list::open_tab(state, tab);
        }
        return;
    }
    let click = option_gesture(app, event, RegionId::BottomApp, |app| {
        &mut app.skills_browser.list
    });
    let state = &mut app.skills_browser;
    let on_option = state.list.index_at(at).is_some();
    let press = search_field::press(&mut state.search, &event, true, on_option);
    if press == Press::Field {
        return;
    }
    if let Some(Click::Confirm(_)) = click {
        list::activate(app, client);
    }
}

/// The page's list, or its picker's while one is open.
fn detail_list(app: &mut App) -> &mut OptionList<SingleAction> {
    let page = app.skills_browser.page.as_mut();
    page.expect("a skill page is open").visible_list()
}

/// Paste into a focused search field; the pane follows the new highlight.
pub fn paste(app: &mut App, client: &Arc<Client>, text: &str) {
    let state = &mut app.skills_browser;
    if state.page.is_some() || !search_field::paste(&mut state.search, text) {
        return;
    }
    list::reset(state);
    bodies::sync(app, client);
}
