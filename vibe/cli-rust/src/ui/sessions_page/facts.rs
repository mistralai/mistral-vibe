//! What the preview says about the highlighted session above its conversation: how it began, where it works, what runs it, what it spent, who started it.

use ratatui::style::Style;
use unicode_width::UnicodeWidthStr;

use super::super::theme;
use crate::app::App;
use crate::sessions_page::Session;
use crate::utils::paths::collapse_home;
use crate::utils::text::{ellipsize, format_compact_count};

/// The label column of a fact.
const LABEL: usize = 11;

/// The session's facts as `(label, value)` rows, most telling first; empty values are left out.
pub(super) fn of(app: &App, session: &Session) -> Vec<(&'static str, String)> {
    let page = &app.sessions_page;
    let folder = match (
        session.cwd.as_deref().map(collapse_home),
        session.cwd_missing,
    ) {
        (Some(folder), true) => format!("{folder} (deleted)"),
        (folder, _) => folder.unwrap_or_default(),
    };
    let model = model(app, session);
    let tokens = session
        .token_usage
        .filter(|usage| usage.input_tokens + usage.output_tokens > 0)
        .map(|usage| {
            format!(
                "{} in · {} out",
                format_compact_count(usage.input_tokens),
                format_compact_count(usage.output_tokens)
            )
        })
        .unwrap_or_default();
    let starter = session
        .started_by_session_id
        .as_deref()
        .and_then(|id| page.sessions.iter().find(|other| other.id == id))
        .map(|starter| starter.label().to_owned())
        .unwrap_or_default();
    let first = crate::utils::text::first_line(&session.preview)
        .trim()
        .to_owned();
    // The first prompt says what the session was about, unless the title is it.
    let first = match first == session.label() {
        true => String::new(),
        false => first,
    };
    [
        ("First", first),
        ("Folder", folder),
        ("Branch", session.branch().unwrap_or_default().to_owned()),
        ("Model", model),
        ("Tokens", tokens),
        ("Started by", starter),
    ]
    .into_iter()
    .filter(|(_, value)| !value.is_empty())
    .collect()
}

/// The model the session runs, by its display name, then its reasoning; a
/// session that pins none follows the default model.
fn model(app: &App, session: &Session) -> String {
    let picker = &app.model_picker;
    let name = match session.model.as_deref() {
        Some(alias) => picker
            .models
            .iter()
            .find(|model| model.alias == alias)
            .map_or(alias, |model| model.display_name.as_str())
            .to_owned(),
        None if picker.default_display_name.is_empty() => String::new(),
        None => format!("{} (default)", picker.default_display_name),
    };
    match (name.is_empty(), session.reasoning_effort.as_deref()) {
        (false, Some(effort)) => format!("{name} · {effort} reasoning"),
        (true, Some(effort)) => format!("{effort} reasoning"),
        _ => name,
    }
}

/// One fact as a painted row of `width` cells: a muted label, then the value, cut with `…`.
pub(super) fn row(label: &str, value: &str, width: usize) -> Vec<(String, Style)> {
    let value = ellipsize(value, width.saturating_sub(LABEL));
    let label = ellipsize(label, width.min(LABEL));
    let pad = LABEL.min(width).saturating_sub(label.width());
    vec![
        (format!("{label}{}", " ".repeat(pad)), theme::muted_style()),
        (value, theme::text(theme::foreground())),
    ]
}
