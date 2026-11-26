//! Hiding the credentials a dumped config may hold, with the app server's
//! rules (`vibe/app_server/_config_introspect.py`, `vibe/app_server/run_export.py`).

use std::path::Path;

use serde_json::Value;

/// The placeholder the app server's redaction writes over credentials.
const REDACTED: &str = "<redacted>";
/// Config entries whose values are credentials as often as not: header and
/// environment mappings, and the command line of a local MCP server.
const SECRET_KEYS: [&str; 4] = ["headers", "extra_headers", "env", "args"];
/// Only an MCP server's command is redacted: tool configs may have their own.
const MCP_SERVERS_KEY: &str = "mcp_servers";
const MCP_COMMAND_KEY: &str = "command";

/// Hide the credentials a dumped config may hold, keeping its shape, as the
/// app server's `redact_config` does.
pub fn redact_config(config: Value) -> Value {
    redact_value(config, false)
}

/// Redact the config snapshot a copied session journal holds. A snapshot
/// that cannot be parsed cannot be redacted either, so it is dropped.
pub(super) fn redact_metadata_config(path: &Path) {
    let Ok(text) = std::fs::read_to_string(path) else {
        return;
    };
    let Ok(mut metadata) = serde_json::from_str::<Value>(&text) else {
        tracing::warn!(
            "Dropping the copied {}: it cannot be parsed, so it cannot be redacted",
            path.display()
        );
        let _ = std::fs::remove_file(path);
        return;
    };
    let Some(config) = metadata.get_mut("config") else {
        return;
    };
    if !config.is_object() {
        return;
    }
    *config = redact_config(std::mem::take(config));
    // Replace rather than write through, in case the copy kept a symlink.
    let _ = std::fs::remove_file(path);
    match serde_json::to_string_pretty(&metadata) {
        Ok(json) => {
            let _ = std::fs::write(path, json);
        }
        Err(error) => {
            tracing::warn!("Could not rewrite the redacted {}: {error}", path.display());
        }
    }
}

fn redact_value(value: Value, secret: bool) -> Value {
    match value {
        Value::Object(map) => {
            if secret {
                return Value::Object(
                    map.into_iter()
                        .map(|(key, _)| (key, Value::String(REDACTED.to_owned())))
                        .collect(),
                );
            }
            Value::Object(
                map.into_iter()
                    .map(|(key, item)| {
                        let redacted = if key == MCP_SERVERS_KEY {
                            redact_mcp_servers(item)
                        } else {
                            redact_value(item, SECRET_KEYS.contains(&key.as_str()))
                        };
                        (key, redacted)
                    })
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(
            items
                .into_iter()
                .map(|item| redact_value(item, secret))
                .collect(),
        ),
        // A secret scalar is replaced whole, before the URL pass below.
        value if secret && !value.is_null() => Value::String(REDACTED.to_owned()),
        Value::String(text) => Value::String(redact_url(&text)),
        value => value,
    }
}

fn redact_mcp_servers(servers: Value) -> Value {
    let mut redacted = redact_value(servers, false);
    let Some(items) = redacted.as_array_mut() else {
        return redacted;
    };
    for server in items.iter_mut() {
        if let Some(command) = server.get_mut(MCP_COMMAND_KEY) {
            *command = redact_command(std::mem::take(command));
        }
    }
    redacted
}

/// The program says which server runs; what follows it may be a credential.
fn redact_command(command: Value) -> Value {
    match command {
        Value::String(text) => {
            let mut words = text.split_whitespace();
            match (words.next(), words.next()) {
                (Some(first), Some(_)) => Value::String(format!("{first} {REDACTED}")),
                _ => Value::String(text),
            }
        }
        Value::Array(mut items) => {
            if items.is_empty() {
                return Value::Array(items);
            }
            let program = items.remove(0);
            let rest = items
                .drain(..)
                .map(|_| Value::String(REDACTED.to_owned()))
                .collect::<Vec<_>>();
            let mut redacted = vec![program];
            redacted.extend(rest);
            Value::Array(redacted)
        }
        other => other,
    }
}

/// Any URL keeps its scheme, host and path but loses its user info and
/// query string. The string is split, not normalized, so the result matches
/// the app server's redaction.
fn redact_url(value: &str) -> String {
    let Some((scheme, rest)) = value.split_once("://") else {
        return value.to_owned();
    };
    if scheme.is_empty() {
        return value.to_owned();
    }
    // The authority runs to the next /, ? or #.
    let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let (authority, tail) = rest.split_at(authority_end);
    if authority.is_empty() {
        return value.to_owned();
    }
    let mut out = format!("{scheme}://");
    if authority.contains('@') {
        out.push_str(REDACTED);
        out.push('@');
        out.push_str(&authority[authority.rfind('@').unwrap() + 1..]);
    } else {
        out.push_str(authority);
    }
    // The query is redacted whole; the path and fragment are kept.
    let (path, query_and_fragment) = match tail.find('?') {
        Some(q) => (&tail[..q], Some(&tail[q + 1..])),
        None => (tail, None),
    };
    out.push_str(path);
    if let Some(rest) = query_and_fragment {
        let (query, fragment) = match rest.find('#') {
            Some(f) => (&rest[..f], Some(&rest[f..])),
            None => (rest, None),
        };
        if !query.is_empty() {
            out.push_str("?<redacted>");
        }
        if let Some(fragment) = fragment {
            out.push_str(fragment);
        }
    }
    out
}
