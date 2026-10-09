//! The `/status` "Model & Provider" section, from `providerAuth/read`.

use std::sync::Arc;

use serde_json::{json, Value};

use super::event::{dispatch, CommandEvent};
use super::simple::status_text;
use super::submission::new_message_id;
use crate::app::App;
use crate::server::{method, Client};
use crate::transcript::local;

/// Markdown punctuation that changes inline parsing when left bare.
const MD_INLINE: &[char] = &[
    '\\', '`', '*', '_', '.', ':', '<', '>', '&', '!', '#', '[', ']',
];

/// `/status`: statistics always render; the section is additive (Python `_show_status`).
pub(super) fn status(app: &mut App, client: &Arc<Client>) {
    let stats = status_text(app);
    let Some((session_id, tx)) = dispatch(app) else {
        add_agent_statistics(app, &new_message_id(), &stats);
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let read = client
            .request(method::PROVIDER_AUTH_READ, json!({"sessionId": session_id}))
            .await;
        let _ = tx.try_send(CommandEvent::AgentStatistics(attach(stats, read)));
    });
}

/// Mount the `/status` message, whose exact counts render muted (Python `.agent-statistics`).
pub(crate) fn add_agent_statistics(app: &mut App, id: &str, text: &str) {
    local::add_message(&mut app.view.transcript, id, "agent_statistics", text);
}

/// The command text from a `providerAuth/read` outcome: statistics always
/// render; the section is additive. A failed read keeps the statistics-only
/// output — no error, no empty section (Python `_provider_auth_section`).
pub fn attach(stats: String, read: anyhow::Result<Value>) -> String {
    match read {
        Ok(value) => match value.pointer("/auth") {
            Some(auth) => format!("{stats}\n{}", render_provider_auth_section(auth)),
            None => stats,
        },
        Err(error) => {
            // The failure text stays in the log; the transcript never grows
            // an error for this read.
            tracing::warn!(%error, "provider auth status read failed");
            stats
        }
    }
}

/// The section text (Python `render_provider_auth_section`).
pub fn render_provider_auth_section(view: &Value) -> String {
    let field = |key: &str| {
        view.pointer(&format!("/{key}"))
            .and_then(Value::as_str)
            .unwrap_or("")
    };
    [
        "## Model & Provider".to_owned(),
        String::new(),
        format!("- **Model**: {}", literal(field("modelDisplayName"))),
        format!("- **Provider**: {}", literal(field("providerName"))),
        render_api_base(view),
    ]
    .join("\n")
}

fn render_api_base(view: &Value) -> String {
    match view.pointer("/apiBase").and_then(Value::as_str) {
        // The view already sanitized or refused the base; `None` is invalid.
        Some(base) => format!("- **API base**: {}", literal(base)),
        None => "- **API base**: Invalid API URL".to_owned(),
    }
}

/// Escape markdown so a dynamic value renders literally (Python `_literal`).
pub fn literal(value: &str) -> String {
    // Control characters (Cc, including the C1 range) render as a space: a
    // newline would start a new markdown line and could inject structure.
    let cleaned: Vec<char> = value
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let mut out = String::with_capacity(cleaned.len());
    for (index, &c) in cleaned.iter().enumerate() {
        // A strikethrough needs a `~~` pair, so a tilde escapes only next to
        // another one; escaping every tilde would deface a path-like value.
        let beside_tilde = (index > 0 && cleaned[index - 1] == '~')
            || (index + 1 < cleaned.len() && cleaned[index + 1] == '~');
        if c == '~' && beside_tilde || MD_INLINE.contains(&c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}
