//! The viewed source's tool search matches names fuzzily, then descriptions, and resets per source.

use vibe_rs::app::{App, MCPApp};
use vibe_rs::mcp::{self, detail, rows, rows::Row, search};
use vibe_rs::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary, MCPToolSummary};

fn tool(name: &str, description: &str) -> MCPToolSummary {
    MCPToolSummary {
        name: name.to_owned(),
        description: description.to_owned(),
        enabled: true,
    }
}

fn github(status: MCPSourceStatus) -> MCPSourceSummary {
    MCPSourceSummary {
        name: "github".to_owned(),
        display_name: String::new(),
        kind: MCPSourceKind::Connector,
        transport: "http".to_owned(),
        status,
        tools: vec![
            tool("list_issues", "List issues of a repository"),
            tool("create_issue", "Open an issue"),
            tool(
                "merge_pull_request",
                "Merge a pull request\nonce it is approved",
            ),
        ],
        error: None,
        plugin_name: None,
    }
}

fn viewing(query: &str) -> MCPApp {
    let mut app = MCPApp::default();
    app.state.sources = vec![github(MCPSourceStatus::Connected)];
    app.viewing_name = Some("github".to_owned());
    app.tool_search.query = query.to_owned();
    app
}

fn tool_names(app: &MCPApp) -> Vec<String> {
    rows::rows(app)
        .into_iter()
        .filter_map(|row| match row {
            Row::Tool(tool) => Some(tool.name),
            _ => None,
        })
        .collect()
}

#[test]
fn name_matches_rank_before_description_matches() {
    assert_eq!(
        tool_names(&viewing("issue")),
        ["create_issue", "list_issues"]
    );
    assert_eq!(tool_names(&viewing("REPOSITORY")), ["list_issues"]);
    assert_eq!(tool_names(&viewing("once it")), ["merge_pull_request"]);
    assert_eq!(tool_names(&viewing("request once")), ["merge_pull_request"]);
}

#[test]
fn no_match_shows_a_note_and_no_selectable_row() {
    let rows = rows::rows(&viewing("zzz"));
    assert!(matches!(rows.as_slice(), [Row::Note(note)] if note == "No matching tools"));
}

#[test]
fn source_search_does_not_filter_tools_nor_tool_search_sources() {
    let mut app = viewing("");
    app.search.query = "zzz".to_owned();
    assert_eq!(tool_names(&app).len(), 3);
    app.search.query.clear();
    app.viewing_name = None;
    app.tool_search.query = "zzz".to_owned();
    assert!(rows::rows(&app)
        .iter()
        .any(|row| matches!(row, Row::Source(source) if source.name == "github")));
}

#[test]
fn only_a_source_listing_tools_is_searchable() {
    assert!(detail::searchable(&viewing("")));
    let mut app = viewing("");
    app.state.sources = vec![github(MCPSourceStatus::NeedsSetup)];
    assert!(!detail::searchable(&app));
}

#[test]
fn typing_in_the_tool_view_filters_tools_and_back_clears_it() {
    let mut app = App {
        mcp: viewing(""),
        ..Default::default()
    };
    search::focus(&mut app);
    search::paste(&mut app, "merge");
    assert_eq!(tool_names(&app.mcp), ["merge_pull_request"]);
    assert!(app.mcp.search.query.is_empty());
    mcp::back(&mut app);
    assert!(app.mcp.tool_search.query.is_empty());
    assert!(!app.mcp.tool_search.focused);
}
