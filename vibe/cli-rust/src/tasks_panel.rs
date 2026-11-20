//! The background tasks window the rail's tasks item opens: lists the running processes, `s` stops one.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::option_list::{OptionList, SingleAction};
use crate::server::request_error::describe;
use crate::server::{method, BackgroundProcessStopParams, Client};

/// The highlighted row of the tasks window.
#[derive(Default)]
pub struct TasksPanel {
    pub list: OptionList<SingleAction>,
}

/// Keep the highlight on a row that still exists.
pub fn sync(app: &mut App) {
    let len = app.session.background_tasks.len();
    let selected = app.tasks_panel.list.selected().min(len.saturating_sub(1));
    app.tasks_panel.list.select(selected);
}

/// Arrows move with wrap, `s` stops the highlighted task; Enter does nothing, so a stray one never kills a process.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let len = app.session.background_tasks.len();
    let plain = key.modifiers.is_empty();
    let down = match key.code {
        KeyCode::Up => false,
        KeyCode::Down => true,
        KeyCode::Char('k') if plain => false,
        KeyCode::Char('j') if plain => true,
        KeyCode::Char('s') if plain => return stop(app, client, app.tasks_panel.list.selected()),
        _ => return,
    };
    app.tasks_panel.list.step(down, len, true, |_| true);
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
