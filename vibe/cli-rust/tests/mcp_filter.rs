//! MCP source filtering preserves grouping, ranking, and tool-list identity.

use vibe_rs::app::{App, MCPApp};
use vibe_rs::mcp::{
    rows::{self, Row},
    search,
};
use vibe_rs::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary, MCPToolSummary};

fn source(name: &str, kind: MCPSourceKind, tools: bool) -> MCPSourceSummary {
    MCPSourceSummary {
        name: name.to_owned(),
        kind,
        transport: "stdio".to_owned(),
        status: MCPSourceStatus::Connected,
        tools: if tools {
            vec![MCPToolSummary {
                name: "read_file".to_owned(),
                description: String::new(),
                enabled: true,
            }]
        } else {
            Vec::new()
        },
        error: None,
    }
}

fn names(app: &MCPApp) -> Vec<String> {
    rows::rows(app)
        .into_iter()
        .filter_map(|row| match row {
            Row::Source(row) => Some(row.name),
            _ => None,
        })
        .collect()
}

#[test]
fn fuzzy_filter_ranks_matches_within_source_groups() {
    let mut app = MCPApp::default();
    app.state.sources = vec![
        source("a-github", MCPSourceKind::Server, true),
        source("git-host", MCPSourceKind::Server, true),
        source("github", MCPSourceKind::Connector, true),
        source("filesystem", MCPSourceKind::Server, true),
    ];
    app.search.query = " GH ".to_owned();
    assert_eq!(names(&app), ["git-host", "a-github", "github"]);
    assert_eq!(rows::title(&app), "MCP Servers & Connectors");
}

#[test]
fn blank_query_keeps_tool_availability_and_name_order() {
    let mut app = MCPApp::default();
    app.state.sources = vec![
        source("alpha", MCPSourceKind::Server, false),
        source("zulu", MCPSourceKind::Server, true),
        source("beta", MCPSourceKind::Server, true),
    ];
    app.search.query = "   ".to_owned();
    assert_eq!(names(&app), ["beta", "zulu", "alpha"]);
}

#[test]
fn no_matches_has_no_selectable_rows_or_empty_group_headers() {
    let mut app = MCPApp::default();
    app.state.sources = vec![source("github", MCPSourceKind::Connector, true)];
    app.search.query = "missing".to_owned();
    let rows = rows::rows(&app);
    assert_eq!(rows.len(), 1);
    assert!(matches!(&rows[0], Row::Note(note) if note == "No matching MCP servers or connectors"));
}

#[test]
fn source_filter_does_not_filter_the_selected_sources_tools() {
    let mut app = MCPApp::default();
    app.state.sources = vec![source("github", MCPSourceKind::Connector, true)];
    app.viewing_name = Some("github".to_owned());
    app.search.query = "GH".to_owned();
    assert!(matches!(&rows::rows(&app)[0], Row::Tool(tool) if tool.name == "read_file"));
}

#[test]
fn pasted_query_is_bounded_at_a_unicode_character_boundary() {
    let mut app = App::default();
    search::focus(&mut app);
    search::paste(&mut app, &"é".repeat(search::MAX_QUERY_BYTES));
    assert_eq!(app.mcp.search.query.len(), search::MAX_QUERY_BYTES);
    assert_eq!(app.mcp.search.cursor, search::MAX_QUERY_BYTES);
    assert!(app.mcp.search.query.chars().all(|ch| ch == 'é'));
    assert!(app.chat_input.input.is_empty());
}
