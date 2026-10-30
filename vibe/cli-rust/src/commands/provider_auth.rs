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

/// `/status`: statistics always render; the section is additive.
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

/// The `providerAuth/read` auth view, or `None` when the read fails.
pub(super) async fn read(client: &Client, session_id: &str) -> Option<Value> {
    match client
        .request(method::PROVIDER_AUTH_READ, json!({"sessionId": session_id}))
        .await
    {
        Ok(mut value) => Some(value.get_mut("auth")?.take()),
        Err(error) => {
            tracing::warn!(%error, "provider auth read failed");
            None
        }
    }
}

/// The `/whoami` line naming the credential source in use, e.g.
/// `- **Credential source (MISTRAL_API_KEY)**: Process environment`.
pub fn active_source_line(view: &Value) -> Option<String> {
    if view.pointer("/authKind").and_then(Value::as_str) != Some("api_key") {
        return None;
    }
    let in_use = sources(view)
        .into_iter()
        .find(|source| source.state == "in_use")?;
    let env_var = view
        .pointer("/apiKeyEnvVar")
        .and_then(Value::as_str)
        .unwrap_or("");
    let env_file_path = view
        .pointer("/envFilePath")
        .and_then(Value::as_str)
        .unwrap_or("");
    Some(format!(
        "- **Credential source ({})**: {}",
        literal(env_var),
        source_label(&in_use, env_file_path)
    ))
}

/// Mount the `/status` message, whose exact counts render muted.
pub(crate) fn add_agent_statistics(app: &mut App, id: &str, text: &str) {
    local::add_message(&mut app.view.transcript, id, "agent_statistics", text);
}

/// The command text from a `providerAuth/read` outcome: statistics always
/// render; the section is additive. A failed read keeps the statistics-only
/// output — no error, no empty section.
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

/// The section text.
pub fn render_provider_auth_section(view: &Value) -> String {
    let field = |key: &str| {
        view.pointer(&format!("/{key}"))
            .and_then(Value::as_str)
            .unwrap_or("")
    };
    let mut lines = vec![
        "## Model & Provider".to_owned(),
        String::new(),
        format!("- **Model**: {}", literal(field("modelDisplayName"))),
        format!("- **Provider**: {}", literal(field("providerName"))),
        render_api_base(view),
    ];
    lines.extend(render_credential(view));
    lines.join("\n")
}

fn render_api_base(view: &Value) -> String {
    match view.pointer("/apiBase").and_then(Value::as_str) {
        // The view already sanitized or refused the base; `None` is invalid.
        Some(base) => format!("- **API base**: {}", literal(base)),
        None => "- **API base**: Invalid API URL".to_owned(),
    }
}

/// One credential source entry (the server's `ProviderCredentialSourceView`).
struct Source {
    kind: String,
    state: String,
}

fn sources(view: &Value) -> Vec<Source> {
    view.pointer("/sources")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| {
                    Some(Source {
                        kind: entry.get("kind")?.as_str()?.to_owned(),
                        state: entry
                            .get("state")
                            .and_then(Value::as_str)
                            .unwrap_or("available")
                            .to_owned(),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// The `$VIBE_HOME/.env` label, home abbreviated to `~`.
fn env_file_display(path: &str) -> String {
    let Some(home) = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(|home| home.to_string_lossy().into_owned())
    else {
        return path.to_owned();
    };
    // A separator check keeps a sibling of home from being abbreviated: it
    // shares the string prefix but is not under home.
    if path == home || path.starts_with(&format!("{home}{}", std::path::MAIN_SEPARATOR)) {
        return format!("~{}", &path[home.len()..]);
    }
    path.to_owned()
}

fn source_label(source: &Source, env_file_path: &str) -> String {
    let label = match source.kind.as_str() {
        "vibe_home_env_file" => env_file_display(env_file_path),
        "process_env" => "Process environment".to_owned(),
        "os_keyring" => "OS keyring".to_owned(),
        other => other.to_owned(),
    };
    // The `.env` path is dynamic (`VIBE_HOME` is user-controlled), so every
    // label goes through the same literal rendering as the other fields.
    literal(&label)
}

fn source_marker(source: &Source) -> &'static str {
    match source.state.as_str() {
        "in_use" => " · currently in use",
        "unavailable" => " · unavailable",
        _ => "",
    }
}

/// The credential line(s).
fn render_credential(view: &Value) -> Vec<String> {
    match view.pointer("/authKind").and_then(Value::as_str) {
        Some("api_key") => render_api_key_credential(view),
        Some("google_adc") => vec![
            "- **Credential**: Google Application Default Credentials · resolved on request"
                .to_owned(),
        ],
        // `none` — and anything unrecognized — omits the field entirely.
        _ => Vec::new(),
    }
}

fn render_api_key_credential(view: &Value) -> Vec<String> {
    let env_var = literal(
        view.pointer("/apiKeyEnvVar")
            .and_then(Value::as_str)
            .unwrap_or(""),
    );
    let label = format!("- **Credential ({env_var})**");
    let env_file_path = view
        .pointer("/envFilePath")
        .and_then(Value::as_str)
        .unwrap_or("");
    let entries = sources(view);
    if entries.is_empty() {
        return vec![format!("{label}: no source available")];
    }
    // One in-use source needs no disambiguation: the credential is that
    // source, so the line itself says it and no marker follows.
    if entries.len() == 1 && entries[0].state == "in_use" {
        return vec![format!(
            "{label}: {}",
            source_label(&entries[0], env_file_path)
        )];
    }
    // Several sources: the list keeps the precedence order visible, and the
    // markers say which one a model call would actually use.
    let mut lines = vec![format!("{label}:")];
    lines.extend(entries.iter().enumerate().map(|(index, source)| {
        format!(
            "  {}. {}{}",
            index + 1,
            source_label(source, env_file_path),
            source_marker(source)
        )
    }));
    lines
}

/// Escape markdown so a dynamic value renders literally.
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
