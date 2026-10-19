//! Project-picker keyboard, paste, and pointer input.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::{
    request::{self, Operation},
    Field, FieldSlot, State,
};
use crate::app::App;
use crate::chat_input::Action;
use crate::focus::Focus;
use crate::selection::{self, RegionId};
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
        escape(app, client);
        return;
    }
    if matches!(key.code, KeyCode::Tab | KeyCode::BackTab) {
        let state = &mut app.vibe_code_project;
        if let Some(create) = &mut state.create {
            create.branch_focused = !create.branch_focused;
        }
        if let Some(field) = state
            .create
            .is_some()
            .then(|| focused_field(state))
            .flatten()
        {
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
                return;
            }
            KeyCode::PageUp | KeyCode::PageDown => {
                state.page(key.code == KeyCode::PageDown);
                return;
            }
            KeyCode::Home | KeyCode::End if !state.search_focused => {
                let index = if key.code == KeyCode::Home {
                    state.items.iter().position(|i| i.selectable())
                } else {
                    state.items.iter().rposition(|i| i.selectable())
                };
                state.list.select(index.unwrap_or(0));
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

/// Esc backs out one level: create form, search field, search filter, then the picker.
fn escape(app: &mut App, client: &Arc<Client>) {
    let state = &mut app.vibe_code_project;
    if state.create.is_some() {
        state.show_picker();
    } else if state.search_focused {
        state.search_focused = false;
    } else if !state.query.text.is_empty() {
        state.query = Field::default();
        state.refresh(None);
    } else {
        request::start(app, client, Operation::Cancel);
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
    if app.focus() != Focus::VibeCodeProject || app.vibe_code_project.pending {
        return false;
    }
    let state = &mut app.vibe_code_project;
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

/// A press on a text field moves its caret; anything else is an option-list
/// gesture, ignored while a request is pending.
pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    let state = &app.vibe_code_project;
    let field = match event.kind {
        MouseEventKind::Down(MouseButton::Left) if !state.pending => field_at(state, at),
        _ => state.dragged_field,
    };
    if let Some(slot) = field {
        field_mouse(app, event, slot);
        return;
    }
    let click = crate::mouse::option_gesture(app, event, RegionId::BottomApp, |app| {
        &mut app.vibe_code_project.list
    });
    let state = &mut app.vibe_code_project;
    if state.pending {
        state.list.cancel();
        return;
    }
    if event.kind == MouseEventKind::Down(MouseButton::Left) && state.list.index_at(at).is_some() {
        state.search_focused = false;
    }
    if click.is_some() {
        super::select(app, client);
    }
}

fn field_at(state: &State, at: (u16, u16)) -> Option<FieldSlot> {
    if state.branch_area.contains(at.into()) {
        Some(FieldSlot::Branch)
    } else if state.search_area.contains(at.into()) {
        Some(FieldSlot::Primary)
    } else {
        None
    }
}

/// A gesture started on a text field focuses it and drags its caret.
fn field_mouse(app: &mut App, event: MouseEvent, slot: FieldSlot) {
    let at = (event.column, event.row);
    let state = &mut app.vibe_code_project;
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            if let Some(create) = &mut state.create {
                create.branch_focused = slot == FieldSlot::Branch;
            }
            state.search_focused = true;
            state.list.cancel();
            state.dragged_field = Some(slot);
            move_field_cursor(state, slot, event.column, true);
            selection::press_owned(app, at, RegionId::BottomApp);
        }
        MouseEventKind::Drag(MouseButton::Left) => {
            move_field_cursor(state, slot, event.column, false);
            selection::drag(app, at);
        }
        MouseEventKind::Up(MouseButton::Left) => {
            state.dragged_field = None;
            selection::release(app);
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
