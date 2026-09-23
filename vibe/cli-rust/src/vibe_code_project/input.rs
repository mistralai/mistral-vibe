//! Project-picker keyboard, paste, and pointer input.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::{
    request::{self, Operation},
    Field, FieldSlot, State,
};
use crate::app::App;
use crate::chat_input::Action;
use crate::selection::{self, Release};
use crate::server::Client;

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    app.overlays.last_escape = None;
    if app.vibe_code_project.pending {
        app.vibe_code_project.cancel_requested |= key.code == KeyCode::Esc;
        return;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        match key.code {
            KeyCode::Char('x') => {
                copy_selection(app, true);
                return;
            }
            KeyCode::Char('v') => {
                let text = app.vibe_code_project.clipboard.clone();
                paste(app, &text);
                return;
            }
            KeyCode::Char('a') if key.modifiers.contains(KeyModifiers::SHIFT) => {
                if let Some(field) = focused_field(&mut app.vibe_code_project) {
                    field.edit(Action::SelectAll);
                }
                return;
            }
            _ => {}
        }
    }
    if key.code == KeyCode::Esc {
        if app.vibe_code_project.create.is_some() {
            app.vibe_code_project.show_picker();
        } else {
            request::start(app, client, Operation::Cancel);
        }
        return;
    }
    if matches!(key.code, KeyCode::Tab | KeyCode::BackTab) {
        let state = &mut app.vibe_code_project;
        if let Some(create) = &mut state.create {
            create.branch_focused = !create.branch_focused;
        } else {
            state.search_focused = !state.search_focused;
        }
        if let Some(field) = focused_field(state) {
            field.edit(Action::SelectAll);
        }
        return;
    }
    if key.code == KeyCode::Enter {
        if let Some(create) = &app.vibe_code_project.create {
            let name = create.name.text.trim().to_owned();
            let branch = create.branch.text.trim().to_owned();
            if !name.is_empty() && !branch.is_empty() {
                request::start(app, client, Operation::Create { name, branch });
            }
        } else {
            super::select(app, client);
        }
        return;
    }
    let state = &mut app.vibe_code_project;
    if state.create.is_none() {
        let plain = !key
            .modifiers
            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
        match key.code {
            KeyCode::Up => {
                state.navigate(false);
                return;
            }
            KeyCode::Down => {
                state.navigate(true);
                return;
            }
            KeyCode::Char('j' | 'k') if plain && !state.search_focused => {
                state.navigate(key.code == KeyCode::Char('j'));
                return;
            }
            KeyCode::Char('/') if plain && !state.search_focused => {
                state.search_focused = true;
                state.query.edit(Action::SelectAll);
                return;
            }
            KeyCode::PageUp | KeyCode::PageDown if !state.search_focused => {
                state.page(key.code == KeyCode::PageDown);
                return;
            }
            KeyCode::Home | KeyCode::End if !state.search_focused => {
                let index = if key.code == KeyCode::Home {
                    state.items.iter().position(|i| i.selectable())
                } else {
                    state.items.iter().rposition(|i| i.selectable())
                };
                state.selected = index.unwrap_or(0);
                state.free_scroll = false;
                return;
            }
            _ => {}
        }
    }
    if let Some(action) = crate::keymap::action_for(&key) {
        if let Some(field) = focused_field(state) {
            field.edit(action);
        }
        if state.create.is_none() {
            state.refresh(None);
        }
    }
}

pub fn paste(app: &mut App, text: &str) {
    let state = &mut app.vibe_code_project;
    if state.pending {
        return;
    }
    if let Some(field) = focused_field(state) {
        for c in text.lines().next().unwrap_or_default().chars() {
            field.edit(Action::Insert(c));
        }
    }
    if state.create.is_none() {
        state.refresh(None);
    }
}

pub fn copy_selection(app: &mut App, cut: bool) -> bool {
    let state = &mut app.vibe_code_project;
    if !state.open || state.pending {
        return false;
    }
    let Some(field) = focused_field(state) else {
        return false;
    };
    let Some(text) = crate::chat_input::selected_text(&field.text, field.cursor, field.anchor)
    else {
        return false;
    };
    crate::clipboard::copy_to_clipboard(&text);
    if cut {
        field.edit(Action::DeleteLeft);
    }
    state.clipboard = text;
    if cut && state.create.is_none() {
        state.refresh(None);
    }
    true
}

fn focused_field(state: &mut State) -> Option<&mut Field> {
    if let Some(create) = &mut state.create {
        return Some(if create.branch_focused {
            &mut create.branch
        } else {
            &mut create.name
        });
    }
    state.search_focused.then_some(&mut state.query)
}

pub fn wheel(app: &mut App, up: bool) {
    let state = &mut app.vibe_code_project;
    state.free_scroll = true;
    state.scroll = if up {
        state.scroll.saturating_sub(2)
    } else {
        state.scroll.saturating_add(2)
    };
}

pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    let release = match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            selection::press_including_padding(app, at);
            None
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            selection::drag(app, at);
            None
        }
        MouseEventKind::Up(MouseButton::Left) => Some(selection::release(app)),
        _ => None,
    };
    let state = &mut app.vibe_code_project;
    if state.pending {
        return;
    }
    let at = at.into();
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            let slot = if state.branch_area.contains(at) {
                Some(FieldSlot::Branch)
            } else if state.search_area.contains(at) {
                Some(FieldSlot::Primary)
            } else {
                None
            };
            if let Some(slot) = slot {
                if let Some(create) = &mut state.create {
                    create.branch_focused = slot == FieldSlot::Branch;
                }
                state.search_focused = true;
                state.pressed = None;
                state.dragged_field = Some(slot);
                move_field_cursor(state, slot, event.column, true);
            } else if let Some(index) = state.selectable_index_at((event.column, event.row)) {
                state.selected = index;
                state.search_focused = false;
                state.pressed = Some(index);
                state.dragged_field = None;
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            if let Some(slot) = state.dragged_field {
                move_field_cursor(state, slot, event.column, false);
            }
        }
        MouseEventKind::Up(MouseButton::Left) => {
            if state.dragged_field.take().is_some() {
                return;
            }
            if matches!(release, Some(Release::Selected)) {
                state.pressed = None;
                return;
            }
            let index = state.selectable_index_at((event.column, event.row));
            if state.pressed.take() == index && index.is_some() {
                super::select(app, client);
            }
        }
        _ => {}
    }
}

fn move_field_cursor(state: &mut State, slot: FieldSlot, column: u16, anchor: bool) {
    let area = match slot {
        FieldSlot::Primary => state.search_area,
        FieldSlot::Branch => state.branch_area,
    };
    let column = column.saturating_sub(area.x) as usize;
    let Some(field) = field_for_slot(state, slot) else {
        return;
    };
    field.cursor = field.byte_at(column);
    if anchor {
        field.anchor = Some(field.cursor);
    }
}

fn field_for_slot(state: &mut State, slot: FieldSlot) -> Option<&mut Field> {
    match slot {
        FieldSlot::Primary => Some(match &mut state.create {
            Some(create) => &mut create.name,
            None => &mut state.query,
        }),
        FieldSlot::Branch => state.create.as_mut().map(|create| &mut create.branch),
    }
}
