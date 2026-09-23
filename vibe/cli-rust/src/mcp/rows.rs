//! Option rows of the `/mcp` browser (Python `MCPApp._show_{list,detail}_view`).

use crate::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary, MCPState};

use crate::app::MCPApp;

/// One `OptionList` row; only `Source` and `Tool` rows are selectable.
pub enum Row {
    Header(String),
    Blank,
    Note(String),
    Detail(String),
    /// A note with one highlighted shortcut key inside it.
    Hint {
        before: String,
        key: String,
        after: String,
    },
    Source(SourceRow),
    Tool(ToolRow),
}

pub struct SourceRow {
    pub name: String,
    pub kind: MCPSourceKind,
    /// Name, `[transport]` tag and tool count, padded to their group's widths.
    pub label: String,
    pub transport: String,
    pub tools: String,
    pub symbol: &'static str,
    pub connected: bool,
    pub status: String,
    pub needs_auth: bool,
}

pub struct ToolRow {
    pub name: String,
    pub description: String,
    pub enabled: bool,
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Source(_) | Self::Tool(_))
    }

    /// Stable identity used to keep the highlight across a rebuild.
    pub fn id(&self) -> Option<String> {
        match self {
            Self::Source(row) => Some(format!("{}:{}", row.kind.as_str(), row.name)),
            Self::Tool(row) => Some(format!("tool:{}", row.name)),
            _ => None,
        }
    }
}

/// The rows of the current view: the source list, or one source's tools.
pub fn rows(app: &MCPApp) -> Vec<Row> {
    match viewing_source(app) {
        Some(source) => detail_rows(&app.state, source),
        None => list_rows(&app.state, &app.search.query),
    }
}

/// The source the browser is viewing, mirroring Python `_find_source`.
pub fn viewing_source(app: &MCPApp) -> Option<&MCPSourceSummary> {
    find_source(&app.state, app.viewing_name.as_deref()?, app.viewing_kind)
}

pub fn find_source<'a>(
    state: &'a MCPState,
    name: &str,
    kind: Option<MCPSourceKind>,
) -> Option<&'a MCPSourceSummary> {
    let named = || state.sources.iter().filter(|source| source.name == name);
    match kind {
        Some(kind) => named().find(|source| source.kind == kind),
        // Without an explicit kind a server wins over a same-named connector.
        None => named()
            .find(|source| source.kind == MCPSourceKind::Server)
            .or_else(|| named().next()),
    }
}

/// Title line: `MCP Servers[ & Connectors]`, or `MCP Server: name` in detail view.
pub fn title(app: &MCPApp) -> String {
    if let Some(source) = viewing_source(app) {
        let prefix = match source.kind {
            MCPSourceKind::Connector => "Connector",
            MCPSourceKind::Server => "MCP Server",
        };
        return format!("{prefix}: {}", source.name);
    }
    if sources(&app.state, MCPSourceKind::Connector).is_empty() {
        "MCP Servers".to_owned()
    } else {
        "MCP Servers & Connectors".to_owned()
    }
}

fn list_rows(state: &MCPState, query: &str) -> Vec<Row> {
    let servers = filtered_sources(state, MCPSourceKind::Server, query);
    let connectors = filtered_sources(state, MCPSourceKind::Connector, query);
    if servers.is_empty() && connectors.is_empty() {
        return vec![Row::Note(
            if query.trim().is_empty() {
                "No MCP servers or connectors configured"
            } else {
                "No matching MCP servers or connectors"
            }
            .to_owned(),
        )];
    }
    let mut rows = Vec::new();
    if !servers.is_empty() {
        add_source_group(&mut rows, "Local MCP Servers", &servers);
    }
    if !connectors.is_empty() {
        if !servers.is_empty() {
            rows.push(Row::Blank);
        }
        add_source_group(&mut rows, "Available Connectors", &connectors);
    }
    rows
}

/// Sources of one kind, ordered as Python `_sort_sources_for_menu`.
fn sources(state: &MCPState, kind: MCPSourceKind) -> Vec<&MCPSourceSummary> {
    let mut sources: Vec<_> = state
        .sources
        .iter()
        .filter(|source| source.kind == kind)
        .collect();
    sources.sort_by(|a, b| {
        a.tools
            .is_empty()
            .cmp(&b.tools.is_empty())
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
            .then_with(|| a.name.cmp(&b.name))
    });
    sources
}

fn filtered_sources<'a>(
    state: &'a MCPState,
    kind: MCPSourceKind,
    query: &str,
) -> Vec<&'a MCPSourceSummary> {
    let ordered = sources(state, kind);
    let query = query.trim();
    if query.is_empty() {
        return ordered;
    }
    let mut scored: Vec<_> = ordered
        .into_iter()
        .filter_map(|source| {
            crate::utils::fuzzy::score(query, &source.name).map(|score| (score, source))
        })
        .collect();
    scored.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
    scored.into_iter().map(|(_, source)| source).collect()
}

fn add_source_group(rows: &mut Vec<Row>, title: &str, sources: &[&MCPSourceSummary]) {
    rows.push(Row::Header(title.to_owned()));
    let max_name = sources
        .iter()
        .map(|s| s.name.chars().count())
        .max()
        .unwrap_or(0);
    let max_transport = sources
        .iter()
        .map(|s| s.transport.chars().count() + 2)
        .max()
        .unwrap_or(0);
    let labels: Vec<String> = sources.iter().map(|source| tool_label(source)).collect();
    let max_tools = labels.iter().map(|l| l.chars().count()).max().unwrap_or(0);
    for (source, tools) in sources.iter().zip(labels) {
        let (symbol, connected, status) = source_status(source);
        rows.push(Row::Source(SourceRow {
            name: source.name.clone(),
            kind: source.kind,
            label: pad(&source.name, max_name),
            transport: pad(&format!("[{}]", source.transport), max_transport),
            tools: pad(&tools, max_tools),
            symbol,
            connected,
            status,
            needs_auth: source.status == MCPSourceStatus::NeedsAuth,
        }));
    }
}

fn detail_rows(state: &MCPState, source: &MCPSourceSummary) -> Vec<Row> {
    if let Some(error) = &source.error {
        return vec![
            Row::Note("Failed to bootstrap".to_owned()),
            Row::Detail(error.clone()),
        ];
    }
    match source.status {
        // The auth flows own these views; the browser shows no options for them.
        MCPSourceStatus::NeedsAuth => return Vec::new(),
        MCPSourceStatus::NeedsSetup => {
            return vec![Row::Hint {
                before: "Set up credentials in the Mistral dashboard, then press ".to_owned(),
                key: "r".to_owned(),
                after: " to refresh.".to_owned(),
            }]
        }
        _ => {}
    }
    if source.tools.is_empty() {
        if source.kind == MCPSourceKind::Server && source.status == MCPSourceStatus::Unavailable {
            let mut rows = vec![Row::Note("Tool discovery failed".to_owned())];
            if let Some(error) = state.discovery_errors.get(&source.name) {
                rows.push(Row::Detail(error.clone()));
            }
            return rows;
        }
        return vec![Row::Note("No tools discovered".to_owned())];
    }
    let mut tools: Vec<_> = source.tools.iter().collect();
    tools.sort_by(|a, b| a.name.cmp(&b.name));
    tools
        .into_iter()
        .map(|tool| {
            Row::Tool(ToolRow {
                name: tool.name.clone(),
                description: tool.description.clone(),
                enabled: tool.enabled,
            })
        })
        .collect()
}

/// Status glyph, whether it reads as connected (green), and its label.
fn source_status(source: &MCPSourceSummary) -> (&'static str, bool, String) {
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

fn tool_label(source: &MCPSourceSummary) -> String {
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

fn pad(text: &str, width: usize) -> String {
    let mut padded = text.to_owned();
    for _ in text.chars().count()..width {
        padded.push(' ');
    }
    padded
}
