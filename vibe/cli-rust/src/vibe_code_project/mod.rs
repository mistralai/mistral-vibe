//! Remote-project picker state and server-result reduction.

mod field;
pub use field::{Field, MAX_INPUT_BYTES};
pub mod input;
pub mod items;
mod request;

use std::sync::Arc;

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::server::{proto_projects::*, Client};
use crate::transcript::local;
use items::{build_project_picker_items, Item};
use request::Operation;

pub const MAX_PROJECTS: usize = 10_000;
#[derive(Default)]
pub struct State {
    pub open: bool,
    pub pending: bool,
    pub cancel_requested: bool,
    pub session_id: String,
    pub picker_id: String,
    pub view: Option<PickerView>,
    pub query: Field,
    pub clipboard: String,
    pub items: Vec<Item>,
    pub selected: usize,
    pub scroll: usize,
    pub free_scroll: bool,
    pub search_focused: bool,
    pub create: Option<Create>,
    pub list_area: ratatui::layout::Rect,
    pub search_area: ratatui::layout::Rect,
    pub branch_area: ratatui::layout::Rect,
    pub pressed: Option<usize>,
    pub dragged_field: Option<FieldSlot>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum FieldSlot {
    Primary,
    Branch,
}

pub struct Create {
    pub name: Field,
    pub branch: Field,
    pub branch_focused: bool,
}

pub struct Reply {
    pub session_id: String,
    pub picker_id: String,
    pub event: Event,
}

pub enum Event {
    Opened(OpenResponse),
    Loaded(LoadMoreResponse),
    Created(ProjectResponse),
    Selected(ProjectResponse),
    Unlinked,
    Cancelled,
    CancelFailed(String),
    Error(String),
}

impl State {
    pub fn refresh(&mut self, focus: Option<String>) {
        let Some(view) = &self.view else { return };
        let previous = focus.or_else(|| {
            self.items
                .get(self.selected)
                .and_then(|i| i.option_id(view))
        });
        self.items = build_project_picker_items(view, &self.query.text);
        self.selected = previous
            .and_then(|id| {
                self.items
                    .iter()
                    .position(|i| i.option_id(view).as_deref() == Some(&id))
            })
            .unwrap_or_else(|| self.items.iter().position(Item::selectable).unwrap_or(0));
        self.free_scroll = false;
    }

    pub fn show_picker(&mut self) {
        self.create = None;
        self.query = Field::default();
        self.items.clear();
        self.scroll = 0;
        self.search_focused = true;
        self.pressed = None;
        self.dragged_field = None;
        self.refresh(None);
    }

    pub(crate) fn selectable_index_at(&self, at: (u16, u16)) -> Option<usize> {
        if !self.list_area.contains(at.into()) {
            return None;
        }
        let index = self.scroll + at.1.saturating_sub(self.list_area.y) as usize;
        self.items
            .get(index)
            .is_some_and(Item::selectable)
            .then_some(index)
    }

    pub fn page(&mut self, down: bool) {
        if self.items.is_empty() {
            return;
        }
        let height = self.list_area.height as usize;
        let target = if down {
            self.selected.saturating_add(height)
        } else {
            self.selected.saturating_sub(height)
        }
        .min(self.items.len().saturating_sub(1));
        let selected = if down {
            (target..self.items.len()).find(|&i| self.items[i].selectable())
        } else {
            (0..=target).rev().find(|&i| self.items[i].selectable())
        };
        if let Some(selected) = selected {
            self.selected = selected;
            self.free_scroll = false;
        }
    }

    pub fn navigate(&mut self, down: bool) {
        let len = self.items.len();
        let Some(selected) = (1..=len)
            .map(|offset| {
                if down {
                    (self.selected + offset) % len
                } else {
                    (self.selected + len - offset) % len
                }
            })
            .find(|&index| self.items[index].selectable())
        else {
            return;
        };
        self.selected = selected;
        self.search_focused = false;
        self.free_scroll = false;
    }
}

pub fn open(app: &mut App, client: &Arc<Client>) {
    if app.vibe_code_project.pending {
        return;
    }
    crate::commands::usage::record_usage(app, client, "remote-project".into(), "builtin");
    app.vibe_code_project = State::default();
    request::start(app, client, Operation::Open);
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, reply: Reply) {
    if app.vibe_code_project.session_id != reply.session_id
        || app.vibe_code_project.picker_id != reply.picker_id
    {
        return;
    }
    if app.session.session_id.as_deref() != Some(&reply.session_id) {
        app.vibe_code_project = State::default();
        return;
    }
    app.vibe_code_project.pending = false;
    let cancel = std::mem::take(&mut app.vibe_code_project.cancel_requested);
    match reply.event {
        Event::Opened(response) => {
            let state = &mut app.vibe_code_project;
            state.picker_id = response.picker_id;
            state.view = Some(response.view);
            state.open = true;
            state.show_picker();
            if cancel {
                request::start(app, client, Operation::Cancel);
            }
        }
        Event::Loaded(response) => {
            let state = &mut app.vibe_code_project;
            let focus = response.focus_option_id.or_else(|| {
                state
                    .view
                    .as_ref()
                    .and_then(|v| state.items.get(state.selected).and_then(|i| i.option_id(v)))
            });
            state.view = Some(response.view);
            state.refresh(focus);
            if cancel {
                request::start(app, client, Operation::Cancel);
            }
        }
        Event::Created(response) => {
            app.vibe_code_project.view = Some(response.view);
            if cancel {
                app.vibe_code_project.show_picker();
            } else {
                request::start(app, client, Operation::Select(response.project.project_id));
            }
        }
        Event::Selected(response) => {
            result(
                app,
                &format!(
                    "Linked this repository to Vibe Code project **{}**.",
                    response.project.name
                ),
            );
            app.vibe_code_project = State::default();
        }
        Event::Unlinked => {
            result(app, "Remote Vibe Code project link cleared.");
            app.vibe_code_project = State::default();
        }
        Event::Cancelled => app.vibe_code_project = State::default(),
        Event::CancelFailed(error) => {
            app.vibe_code_project = State::default();
            local::add_command_error(&mut app.view.transcript, &new_message_id(), &error);
        }
        Event::Error(error) => {
            local::add_command_error(&mut app.view.transcript, &new_message_id(), &error);
            if cancel && app.vibe_code_project.create.is_some() {
                app.vibe_code_project.show_picker();
            } else if cancel && app.vibe_code_project.open {
                request::start(app, client, Operation::Cancel);
            }
        }
    }
}

pub fn select(app: &mut App, client: &Arc<Client>) {
    let state = &mut app.vibe_code_project;
    if state.pending {
        return;
    }
    let Some(view) = &state.view else { return };
    let Some(item) = state
        .items
        .get(state.selected)
        .filter(|i| i.selectable())
        .or_else(|| state.items.iter().find(|i| i.selectable()))
    else {
        return;
    };
    let operation = match item {
        Item::Project { index, .. } => {
            Operation::Select(view.state.projects[*index].project_id.clone())
        }
        Item::LoadMore => Operation::LoadMore,
        Item::Unlink => Operation::Unlink,
        Item::Create { name, .. } => {
            state.create = Some(Create {
                name: Field::new(name.clone()),
                branch: Field::new(view.git.suggested_default_branch().into()),
                branch_focused: false,
            });
            return;
        }
        Item::Section(_) => return,
    };
    request::start(app, client, operation);
}

fn result(app: &mut App, text: &str) {
    local::add_command_result(&mut app.view.transcript, &new_message_id(), text);
}
