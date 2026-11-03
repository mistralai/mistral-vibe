//! Warnings and user-action feedback: a startup box, then `⚠`/`✕` rows, echoed when hidden.

use std::collections::HashSet;

use serde_json::Value;

use crate::app::{App, Severity, Status};
use crate::commands::submission::new_message_id;
use crate::focus::Focus;
use crate::transcript::local;
use crate::ui::notice::Notice;
use crate::utils::clean;

/// The startup box's title, drawn in its frame.
pub const TITLE: &str = "Warnings";

/// Warnings collected before the first turn; once one starts, warnings mount as rows.
#[derive(Default)]
pub struct StartupNotices {
    /// The mounted startup box.
    id: Option<String>,
    /// Its warnings, in arrival order.
    items: Vec<String>,
    /// Set by the first turn.
    closed: bool,
}

/// Mount every config issue and validation warning of the startup snapshot.
pub fn show_config_issues(app: &mut App, runtime: &Value) {
    let snapshot = crate::server::RuntimeSnapshot::read(runtime).unwrap_or_default();
    for issue in snapshot.issues {
        match issue.file.as_str() {
            "" => warn(app, &issue.message),
            file => warn(app, &format!("{file}: {}", issue.message)),
        }
    }
    show_validation_warnings(app, runtime);
}

/// Mount validation warnings that appear after startup, once the session is ready.
pub fn show_updated_config_warnings(app: &mut App, runtime: &Value) {
    // Pre-ready snapshots still carry the config a catalog fetch may resolve.
    if !matches!(
        app.session.status,
        Status::Ready | Status::Generating { .. }
    ) {
        return;
    }
    show_validation_warnings(app, runtime);
}

/// Mount each validation warning once; a warning that disappears is forgotten so it can show again.
fn show_validation_warnings(app: &mut App, runtime: &Value) {
    let Some(config) = crate::server::RuntimeSnapshot::config(runtime) else {
        return;
    };
    let warnings = config.validation_warnings;
    let present: HashSet<&String> = warnings.iter().collect();
    app.shown_config_warnings
        .retain(|shown| present.contains(shown));
    for warning in warnings {
        if app.shown_config_warnings.insert(warning.clone()) {
            warn(app, &warning);
        }
    }
}

/// A server or config warning: into the startup box, or a warning row once a turn ran.
pub fn warn(app: &mut App, message: &str) {
    let message = clean::clean_output(message.trim());
    if message.is_empty() {
        return;
    }
    if app.startup_notices.closed {
        let id = new_message_id();
        local::add_message(&mut app.view.transcript, &id, "notice_warning", &message);
        return;
    }
    let notices = &mut app.startup_notices;
    if notices.items.contains(&message) {
        return;
    }
    notices.items.push(message);
    sync(app);
}

/// Mount or refresh the startup box, remounting it when a restored transcript dropped it.
pub fn sync(app: &mut App) {
    let notices = &mut app.startup_notices;
    if notices.closed || notices.items.is_empty() {
        return;
    }
    let text = body(&notices.items);
    let transcript = &mut app.view.transcript;
    match notices.id.as_deref().filter(|id| transcript.contains(id)) {
        Some(id) => local::set_text(transcript, id, &text),
        None => {
            let id = new_message_id();
            local::add_message(transcript, &id, "startup_notices", &text);
            notices.id = Some(id);
        }
    }
}

/// A server error: always its own row, so it is never filed among the startup warnings.
pub fn error(app: &mut App, message: &str) {
    let message = clean::clean_output(message.trim());
    if !message.is_empty() {
        let id = new_message_id();
        local::add_message(&mut app.view.transcript, &id, "notice_error", &message);
    }
}

/// A `/clear` starts a fresh session: its warnings collect in a new box until its first turn.
pub fn clear(app: &mut App) {
    app.startup_notices = StartupNotices::default();
}

/// A user action that failed: a `✕` row in the conversation, kept for the record.
pub fn failed(app: &mut App, message: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), message);
    echo_if_hidden(app, Severity::Error, message);
}

/// A user action refused for an expected reason: a `⚠` row in the conversation.
pub fn refused(app: &mut App, message: &str) {
    // A refusal repeated by the same key stays one row.
    if !last_row_is(app, "command_warning", message) {
        local::add_command_warning(&mut app.view.transcript, &new_message_id(), message);
    }
    echo_if_hidden(app, Severity::Warning, message);
}

fn last_row_is(app: &App, role: &str, message: &str) -> bool {
    app.view.transcript.last_shown_row().is_some_and(|raw| {
        raw["role"] == role && raw["content"][0]["text"].as_str() == Some(message)
    })
}

/// Repeat the row where the user looks when the main conversation is not on screen.
fn echo_if_hidden(app: &mut App, severity: Severity, message: &str) {
    match app.focus() {
        Focus::Config => app.config_screen.status = Some(Notice::new(severity, message, None)),
        // Release notes stay open: closing them under the user's typing would steal focus.
        // Only the base screen swaps the conversation for the viewed subagent's.
        Focus::SubagentList | Focus::Composer if app.subagents.viewed_subagent_id.is_some() => {
            crate::ui::notice::show_sticky(app, severity, message)
        }
        _ => {}
    }
}

/// The first turn closes the startup box: later warnings belong to the conversation.
pub fn close_startup(app: &mut App) {
    app.startup_notices.closed = true;
}

/// The box body: one bullet per warning, its text escaped so markdown never rewrites it.
fn body(items: &[String]) -> String {
    items
        .iter()
        .map(|item| format!("- {}", escape(&item.replace('\n', " "))))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Backslash-escape every ASCII punctuation character, which CommonMark then prints literally.
fn escape(text: &str) -> String {
    text.chars()
        .flat_map(|c| {
            c.is_ascii_punctuation()
                .then_some('\\')
                .into_iter()
                .chain([c])
        })
        .collect()
}
