//! Pure `/plugins` and `/reload-plugins` text (Python `widgets/plugins_app.py` helpers).

use std::collections::BTreeMap;

use crate::server::{PluginCatalogComponent, PluginCatalogEntry, PluginCatalogState};
use crate::utils::paths::collapse_home;

const DIGEST_WIDTH: usize = 8;
pub const UNKNOWN: &str = "—";
pub const NOTHING_CHANGED: &str = "Plugins reloaded. Nothing changed.";

/// One plugin whose pinned content digest moved across a reload (Python `PluginCatalogChange`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    pub name: String,
    pub before: Option<String>,
    pub after: Option<String>,
}

/// Compare two catalogues on content digest, by name (Python `_plugin_changes`).
pub fn changes(before: &PluginCatalogState, after: &PluginCatalogState) -> Vec<Change> {
    let digests = |state: &PluginCatalogState| -> BTreeMap<String, Option<String>> {
        state
            .plugins
            .iter()
            .map(|entry| (entry.name.clone(), entry.content_sha256.clone()))
            .collect()
    };
    let (pinned, current) = (digests(before), digests(after));
    let mut names: Vec<&String> = pinned.keys().chain(current.keys()).collect();
    names.sort();
    names.dedup();
    names
        .into_iter()
        .filter_map(|name| {
            let before = pinned.get(name).cloned().flatten();
            let after = current.get(name).cloned().flatten();
            (before != after).then(|| Change {
                name: name.clone(),
                before,
                after,
            })
        })
        .collect()
}

/// What a reload moved, by digest, rather than that it ran (Python `plugin_reload_report`).
pub fn reload_report(changes: &[Change], state: &PluginCatalogState) -> String {
    if changes.is_empty() {
        return NOTHING_CHANGED.to_owned();
    }
    let mut lines = vec!["### Plugins reloaded".to_owned(), String::new()];
    for change in changes {
        let version = state
            .plugins
            .iter()
            .find(|entry| entry.name == change.name)
            .and_then(|entry| entry.version.as_deref());
        lines.push(change_line(change, version));
    }
    lines.join("\n")
}

fn change_line(change: &Change, version: Option<&str>) -> String {
    let name = &change.name;
    match (&change.before, &change.after) {
        (None, _) => match version.filter(|version| !version.is_empty()) {
            Some(version) => format!("- `+` `{name}` {version}"),
            None => format!("- `+` `{name}`"),
        },
        (_, None) => format!("- `-` `{name}` — no longer installed"),
        (Some(before), Some(after)) => format!(
            "- `~` `{name}` {} → {}",
            short_digest(Some(before)),
            short_digest(Some(after))
        ),
    }
}

/// `scope · format · digest`, the dim facts after a list entry's name.
pub fn entry_facts(entry: &PluginCatalogEntry) -> String {
    [
        or_unknown(entry.scope.as_deref()),
        entry.source_format.as_str(),
        &short_digest(entry.content_sha256.as_deref()),
    ]
    .join(" · ")
}

/// Whether the filter query (already case-folded) matches the name or description.
pub fn matches(entry: &PluginCatalogEntry, query: &str) -> bool {
    query.is_empty()
        || caseless::default_case_fold_str(&format!("{} {}", entry.name, entry.description))
            .contains(query)
}

/// The detail view's lines (Python `_detail_lines`).
pub fn detail_lines(entry: &PluginCatalogEntry) -> Vec<String> {
    let mut lines = vec![
        format!("  Author: {}", or_unknown(entry.author.as_deref())),
        format!("  Version: {}", or_unknown(entry.version.as_deref())),
    ];
    if !entry.description.is_empty() {
        lines.push(String::new());
        lines.push(format!("  {}", entry.description));
    }
    lines.push(String::new());
    lines.push(match &entry.installed_root {
        Some(root) => format!("  Location: {}", collapse_home(root)),
        None => "  Location: (uninstalled since pin)".to_owned(),
    });
    lines.push(format!("  Scope: {}", or_unknown(entry.scope.as_deref())));
    lines.push(format!("  Format: {}", entry.source_format));
    lines.push(format!(
        "  Pinned: {}",
        short_digest(entry.content_sha256.as_deref())
    ));
    if entry.components.is_empty() {
        return lines;
    }
    lines.push(String::new());
    lines.push("  Components:".to_owned());
    lines.extend(component_lines(&entry.components));
    lines
}

/// One `● Kind: a, b` line per component kind, in first-seen order.
fn component_lines(components: &[PluginCatalogComponent]) -> Vec<String> {
    let mut grouped: Vec<(&str, Vec<String>)> = Vec::new();
    for component in components {
        let name = match &component.status {
            Some(status) => format!("{} ({status})", component.name),
            None => component.name.clone(),
        };
        match grouped.iter_mut().find(|(kind, _)| *kind == component.kind) {
            Some((_, names)) => names.push(name),
            None => grouped.push((&component.kind, vec![name])),
        }
    }
    grouped
        .into_iter()
        .map(|(kind, names)| format!("  ● {}: {}", component_label(kind), names.join(", ")))
        .collect()
}

fn component_label(kind: &str) -> &'static str {
    match kind {
        "skill" => "Skills",
        "knowledge" => "Knowledge",
        "library" => "Libraries",
        "mcp_server" => "MCP servers",
        "connector" => "Connectors",
        "hook" => "Hooks",
        "agent" => "Agents",
        "subagent" => "Subagents",
        "tool" => "Tools",
        _ => "Other",
    }
}

/// A dropped plugin file and why it was not loaded.
pub fn dropped_line(file: &str, message: &str) -> String {
    format!("  ! {} — {message}", collapse_home(file))
}

pub fn short_digest(digest: Option<&str>) -> String {
    digest.map_or_else(
        || UNKNOWN.to_owned(),
        |digest| digest.chars().take(DIGEST_WIDTH).collect(),
    )
}

fn or_unknown(value: Option<&str>) -> &str {
    value.filter(|value| !value.is_empty()).unwrap_or(UNKNOWN)
}
