//! Rows of one source's view: its state, or its tools filtered by the tool search.

use caseless::default_case_fold_str as casefold;

use crate::app::MCPApp;
use crate::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary};
use crate::utils::fuzzy;

use super::rows::{self, Row, ToolRow};
use super::scope;

const NO_MATCHING_TOOLS: &str = "No matching tools";

pub(super) fn rows(app: &MCPApp, source: &MCPSourceSummary) -> Vec<Row> {
    status_rows(app, source).unwrap_or_else(|| tool_rows(source, &app.tool_search.query))
}

/// Whether the current view lists something to search: the sources, or the viewed source's tools.
pub fn searchable(app: &MCPApp) -> bool {
    rows::viewing_source(app).is_none_or(|source| status_rows(app, source).is_none())
}

/// The rows that replace the tool list while the source cannot list tools.
fn status_rows(app: &MCPApp, source: &MCPSourceSummary) -> Option<Vec<Row>> {
    let state = &app.state;
    if let Some(error) = &source.error {
        return Some(vec![
            Row::Note("Failed to bootstrap".to_owned()),
            Row::Detail(error.clone()),
        ]);
    }
    if scope::blocked(state, source) {
        return Some(vec![scope::blocked_hint(source)]);
    }
    match source.status {
        // The auth flows own these views; the browser shows no options for them.
        MCPSourceStatus::NeedsAuth => return Some(Vec::new()),
        MCPSourceStatus::NeedsConnect => {
            let note = if app.connecting.as_ref() == Some(&source.name) {
                "Connecting..."
            } else {
                "Not connected"
            };
            return Some(vec![Row::Note(note.to_owned())]);
        }
        MCPSourceStatus::NeedsSetup => {
            return Some(vec![Row::Hint {
                before: "Set up credentials in the Mistral dashboard, then press ".to_owned(),
                key: "r".to_owned(),
                after: " to refresh.".to_owned(),
            }])
        }
        _ => {}
    }
    if !source.tools.is_empty() {
        return None;
    }
    if source.kind == MCPSourceKind::Server && source.status == MCPSourceStatus::Unavailable {
        let mut rows = vec![Row::Note("Tool discovery failed".to_owned())];
        if let Some(error) = state.discovery_errors.get(&source.name) {
            rows.push(Row::Detail(error.clone()));
        }
        return Some(rows);
    }
    Some(vec![Row::Note("No tools discovered".to_owned())])
}

/// Tools by name; with a query, fuzzy name matches by score, then description matches by name.
fn tool_rows(source: &MCPSourceSummary, query: &str) -> Vec<Row> {
    let query = query.trim();
    let needle = casefold(query);
    let mut tools: Vec<_> = source
        .tools
        .iter()
        .filter_map(|tool| {
            let row = ToolRow {
                name: tool.name.clone(),
                description: one_line(&tool.description),
                enabled: tool.enabled,
            };
            if query.is_empty() {
                return Some(((0, 0), row));
            }
            if let Some(score) = fuzzy::score(query, &row.name) {
                return Some(((0, -score), row));
            }
            casefold(&row.description)
                .contains(needle.as_str())
                .then_some(((1, 0), row))
        })
        .collect();
    if tools.is_empty() {
        return vec![Row::Note(NO_MATCHING_TOOLS.to_owned())];
    }
    tools.sort_by(|(a, x), (b, y)| a.cmp(b).then_with(|| x.name.cmp(&y.name)));
    tools.into_iter().map(|(_, row)| Row::Tool(row)).collect()
}

/// The description as its row shows it, so the search matches the visible text.
fn one_line(description: &str) -> String {
    description.split_whitespace().collect::<Vec<_>>().join(" ")
}
