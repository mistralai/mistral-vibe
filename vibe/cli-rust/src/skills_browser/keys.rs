//! The `/skills` keys and mouse, screen by screen.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::list::Row;
use super::page::{self, Property};
use super::picker::PickerRow;
use super::{bodies, close, list, picker, Screen, State};
use crate::app::App;
use crate::mouse::option_gesture;
use crate::option_list::{Click, ClickRule, OptionList, SingleAction};
use crate::search_field::{self, Outcome, Press};
use crate::selection::{self, RegionId};
use crate::server::Client;
use crate::tab_bar;

/// The open browser owns keys; a page and a picker ignore chords.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match app.skills_browser.screen() {
        Screen::List => list_key(app, client, key),
        _ if chorded(&key) => {}
        Screen::Page(_) => page_key(app, client, key),
        Screen::Picker(..) => picker_key(app, client, key),
    }
    bodies::sync(app, client);
}

/// The list: Esc first disarms a removal, then the search field; `d` arms a removal, Enter waits while it is armed, and any other key disarms it.
fn list_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let armed = app.skills_browser.removing.take();
    if key.code == KeyCode::Esc && armed.is_some() {
        return;
    }
    match search_field::handle_key(&mut app.skills_browser.search, &key) {
        Outcome::Consumed => return,
        Outcome::Filtered => return list::reset(&mut app.skills_browser),
        Outcome::Pass if chorded(&key) => return,
        Outcome::Pass => {}
    }
    let state = &mut app.skills_browser;
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Enter if armed.is_some() => state.removing = armed,
        KeyCode::Enter => {
            state.search.focused = false;
            list::activate(app, client);
        }
        KeyCode::Char(' ') => list::toggle(app, client),
        KeyCode::Char('d') => list::remove(app, client, armed),
        KeyCode::Tab => {
            let other = state.tab.other();
            list::open_tab(state, other);
        }
        code => navigate(state, code),
    }
}

/// A page has no search: Esc disarms Remove or steps back, Space only switches from Enabled, pages scroll the body.
fn page_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let state = &mut app.skills_browser;
    let armed = state.removing.take();
    let Some(page) = state.page.as_ref() else {
        return;
    };
    let selected = page.list.selected();
    let on_enabled = page.rows.get(selected) == Some(&Property::Enabled);
    match key.code {
        KeyCode::Esc if armed.is_some() => {}
        KeyCode::Esc => page::back(app),
        KeyCode::Enter => page::activate(app, client, selected, armed),
        KeyCode::Char(' ') if on_enabled => page::activate(app, client, selected, None),
        KeyCode::PageUp | KeyCode::PageDown => state.pane.page(key.code == KeyCode::PageDown),
        code => navigate(state, code),
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

/// The list follows its click rule; a click on a page or picker row acts at once, but only Enter confirms a removal, and the second press of a double-click that opened a screen lands nowhere.
pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    if dropped(&mut app.skills_browser, &event) {
        return;
    }
    let place = |state: &State| (state.screen().kind(), state.opening.is_some());
    let before = place(&app.skills_browser);
    match app.skills_browser.screen() {
        Screen::List => list_mouse(app, client, event),
        Screen::Page(_) => {
            if let Some(index) = option_gesture(app, event, RegionId::BottomApp, detail_list) {
                app.skills_browser.removing = None;
                page::activate(app, client, index, None);
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

/// A press on a tab opens it, one on the search field focuses it; any press on the field or a row disarms a removal.
fn list_mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    if let Some(tab) = tab_bar::pressed(&app.skills_browser.tab_areas, &event) {
        let state = &mut app.skills_browser;
        state.removing = None;
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
    if press != Press::Other {
        state.removing = None;
    }
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
