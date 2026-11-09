//! `diagnostics/logs/read` wire parsing for the debug console.

use serde_json::Value;

use super::{decode_log_message, LogEntry, LogPage};
use crate::utils::datetime::format_local_seconds;

/// Parse a `diagnostics/logs/read` result; `None` marks a malformed reply.
pub fn parse_page(value: &Value) -> Option<LogPage> {
    let logs = value.get("logs")?;
    Some(LogPage {
        entries: parse_entries(value)?,
        has_more: logs
            .get("hasMore")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        cursor: logs.get("cursor").and_then(Value::as_u64),
    })
}

/// Parse the entries of a page or poll answer (newest-first, as served).
pub fn parse_entries(value: &Value) -> Option<Vec<LogEntry>> {
    let entries = value.get("logs")?.get("entries")?.as_array()?;
    Some(entries.iter().filter_map(parse_entry).collect())
}

/// Parse one wire entry; entries without an id or timestamp are dropped.
fn parse_entry(value: &Value) -> Option<LogEntry> {
    let id = value.get("id")?.as_str()?.to_owned();
    let timestamp = value.get("timestamp")?.as_str()?;
    Some(LogEntry {
        id,
        timestamp: format_local_seconds(timestamp_ms(timestamp)),
        level: value
            .get("level")
            .and_then(Value::as_str)
            .unwrap_or("INFO")
            .to_owned(),
        message: decode_log_message(
            value
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
    })
}

/// RFC 3339 to epoch milliseconds; unparseable stamps fall back to the epoch.
pub fn timestamp_ms(timestamp: &str) -> u64 {
    time::OffsetDateTime::parse(timestamp, &time::format_description::well_known::Rfc3339)
        .map(|parsed| {
            parsed.unix_timestamp().max(0) as u64 * 1_000 + u64::from(parsed.millisecond())
        })
        .unwrap_or(0)
}
