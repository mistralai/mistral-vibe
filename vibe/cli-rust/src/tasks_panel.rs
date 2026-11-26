//! The background tasks window the rail's tasks item opens: lists the running processes, Enter shows one's output, `s` stops one.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::option_list::{MultiAction, OptionList};
use crate::server::request_error::describe;
use crate::server::{method, BackgroundProcessStopParams, Client};

pub mod find;
pub mod follow;
pub mod output;
mod output_keys;

pub use follow::Page;
pub use output::{Appended, Output, Row};
pub use output_keys::{paste, typing};

/// The highlighted row of the tasks window, and what it shows.
#[derive(Default)]
pub struct TasksPanel {
    pub list: OptionList<MultiAction>,
    pub view: TasksView,
    generation: u64,
}

/// The task list, or one task's output in its place.
#[derive(Default)]
pub enum TasksView {
    #[default]
    List,
    Output(Box<OutputView>),
}

/// A task's output with its search.
pub struct OutputView {
    pub output: Output,
    pub search: crate::search_field::Search,
    pub find: find::Find,
}

impl OutputView {
    /// Add a page, keeping the search's matches on their text.
    pub fn append(&mut self, bytes: &[u8]) -> Appended {
        let appended = self.output.append(bytes);
        self.find.appended(&self.output, appended.from);
        appended
    }

    /// Drop the output shown for a read that jumps to the latest one, its matches with it.
    pub fn restart(&mut self) -> Appended {
        let appended = self.output.restart();
        self.find.appended(&self.output, appended.from);
        appended
    }
}

impl TasksPanel {
    /// The output the window shows instead of the list.
    pub fn shown(&self) -> Option<&OutputView> {
        match &self.view {
            TasksView::Output(view) => Some(view),
            TasksView::List => None,
        }
    }

    pub fn shown_mut(&mut self) -> Option<&mut OutputView> {
        match &mut self.view {
            TasksView::Output(view) => Some(view),
            TasksView::List => None,
        }
    }

    /// Back to the list; false when it already shows.
    pub fn close_output(&mut self) -> bool {
        matches!(std::mem::take(&mut self.view), TasksView::Output(_))
    }
}

/// The row of the task the output shows, while it still runs.
pub fn shown_index(app: &App) -> Option<usize> {
    let shown = app.tasks_panel.shown()?;
    let tasks = &app.session.background_tasks;
    tasks
        .iter()
        .position(|task| task.process_id == shown.output.process_id)
}

/// Keep the highlight on a row that still exists.
pub fn sync(app: &mut App) {
    let len = app.session.background_tasks.len();
    let selected = app.tasks_panel.list.selected().min(len.saturating_sub(1));
    app.tasks_panel.list.select(selected);
}

/// Keys of the window: the output's, or the list's arrows with wrap, Enter to show a task's output and `s` to stop it; false for an Esc that closes the window.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) -> bool {
    if app.tasks_panel.shown().is_some() {
        output_keys::output_key(app, client, key);
        return true;
    }
    let len = app.session.background_tasks.len();
    let plain = key.modifiers.is_empty();
    let down = match key.code {
        KeyCode::Up => false,
        KeyCode::Down => true,
        KeyCode::Char('k') if plain => false,
        KeyCode::Char('j') if plain => true,
        KeyCode::Enter => return show_output(app, client),
        KeyCode::Char('s') if plain => {
            stop(app, client, app.tasks_panel.list.selected());
            return true;
        }
        KeyCode::Esc => return false,
        _ => return true,
    };
    app.tasks_panel.list.step(down, len, true, |_| true);
    true
}

/// Show the highlighted task's output in the list's place.
pub fn show_output(app: &mut App, client: &Arc<Client>) -> bool {
    let Some(task) = app
        .session
        .background_tasks
        .get(app.tasks_panel.list.selected())
    else {
        return true;
    };
    let (process_id, command) = (task.process_id.clone(), task.command.clone());
    crate::selection::clear_region(app, crate::selection::RegionId::RailWindow);
    app.rail.scroll = crate::rail::Scroll::End;
    let panel = &mut app.tasks_panel;
    panel.generation += 1;
    panel.view = TasksView::Output(Box::new(OutputView {
        output: Output::new(process_id, command, panel.generation),
        search: crate::search_field::Search::default(),
        find: find::Find::default(),
    }));
    follow::request(app, client, follow::Read::Latest);
    true
}

/// Leave the output back to the list; false when the list shows, so Esc closes the window.
pub fn back(app: &mut App) -> bool {
    crate::rail::window(app) == Some(crate::rail::Item::Tasks) && app.tasks_panel.close_output()
}

/// Ask the server to stop the task at `index`; it leaves the list at once, and a failure says so as the next snapshot brings it back.
pub fn stop(app: &mut App, client: &Arc<Client>, index: usize) {
    if index >= app.session.background_tasks.len() {
        return;
    }
    let task = app.session.background_tasks.remove(index);
    crate::background_work::sync_wait_target(app);
    sync(app);
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let params = BackgroundProcessStopParams {
        session_id,
        process_id: task.process_id,
    };
    let (client, tx) = (client.clone(), app.command_tx.clone());
    crate::work::spawn(async move {
        let value = serde_json::to_value(params).unwrap_or_default();
        let Err(error) = client.request(method::BACKGROUND_PROCESS_STOP, value).await else {
            return;
        };
        let message = format!("Failed to stop {}: {}", task.command, describe(&error));
        crate::input::deliver(tx, CommandEvent::Error(message)).await;
    });
}
