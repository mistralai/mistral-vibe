//! Local and single-RPC slash commands (`/help`, `/status`, `/log`, `/whoami`, ...).

use std::sync::Arc;

use crate::server::method;
use crate::server::Client;
use serde_json::Value;

use super::event::{dispatch, reload_result, CommandEvent};
use super::submission::{new_message_id, NOTICE_TIMEOUT_SECS};
use crate::app::App;
use crate::post_ready::{self, AccountReads};
use crate::transcript::local;
use crate::ui;

pub(super) const DATA_RETENTION_MESSAGE: &str = "## Your Data Helps Improve Mistral AI\n\nAt Mistral AI, we're committed to delivering the best possible experience. When you use Mistral models on our API, your interactions may be collected to improve our models, ensuring they stay cutting-edge, accurate, and helpful.\n\nManage your data settings [here](https://chat.mistral.ai/work?profile_dialog=privacy)";

pub fn help_text() -> String {
    let mut lines = vec![
        "### Keyboard Shortcuts".to_owned(),
        String::new(),
        "- `Enter` Submit message".to_owned(),
        "- `Ctrl+J` / `Shift+Enter` Insert newline".to_owned(),
        "- `Escape` Interrupt agent or close dialogs".to_owned(),
        "- `Ctrl+C` Quit (or clear input if text present)".to_owned(),
        "- `Ctrl+G` Edit input in external editor".to_owned(),
        "- `Ctrl+O` Toggle tool output view".to_owned(),
        "- `Shift+Tab` Cycle through agents (ask, plan, ...)".to_owned(),
        "- `Esc Esc` Rewind to a previous message (when input is empty)".to_owned(),
        String::new(),
        "### Special Features".to_owned(),
        String::new(),
        "- `!<command>` Execute bash command directly".to_owned(),
        "- `@path/to/file/` Autocompletes file paths".to_owned(),
        String::new(),
        "### Commands".to_owned(),
        String::new(),
    ];
    let mut commands = super::builtin();
    commands.sort_by(|a, b| a.0.cmp(&b.0));
    for (aliases, description) in commands {
        lines.push(format!("- {aliases}: {description}"));
    }
    lines.join("\n")
}

pub(crate) fn add_text(app: &mut App, text: &str) {
    local::add_command_result(&mut app.view.transcript, &new_message_id(), text);
}

pub(super) fn copy_last_agent_message(app: &mut App) {
    let Some(text) = app.view.transcript.last_assistant_message() else {
        ui::notice::show(
            app,
            "No agent message available to copy",
            NOTICE_TIMEOUT_SECS,
        );
        return;
    };
    crate::clipboard::copy_to_clipboard(&text);
    ui::notice::show(
        app,
        "Last agent message copied to clipboard",
        NOTICE_TIMEOUT_SECS,
    );
}

pub(super) fn status_text(app: &App) -> String {
    let stats = &app.session.stats;
    let session_cached = if stats.session_cached_tokens > 0 {
        format!(" _(including {} cached)_", stats.session_cached_tokens)
    } else {
        String::new()
    };
    let last_turn_cached = if stats.last_turn_cached_tokens > 0 {
        format!(" _(including {} cached)_", stats.last_turn_cached_tokens)
    } else {
        String::new()
    };
    format!(
        "## Agent Statistics\n\n- **Steps**: {}\n- **Session Prompt Tokens**: {}{session_cached}\n- **Session Completion Tokens**: {}\n- **Session Total LLM Tokens**: {}\n- **Last Turn Tokens**: {}{last_turn_cached}\n- **Cost**: ${:.4}",
        stats.steps,
        stats.session_prompt_tokens,
        stats.session_completion_tokens,
        stats.session_prompt_tokens + stats.session_completion_tokens,
        stats.last_turn_total_tokens,
        stats.session_cost,
    )
}

pub(super) fn read_log(app: &mut App, client: &Arc<Client>) {
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let result = client
            .request(
                method::SESSION_LOG_READ,
                serde_json::json!({"sessionId": session_id}),
            )
            .await;
        let result = match result {
            Ok(value) if value.pointer("/log/enabled") == Some(&serde_json::Value::Bool(false)) => {
                CommandEvent::Error("Session logging is disabled in configuration.".to_owned())
            }
            Ok(value) if value.pointer("/log/persisted") != Some(&serde_json::Value::Bool(true)) => {
                CommandEvent::Error("The current session has not been persisted yet.".to_owned())
            }
            Ok(value) => CommandEvent::Result(value
                .pointer("/log/path")
                .and_then(serde_json::Value::as_str)
                .map(|path| format!("## Current Log Directory\n\n`{path}`\n\nYou can send this directory to share your interaction."))
                .unwrap_or_else(|| "The current session has not been persisted yet.".to_owned())),
            Err(error) => CommandEvent::Error(format!("Failed to read session log: {error}")),
        };
        let _ = tx.try_send(result);
    });
}

pub(super) fn rename_session(app: &mut App, client: &Arc<Client>, value: &str) {
    let title = value
        .split_once(char::is_whitespace)
        .map(|(_, value)| value.trim())
        .unwrap_or("");
    if title.is_empty() {
        if let Some(tx) = app.command_tx.clone() {
            app.commit_started();
            let _ = tx.try_send(CommandEvent::Result("Usage: /rename <title>".to_owned()));
        }
        return;
    }
    let title = title.to_owned();
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let result = client
            .request(
                method::SESSION_RENAME,
                serde_json::json!({"sessionId": session_id, "title": title}),
            )
            .await;
        let event = match result {
            Ok(value) => value
                .get("title")
                .and_then(serde_json::Value::as_str)
                .map(|title| CommandEvent::Renamed(title.to_owned()))
                .unwrap_or_else(|| {
                    CommandEvent::Result("Failed to rename session: invalid response".to_owned())
                }),
            Err(error) => CommandEvent::Result(format!("Failed to rename session: {error}")),
        };
        let _ = tx.try_send(event);
    });
}

pub(super) fn reload_config(app: &mut App, client: &Arc<Client>) {
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let result = client
            .request(
                method::CONFIG_RELOAD,
                serde_json::json!({"sessionId": session_id, "reloadRuntime": true}),
            )
            .await;
        let event = match result {
            Ok(value) => reload_result(value),
            Err(error) => CommandEvent::Result(format!("Failed to reload config: {error}")),
        };
        let _ = tx.try_send(event);
    });
}

pub(super) fn whoami(app: &mut App, client: &Arc<Client>) {
    // The post-ready fetch already read both, so answer from that cache; only a
    // model switch (a different credential) sends us back to the server.
    let cached = app
        .whoami
        .hit(&app.session.startup_config.active_model)
        .map(whoami_text);
    if let Some(text) = cached {
        add_text(app, &text);
        return;
    }
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    app.begin_command_loading();
    let client = client.clone();
    tokio::spawn(async move {
        let reads = post_ready::read(&client, &session_id).await;
        let _ = tx.try_send(CommandEvent::Whoami(reads));
    });
}

/// Python `IdentityView.name`: full name, else first name, else the email. The
/// wire carries `firstName`/`lastName` only, since `name` is a bare property.
fn identity_name(identity: &Value, email: Option<&str>) -> Option<String> {
    let part = |key: &str| {
        identity
            .get(key)
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty())
    };
    match (part("firstName"), part("lastName")) {
        (Some(first), Some(last)) => Some(format!("{first} {last}")),
        (Some(first), None) => Some(first.to_owned()),
        _ => email.map(str::to_owned),
    }
}

/// The `/whoami` body, or Python's fallback when the active model has no identity.
pub fn whoami_text(reads: &AccountReads) -> String {
    let identity = &reads.identity;
    if identity.is_null() {
        return "## Who am I\n\nNo identity information is available for the active model."
            .to_owned();
    }
    let mut lines = vec!["## Who am I".to_owned(), String::new()];
    let email = identity
        .get("email")
        .and_then(Value::as_str)
        .filter(|email| !email.is_empty());
    let name = identity_name(identity, email);
    if let Some(name) = name.filter(|name| Some(name.as_str()) != email) {
        lines.push(format!("- **Name**: {name}"));
    }
    if let Some(email) = email {
        lines.push(format!("- **Email**: {email}"));
    }
    for (label, key) in [("Workspace", "workspace"), ("Organization", "organization")] {
        if let Some(name) = identity
            .pointer(&format!("/{key}/name"))
            .and_then(Value::as_str)
        {
            lines.push(format!("- **{label}**: {name}"));
        }
    }
    if let Some(plan) = post_ready::plan_title(&reads.account) {
        lines.push(format!("- **Plan**: {plan}"));
    }
    lines.join("\n")
}
