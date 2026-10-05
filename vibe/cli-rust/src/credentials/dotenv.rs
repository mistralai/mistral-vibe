//! Load `$VIBE_HOME/.env` into the process environment (Python `load_dotenv_values`).

use std::fs;
use std::path::PathBuf;

use crate::utils::paths::vibe_home;

/// The keys the last `load_dotenv_values` set, for `Client::spawn`: the
/// app-server child loads `~/.vibe/.env` itself with python-dotenv, so the
/// keys this (non-interpolating) parser set are stripped from its env.
static DOTENV_SET_KEYS: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

/// Set each `KEY=VALUE` from `~/.vibe/.env` that is not already in the
/// environment, recording exactly which keys were set. Every load defines
/// the record — a missing home or unreadable file leaves it empty, never
/// the previous load's keys.
pub fn load_dotenv_values() {
    let Some(home) = vibe_home() else {
        clear_dotenv_set_keys();
        return;
    };
    let path: PathBuf = home.join(".env");
    let Ok(content) = fs::read_to_string(&path) else {
        clear_dotenv_set_keys();
        return;
    };
    let mut set_keys = Vec::new();
    for (key, value) in parse(&content) {
        // An explicit non-empty process/shell value wins over the .env file
        // (Python `load_dotenv_values`); an empty one lets the file through.
        let already_set = std::env::var_os(&key).is_some_and(|v| !v.is_empty());
        if !already_set {
            std::env::set_var(&key, &value);
            set_keys.push(key);
        }
    }
    *DOTENV_SET_KEYS
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = set_keys;
}

fn clear_dotenv_set_keys() {
    DOTENV_SET_KEYS
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .clear();
}

/// The keys the client's `.env` load set (empty before any load).
pub fn dotenv_set_keys() -> Vec<String> {
    DOTENV_SET_KEYS
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .clone()
}

/// Parse dotenv lines into `(key, value)` pairs, skipping blanks and comments.
pub fn parse(content: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((key, rest)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim().to_owned();
        if key.is_empty() {
            continue;
        }
        let Some(value) = parse_value(rest.trim()) else {
            continue;
        };
        if value.is_empty() {
            continue;
        }
        out.push((key, value));
    }
    out
}

/// One dotenv value (python-dotenv `parse_value`). A quoted value ends at its
/// closing quote; anything after must be whitespace or a `#` comment or the
/// whole line is dropped, and an unclosed quote drops the line too. An
/// unquoted value cuts at the first whitespace-`#` inline comment
/// (`parse_unquoted_value`).
fn parse_value(raw: &str) -> Option<String> {
    let bytes = raw.as_bytes();
    let quote = bytes.first().copied().filter(|b| *b == b'"' || *b == b'\'');
    if let Some(quote) = quote {
        // python-dotenv treats `\` + the quoting quote as an escaped quote;
        // a lone backslash before the closing quote is literal.
        let mut i = 1;
        let mut close = None;
        while i < bytes.len() {
            if bytes[i] == b'\\' && i + 1 < bytes.len() && bytes[i + 1] == quote {
                i += 2;
                continue;
            }
            if bytes[i] == quote {
                close = Some(i);
                break;
            }
            i += 1;
        }
        let close = close?;
        let after = raw[close + 1..].trim_start();
        if !after.is_empty() && !after.starts_with('#') {
            return None;
        }
        return Some(unescape_dotenv(&raw[1..close], quote == b'"'));
    }
    let comment = bytes
        .windows(2)
        .position(|w| w[1] == b'#' && (w[0] as char).is_whitespace());
    let value = match comment {
        Some(i) => &raw[..i],
        None => raw,
    };
    Some(value.trim_end().to_owned())
}

/// python-dotenv `decode_escapes`: `\\` and `\'` in both quote styles, plus
/// `\"` and the `\n`-style controls in double quotes only. Unknown escapes
/// stay literal.
fn unescape_dotenv(value: &str, double: bool) -> String {
    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        let decoded = match chars.peek().copied() {
            Some('\\') => Some('\\'),
            Some('\'') => Some('\''),
            Some('"') if double => Some('"'),
            Some('a') if double => Some('\u{0007}'),
            Some('b') if double => Some('\u{0008}'),
            Some('f') if double => Some('\u{000c}'),
            Some('n') if double => Some('\n'),
            Some('r') if double => Some('\r'),
            Some('t') if double => Some('\t'),
            Some('v') if double => Some('\u{000b}'),
            _ => None,
        };
        if let Some(decoded) = decoded {
            chars.next();
            out.push(decoded);
        } else {
            out.push('\\');
        }
    }
    out
}
