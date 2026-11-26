//! Pure `/plugins` and `/reload-plugins` text.

use std::collections::BTreeMap;

use crate::server::{PluginCatalogComponent, PluginCatalogEntry, PluginCatalogState};
use crate::utils::paths::collapse_home;

const DIGEST_WIDTH: usize = 8;
pub const UNKNOWN: &str = "—";
pub const NOTHING_CHANGED: &str = "Plugins reloaded. Nothing changed.";
pub const DEFERRED: &str = "Plugins will reload when the current turn ends.";

/// One plugin whose pinned content digest moved across a reload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    pub name: String,
    pub before: Option<String>,
    pub after: Option<String>,
}

/// Compare two catalogues on content digest, by name.
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

/// What a reload moved, by digest, rather than that it ran.
pub fn reload_report(changes: &[Change], state: &PluginCatalogState) -> String {
    if changes.is_empty() {
        return NOTHING_CHANGED.to_owned();
    }
    let mut lines = vec!["Plugins reloaded.".to_owned(), String::new()];
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

/// The label column of the detail's facts.
pub const LABEL_WIDTH: usize = 12;

/// One line of a plugin's detail, by the role it plays so the view can style it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DetailLine {
    Blank,
    /// What the plugin does, in the foreground.
    Description(String),
    /// A fact: its dim label in a column, its value in the foreground.
    Fact {
        label: &'static str,
        value: String,
    },
    /// A section heading, such as `Components`.
    Heading(&'static str),
    /// A component kind and its names: the kind in the accent color.
    Components {
        kind: &'static str,
        names: String,
    },
}

/// The detail view: what the plugin does, its facts in a label column, then its components by kind.
pub fn detail_lines(entry: &PluginCatalogEntry) -> Vec<DetailLine> {
    let mut lines = Vec::new();
    if !entry.description.is_empty() {
        lines.push(DetailLine::Description(entry.description.clone()));
        lines.push(DetailLine::Blank);
    }
    let fact = |label, value: &str| DetailLine::Fact {
        label,
        value: value.to_owned(),
    };
    lines.push(fact("Author", or_unknown(entry.author.as_deref())));
    lines.push(fact("Version", or_unknown(entry.version.as_deref())));
    lines.push(fact("Scope", or_unknown(entry.scope.as_deref())));
    let location = match &entry.installed_root {
        Some(root) => collapse_home(root),
        None => "(uninstalled since pin)".to_owned(),
    };
    lines.push(fact("Location", &location));
    lines.push(fact("Format", &entry.source_format));
    lines.push(fact(
        "Pinned",
        &short_digest(entry.content_sha256.as_deref()),
    ));
    if entry.components.is_empty() {
        return lines;
    }
    lines.push(DetailLine::Blank);
    lines.push(DetailLine::Heading("Components"));
    lines.extend(component_lines(&entry.components));
    lines
}

/// One line per component kind, in first-seen order.
fn component_lines(components: &[PluginCatalogComponent]) -> Vec<DetailLine> {
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
        .map(|(kind, names)| DetailLine::Components {
            kind: component_label(kind),
            names: names.join(", "),
        })
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
