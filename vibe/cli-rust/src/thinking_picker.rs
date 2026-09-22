//! `/thinking` picker state. Mirrors Python's `ThinkingPickerApp`: a bottom-panel
//! option list over the five thinking levels, `›` on the current one, Enter
//! persists via `config/write` + `config/reload`.

use serde_json::Value;

use crate::app::App;

/// The five thinking levels, in the canonical order from `THINKING_LEVELS`.
pub const THINKING_LEVELS: [&str; 5] = ["off", "low", "medium", "high", "max"];

/// A committed selection's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by the follow-up `config/reload`.
    Reloaded(Value),
    /// The write or reload was rejected or failed; show why (Python
    /// `_run_settings_update`).
    Failed(String),
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Reloaded(runtime) => {
            crate::event_handler::apply_runtime_value(app, &runtime);
            crate::transcript::local::add_status(
                &mut app.view.transcript,
                &crate::commands::submission::new_message_id(),
                crate::commands::event::RELOADED_MESSAGE,
            );
        }
        Event::Failed(error) => {
            crate::transcript::local::add_command_error(
                &mut app.view.transcript,
                &crate::commands::submission::new_message_id(),
                &error,
            );
        }
    }
    app.commit_finished();
}

/// Open the picker highlighted on the current thinking level.
pub fn open(app: &mut App) {
    app.thinking_picker.selected = index_of(&app.thinking_picker.current_level);
    app.thinking_picker.scroll = 0;
    app.thinking_picker.open = true;
}

/// Move the highlight up/down by one, wrapping at the ends (Textual OptionList).
pub fn navigate(app: &mut App, down: bool) {
    let len = THINKING_LEVELS.len();
    app.thinking_picker.selected = if down {
        (app.thinking_picker.selected + 1) % len
    } else {
        (app.thinking_picker.selected + len - 1) % len
    };
}

/// Close the picker without persisting (Esc / cancel).
pub fn cancel(app: &mut App) {
    app.thinking_picker.open = false;
}

/// Whether row `i` is the current thinking level.
pub fn is_current(app: &App, i: usize) -> bool {
    THINKING_LEVELS[i] == app.thinking_picker.current_level
}

/// The level to persist for the highlighted row.
pub fn selected_level(app: &App) -> &'static str {
    THINKING_LEVELS[app.thinking_picker.selected.min(THINKING_LEVELS.len() - 1)]
}

/// Refresh the current thinking level from a `runtime/read`/`runtime/updated`
/// value (shape `{runtime: {config: {activeModel: {thinking: ...}}}}`).
pub fn apply_runtime(app: &mut App, response: &Value) {
    if let Some(level) = response
        .pointer("/runtime/config/activeModel/thinking")
        .and_then(Value::as_str)
    {
        app.thinking_picker.current_level = level.to_owned();
    }
}

fn index_of(level: &str) -> usize {
    THINKING_LEVELS
        .iter()
        .position(|it| *it == level)
        .unwrap_or(0)
}
