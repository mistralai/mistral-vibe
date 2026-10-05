//! Naming and status helpers (Python `subagent_list` module functions).

use crate::server::SessionStatus;

/// Python `_display_name`: split on `-_`, word and acronym boundaries, then
/// capitalize lowercase words; values containing a space pass through.
pub fn display_name(value: &str) -> String {
    if value.contains(' ') {
        return value.to_owned();
    }
    words(value)
        .into_iter()
        .map(|word| {
            if is_lower(&word) {
                capitalize(&word)
            } else {
                word
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Python `_WORD_BOUNDARY.split`: `[-_]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])`.
fn words(value: &str) -> Vec<String> {
    let chars: Vec<char> = value.chars().collect();
    let mut words = Vec::new();
    let mut current = String::new();
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        if ch == '-' || ch == '_' {
            push_word(&mut words, &mut current);
            while matches!(chars.get(index), Some('-' | '_')) {
                index += 1;
            }
            continue;
        }
        let boundary = index > 0
            && match chars[index - 1] {
                previous if previous.is_ascii_lowercase() || previous.is_ascii_digit() => {
                    ch.is_ascii_uppercase()
                }
                previous if previous.is_ascii_uppercase() => {
                    ch.is_ascii_uppercase()
                        && chars
                            .get(index + 1)
                            .is_some_and(|next| next.is_ascii_lowercase())
                }
                _ => false,
            };
        if boundary {
            push_word(&mut words, &mut current);
        }
        current.push(ch);
        index += 1;
    }
    push_word(&mut words, &mut current);
    words
}

/// Python drops empty split results (`if word`).
fn push_word(words: &mut Vec<String>, current: &mut String) {
    if !current.is_empty() {
        words.push(std::mem::take(current));
    }
}

/// Python `str.islower`: at least one cased character, all cased lowercase.
fn is_lower(word: &str) -> bool {
    let mut cased = 0;
    let mut lower = 0;
    for ch in word.chars() {
        if ch.is_lowercase() {
            cased += 1;
            lower += 1;
        } else if ch.is_uppercase() {
            cased += 1;
        }
    }
    cased > 0 && cased == lower
}

/// Python `str.capitalize`: first character upper, the rest lower.
fn capitalize(word: &str) -> String {
    let mut chars = word.chars();
    match chars.next() {
        Some(first) => format!("{}{}", first.to_uppercase(), chars.as_str().to_lowercase()),
        None => String::new(),
    }
}

/// Python `_status` label half.
pub fn status_label(status: SessionStatus) -> &'static str {
    match status {
        SessionStatus::Running => "running",
        SessionStatus::Blocked => "blocked",
        SessionStatus::Failed => "failed",
        SessionStatus::Archived => "stopped",
        SessionStatus::Idle => "ready",
        SessionStatus::Unknown => "unknown",
    }
}

/// Python `_status` theme half, as a theme tone the UI maps to a color.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StatusTone {
    Success,
    Warning,
    Error,
    Muted,
}

pub fn status_tone(status: SessionStatus) -> StatusTone {
    match status {
        SessionStatus::Running => StatusTone::Success,
        SessionStatus::Blocked => StatusTone::Warning,
        SessionStatus::Failed => StatusTone::Error,
        SessionStatus::Idle | SessionStatus::Archived | SessionStatus::Unknown => StatusTone::Muted,
    }
}

/// Python `subagent_loading_status`: the loading label while a viewed child runs.
pub fn subagent_loading_status(status: SessionStatus) -> Option<&'static str> {
    match status {
        SessionStatus::Running => Some("Running"),
        SessionStatus::Blocked => Some("Waiting for input"),
        _ => None,
    }
}

/// Python `_is_active`: the child counts as work in flight.
pub fn is_active(status: SessionStatus) -> bool {
    matches!(status, SessionStatus::Running | SessionStatus::Blocked)
}
