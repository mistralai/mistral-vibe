//! `/theme` picker state. Mirrors Python's `ThemePickerApp`: an option list of
//! `sorted_theme_names()` with live preview on highlight and persist on select.

use std::time::{Duration, Instant};

use crate::app::App;
use crate::ui::theme;

/// Delay between the highlight moving and the preview applying (Python
/// `PREVIEW_DEBOUNCE_SECONDS`), so held arrow keys do not repaint per step.
pub const PREVIEW_DEBOUNCE: Duration = Duration::from_millis(100);

/// The picker options: `auto` first, then every theme in `theme::ALL` order
/// (which matches Textual's `sorted_theme_names()`).
pub fn options() -> Vec<&'static str> {
    let mut names = vec![theme::AUTO_NAME];
    names.extend(theme::ALL.iter().map(|t| t.name));
    names
}

/// Open the picker highlighted on the configured theme (Python `on_mount`).
pub fn open(app: &mut App) {
    let names = options();
    let configured = app.session.startup_config.theme.as_str();
    let current = names
        .iter()
        .position(|name| *name == configured)
        .unwrap_or(0);
    app.theme_picker.current = current;
    app.theme_picker.original = theme::active_index();
    app.theme_picker.selected = current;
    app.theme_picker.scroll = 0;
    app.theme_picker.free_scroll = false;
    app.theme_picker.preview_at = None;
    app.theme_picker.open = true;
}

/// Move the highlight up/down by one, clamped (Textual's `OptionList` does not
/// wrap), then arm the debounced preview of the newly highlighted theme.
pub fn navigate(app: &mut App, down: bool) {
    app.theme_picker.free_scroll = false;
    let last = options().len() - 1;
    if down {
        app.theme_picker.selected = (app.theme_picker.selected + 1).min(last);
    } else {
        app.theme_picker.selected = app.theme_picker.selected.saturating_sub(1);
    }
    app.theme_picker.preview_at = Some(Instant::now() + PREVIEW_DEBOUNCE);
}

/// Apply the highlighted option's theme so the whole UI repaints in it.
pub fn preview(app: &mut App) {
    app.theme_picker.preview_at = None;
    if let Some(index) = theme::resolve(selected_name(app)) {
        theme::set_active_index(index);
    }
}

/// The highlighted option's theme name (or `auto`).
pub fn selected_name(app: &App) -> &'static str {
    options()[app.theme_picker.selected.min(options().len() - 1)]
}

/// A committed selection's server answer, applied on the main thread.
pub enum Event {
    /// Theme name echoed by the `config/write` response.
    Applied(String),
    /// The write failed; restore the unchanged configured theme.
    Failed,
}

/// Apply the server's answer and release the commit that was in flight.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Applied(name) => {
            theme::set_active(&name);
            app.session.startup_config.theme = name;
        }
        Event::Failed => {
            tracing::warn!(theme = selected_name(app), "failed to persist theme");
            theme::set_active(&app.session.startup_config.theme);
        }
    }
    app.commit_finished();
}

/// Restore the theme active before the picker opened (Esc / cancel).
pub fn cancel(app: &mut App) {
    theme::set_active_index(app.theme_picker.original);
    app.theme_picker.preview_at = None;
    app.theme_picker.open = false;
}
