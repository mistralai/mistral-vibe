//! Option rows, detail static and help line of the connector auth app.

use super::{OptionId, Row};
use crate::app::App;

/// Indent of the three action options (Python `_OPTION_PADDING`).
const OPTION_PADDING: &str = "  ";

/// The option rows: fetching, then either the auth actions or a plain note.
pub fn rows(app: &App) -> Vec<Row> {
    if !app.connector_auth.fetched {
        return vec![Row::Note("Fetching authentication info...".to_owned())];
    }
    if app.connector_auth.auth_url.is_none() {
        return vec![Row::Note(
            "This connector does not provide authentication".to_owned(),
        )];
    }
    vec![
        Row::Note("This connector requires authentication".to_owned()),
        Row::Blank,
        Row::Action {
            id: OptionId::Open,
            before: format!("{OPTION_PADDING}Press "),
            key: "Enter".to_owned(),
            after: " to open auth in your browser".to_owned(),
        },
        Row::Action {
            id: OptionId::Copy,
            before: format!("{OPTION_PADDING}Copy URL to clipboard"),
            key: String::new(),
            after: String::new(),
        },
        Row::Action {
            id: OptionId::Show,
            before: format!("{OPTION_PADDING}Manually show the URL"),
            key: String::new(),
            after: String::new(),
        },
    ]
}

/// The detail static, as `(text, is shortcut)` runs (Python `_update_detail_text`).
pub fn detail(app: &App) -> Vec<(String, bool)> {
    let Some(url) = app.connector_auth.auth_url.as_deref() else {
        return vec![(String::new(), false)];
    };
    let mut parts: Vec<(String, bool)> = Vec::new();
    if app.connector_auth.auth_url_visible {
        parts.push((format!("{url}\n\n"), false));
    }
    parts.push(("Once authenticated, press ".to_owned(), false));
    parts.push(("r".to_owned(), true));
    parts.push((" to refresh".to_owned(), false));
    parts
}

/// The help line, prefixed with the current status (Python `_set_help_text`).
pub fn help_text(app: &App) -> Vec<(String, bool)> {
    let mut spans: Vec<(String, bool)> = Vec::new();
    if let Some(status) = &app.connector_auth.status_message {
        spans.push((format!("{status}  "), false));
    }
    spans.push(("Backspace".to_owned(), true));
    spans.push((" Back".to_owned(), false));
    spans
}
