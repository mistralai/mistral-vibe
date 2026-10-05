//! Column texts of an `/mcp` source row (Python `_add_source_group` helpers).

use crate::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary};

/// Status glyph, whether it reads as connected (green), and its label.
pub(super) fn source_status(source: &MCPSourceSummary) -> (&'static str, bool, String) {
    match source.status {
        MCPSourceStatus::Connected => ("●", true, "connected".to_owned()),
        MCPSourceStatus::Enabled => ("●", true, "enabled".to_owned()),
        MCPSourceStatus::NeedsAuth => ("○", false, "needs auth".to_owned()),
        MCPSourceStatus::NeedsSetup => ("○", false, "needs setup".to_owned()),
        MCPSourceStatus::Unavailable => {
            let hint = match source.kind {
                MCPSourceKind::Server => "check your config",
                MCPSourceKind::Connector => "try refreshing",
            };
            ("○", false, format!("error - {hint}"))
        }
        MCPSourceStatus::Disabled => ("○", false, "disabled".to_owned()),
    }
}

pub(super) fn owner_tag(source: &MCPSourceSummary) -> String {
    source
        .plugin_name
        .as_ref()
        .map_or_else(String::new, |plugin| format!("[plugin:{plugin}]"))
}

pub(super) fn tool_label(source: &MCPSourceSummary) -> String {
    let total = source.tools.len();
    if source.kind == MCPSourceKind::Server
        && source.status == MCPSourceStatus::Unavailable
        && total == 0
    {
        return "tool discovery failed".to_owned();
    }
    let enabled = source.tools.iter().filter(|tool| tool.enabled).count();
    tool_count_text(enabled, total)
}

fn tool_count_text(enabled: usize, total: usize) -> String {
    if enabled < total {
        return format!("{enabled}/{total} {}", plural(total));
    }
    if enabled == 0 {
        return "no tools".to_owned();
    }
    format!("{enabled} {}", plural(enabled))
}

fn plural(count: usize) -> &'static str {
    if count == 1 {
        "tool"
    } else {
        "tools"
    }
}

pub(super) fn pad(text: &str, width: usize) -> String {
    let mut padded = text.to_owned();
    for _ in text.chars().count()..width {
        padded.push(' ');
    }
    padded
}
