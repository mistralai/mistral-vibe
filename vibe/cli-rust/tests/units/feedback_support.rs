//! Shared helpers for the inline notice and the conversation's feedback rows.

use vibe_rs::app::App;

use crate::support::messages;

/// The inline notice's text, if one is up.
pub fn notice_text(app: &App) -> Option<&str> {
    app.overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str())
}

/// Every `✕` row's text, in order.
pub fn errors(app: &App) -> Vec<String> {
    rows_of(app, "command_error")
}

/// Every `⚠` row's text, in order.
pub fn warnings(app: &App) -> Vec<String> {
    rows_of(app, "command_warning")
}

fn rows_of(app: &App, role: &str) -> Vec<String> {
    messages(app)
        .into_iter()
        .filter(|(row_role, _)| row_role == role)
        .map(|(_, text)| text)
        .collect()
}
