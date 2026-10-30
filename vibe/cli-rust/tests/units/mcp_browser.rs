//! MCP browser rows, wire tolerance and runtime adoption.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::mcp::{self, rows, rows::Row};
use vibe_rs::server::{MCPSourceKind, MCPSourceStatus, MCPState};
use vibe_rs::ui_event::{self, UiEvent};

use crate::support::stub;

fn source(name: &str, kind: &str, extra: Value) -> Value {
    let mut source = json!({
        "name": name,
        "kind": kind,
        "transport": "stdio",
        "status": "connected",
        "tools": [{"name": "read_file"}],
    });
    source
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    source
}

fn runtime(sources: Vec<Value>, extra: Value) -> Value {
    let mut mcp = json!({"sources": sources});
    mcp.as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    json!({"runtime": {"mcp": mcp}})
}

fn open_app(runtime: &Value) -> App {
    let mut app = App::default();
    app.mcp.open = true;
    mcp::apply_runtime(&mut app, runtime);
    app
}

/// Route the browser's own requests through a channel the test drains.
fn with_channel(app: &mut App) -> tokio::sync::mpsc::Receiver<UiEvent> {
    let (tx, rx) = tokio::sync::mpsc::channel(8);
    app.events = Some(tx);
    rx
}

fn drain(app: &mut App, rx: &mut tokio::sync::mpsc::Receiver<UiEvent>) {
    let client = stub();
    while let Ok(event) = rx.try_recv() {
        ui_event::apply(app, &client, event);
    }
}

fn source_rows(app: &App) -> Vec<rows::SourceRow> {
    rows::rows(&app.mcp)
        .into_iter()
        .filter_map(|row| match row {
            Row::Source(row) => Some(row),
            _ => None,
        })
        .collect()
}

#[test]
fn malformed_source_is_skipped_and_new_fields_default() {
    let state: MCPState = serde_json::from_value(json!({
        "sources": [
            source("fs", "server", json!({})),
            {"name": "broken", "kind": "server", "status": "from_the_future"},
        ],
    }))
    .unwrap();
    assert_eq!(state.sources.len(), 1);
    assert_eq!(state.sources[0].label(), "fs");
    assert!(state.sources[0].plugin_name.is_none());
    assert!(state.manage_connectors_url.is_none());
    assert!(!state.private_connectors_hidden);
}

#[test]
fn private_connectors_warning_leads_connectors_without_taking_the_highlight() {
    let app = open_app(&runtime(
        vec![source("gh", "connector", json!({}))],
        json!({
            "privateConnectorsHidden": true,
            "manageConnectorsUrl": "https://studio.example/connectors",
        }),
    ));
    let rows = rows::rows(&app.mcp);
    let warning = connector_lead(&rows);
    assert!(matches!(rows[warning], Row::PrivateConnectorsHidden));
    assert!(matches!(&rows[warning + 1], Row::Detail(hint) if hint == rows::KEY_HINT));
    assert!(matches!(rows[warning + 2], Row::Manage));
    assert!(matches!(&rows[app.mcp.list.selected()], Row::Source(row) if row.name == "gh"));
}

#[test]
fn private_connectors_warning_shows_without_any_connector() {
    let app = open_app(&runtime(
        vec![source("fs", "server", json!({}))],
        json!({"privateConnectorsHidden": true}),
    ));
    let rows = rows::rows(&app.mcp);
    assert!(matches!(&rows[3], Row::Header(title) if title == "Available Connectors"));
    assert!(matches!(rows[4], Row::PrivateConnectorsHidden));
    assert!(matches!(&rows[5], Row::Detail(hint) if hint == rows::KEY_HINT));
    assert_eq!(rows.len(), 6);
}

#[test]
fn private_connectors_warning_shows_when_no_source_is_visible() {
    let state =
        serde_json::from_value(json!({"sources": [], "privateConnectorsHidden": true})).unwrap();
    let mut app = App::default();
    let event = mcp::Event::Opened {
        response: None,
        state,
        initial_source: String::new(),
        reopened: false,
    };
    mcp::apply_event(&mut app, &stub(), event);
    assert!(app.mcp.open);
    let rows = rows::rows(&app.mcp);
    assert!(matches!(
        rows[connector_lead(&rows)],
        Row::PrivateConnectorsHidden
    ));
}

#[test]
fn private_connectors_warning_stays_while_search_matches_nothing() {
    let mut app = open_app(&runtime(
        vec![source("gh", "connector", json!({}))],
        json!({"privateConnectorsHidden": true}),
    ));
    app.mcp.search.query = "zzz".to_owned();
    let rows = rows::rows(&app.mcp);
    assert!(matches!(rows[1], Row::PrivateConnectorsHidden));
    assert!(matches!(&rows[2], Row::Detail(hint) if hint == rows::KEY_HINT));
    assert_eq!(rows.len(), 3);
}

#[test]
fn the_key_hint_shows_only_when_connectors_fail_or_are_scoped() {
    let has_hint = |extra: Value| {
        let app = open_app(&runtime(vec![source("gh", "connector", json!({}))], extra));
        rows::rows(&app.mcp)
            .iter()
            .any(|row| matches!(row, Row::Detail(hint) if hint == rows::KEY_HINT))
    };
    assert!(!has_hint(json!({})));
    assert!(has_hint(json!({"connectorError": "HTTP 401"})));
    assert!(has_hint(json!({"privateConnectorsHidden": true})));
}

fn shared_only_app() -> App {
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    open_app(&runtime(
        vec![
            source("weather", "server", needs_auth.clone()),
            source("gmail", "connector", needs_auth),
            source("gh", "connector", json!({})),
        ],
        json!({"privateConnectorsHidden": true}),
    ))
}

#[test]
fn shared_only_key_greys_out_connectors_that_need_a_login() {
    let app = shared_only_app();
    let blocked: Vec<(String, bool, bool, String)> = source_rows(&app)
        .into_iter()
        .map(|row| (row.name, row.blocked, row.action.is_some(), row.status))
        .collect();
    assert_eq!(
        blocked,
        [
            ("weather".to_owned(), false, true, "needs auth".to_owned()),
            ("gh".to_owned(), false, false, "connected".to_owned()),
            (
                "gmail".to_owned(),
                true,
                false,
                "needs a personal API key".to_owned()
            ),
        ]
    );
}

#[test]
fn connector_needing_a_login_is_not_blocked_for_personal_keys() {
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    let app = open_app(&runtime(
        vec![source("gmail", "connector", needs_auth)],
        json!({}),
    ));
    assert!(!source_rows(&app)[0].blocked);
    assert_eq!(
        source_rows(&app)[0].action,
        Some(rows::SourceAction::ConnectorAuth)
    );
}

#[test]
fn enter_on_a_blocked_connector_explains_instead_of_connecting() {
    let mut app = shared_only_app();
    let mut rx = with_channel(&mut app);
    while !matches!(&rows::rows(&app.mcp)[app.mcp.list.selected()], Row::Source(row) if row.name == "gmail")
    {
        mcp::navigate(&mut app, true);
    }
    assert_eq!(mcp::help_text(&app)[1].1, "why blocked");
    mcp::select(&mut app);
    drain(&mut app, &mut rx);
    assert!(app.mcp.open && !app.connector_auth.open);
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("gmail"));
    let rows = rows::rows(&app.mcp);
    assert!(matches!(&rows[..], [Row::Hint { key, .. }] if key == "vibe --setup"));
}

#[test]
fn private_connectors_warning_survives_a_runtime_without_it() {
    let mut app = open_app(&runtime(
        vec![source("gh", "connector", json!({}))],
        json!({"privateConnectorsHidden": true}),
    ));
    mcp::apply_runtime(
        &mut app,
        &runtime(vec![source("gh", "connector", json!({}))], json!({})),
    );
    assert!(app.mcp.state.private_connectors_hidden);
}

#[test]
fn private_connectors_block_survives_a_reread_without_the_flag() {
    let mut app = shared_only_app();
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    let state = serde_json::from_value(json!({
        "sources": [source("gmail", "connector", needs_auth)],
    }))
    .unwrap();
    let event = mcp::Event::Opened {
        response: None,
        state,
        initial_source: String::new(),
        reopened: false,
    };
    mcp::apply_event(&mut app, &stub(), event);
    assert!(app.mcp.state.private_connectors_hidden);
    assert!(source_rows(&app)[0].blocked);
}

#[test]
fn connectors_render_search_and_title_by_display_name() {
    let mut app = open_app(&runtime(
        vec![source(
            "gh_alias",
            "connector",
            json!({"displayName": "GitHub"}),
        )],
        json!({}),
    ));
    assert!(source_rows(&app)[0].label.starts_with("GitHub"));
    app.mcp.search.query = "git".to_owned();
    assert_eq!(source_rows(&app).len(), 1);
    app.mcp.viewing_name = Some("gh_alias".to_owned());
    assert_eq!(rows::title(&app.mcp), "Connector: GitHub");
}

#[test]
fn display_name_lookup_casefolds() {
    let state: MCPState = serde_json::from_value(json!({
        "sources": [source("st_alias", "connector", json!({"displayName": "Straße"}))],
    }))
    .unwrap();
    let found = state
        .resolve_source("STRASSE")
        .map(|source| source.name.as_str());
    assert_eq!(found, Some("st_alias"));
}

#[test]
fn plugin_column_is_only_claimed_by_groups_with_plugin_sources() {
    let app = open_app(&runtime(
        vec![
            source("fs", "server", json!({"pluginName": "tools"})),
            source("web", "server", json!({})),
            source("gh", "connector", json!({})),
        ],
        json!({}),
    ));
    let owners: Vec<String> = source_rows(&app).into_iter().map(|row| row.owner).collect();
    assert_eq!(owners, ["[plugin:tools]", "              ", ""]);
}

#[test]
fn manage_row_leads_connectors_but_never_takes_the_initial_highlight() {
    let app = open_app(&runtime(
        vec![source("gh", "connector", json!({}))],
        json!({"manageConnectorsUrl": "https://studio.example/connectors"}),
    ));
    let rows = rows::rows(&app.mcp);
    assert!(matches!(rows[connector_lead(&rows)], Row::Manage));
    assert!(matches!(&rows[app.mcp.list.selected()], Row::Source(row) if row.name == "gh"));
}

#[test]
fn viewed_source_that_vanished_falls_back_to_the_list() {
    let mut app = open_app(&runtime(
        vec![source("gh", "connector", json!({}))],
        json!({}),
    ));
    app.mcp.viewing_name = Some("gh".to_owned());
    mcp::apply_runtime(
        &mut app,
        &runtime(vec![source("fs", "server", json!({}))], json!({})),
    );
    assert!(app.mcp.viewing_name.is_none());
    assert!(
        matches!(&rows::rows(&app.mcp)[app.mcp.list.selected()], Row::Source(row) if row.name == "fs")
    );
}

#[test]
fn runtime_without_mcp_keeps_the_open_browser_state() {
    let mut app = open_app(&runtime(vec![source("fs", "server", json!({}))], json!({})));
    mcp::apply_runtime(&mut app, &json!({"runtime": {"stats": {}}}));
    assert_eq!(app.mcp.state.sources.len(), 1);
    assert_eq!(app.mcp.confirmed.sources.len(), 1);
}

#[test]
fn closed_browser_ignores_runtime_updates() {
    let mut app = App::default();
    mcp::apply_runtime(
        &mut app,
        &runtime(vec![source("fs", "server", json!({}))], json!({})),
    );
    assert!(app.mcp.state.sources.is_empty());
}

#[test]
fn refreshing_label_only_shows_in_the_list_view() {
    let mut app = open_app(&runtime(vec![source("fs", "server", json!({}))], json!({})));
    app.mcp.refreshing = true;
    assert_eq!(
        rows::title(&app.mcp),
        "MCP Servers & Connectors  (refreshing)"
    );
    app.mcp.viewing_name = Some("fs".to_owned());
    assert_eq!(rows::title(&app.mcp), "MCP Server: fs");
}

/// The first row under the "Available Connectors" header.
fn connector_lead(rows: &[Row]) -> usize {
    1 + rows
        .iter()
        .position(|row| matches!(row, Row::Header(title) if title == "Available Connectors"))
        .expect("connectors section")
}

#[test]
fn an_empty_catalog_shows_both_sections_with_their_empty_notes() {
    let app = open_app(&runtime(vec![], json!({})));
    let notes: Vec<String> = rows::rows(&app.mcp)
        .iter()
        .filter_map(|row| match row {
            Row::Header(text) | Row::Note(text) => Some(text.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(
        notes,
        [
            "Local MCP Servers",
            "No local MCP servers configured.",
            "Available Connectors",
            "No connectors available.",
        ]
    );
}

fn status(app: &App, name: &str) -> MCPSourceStatus {
    let source = app
        .mcp
        .state
        .sources
        .iter()
        .find(|source| source.name == name);
    source.unwrap().status
}

#[test]
fn rejected_toggle_restores_the_server_state_and_warns() {
    let client = stub();
    let mut app = open_app(&runtime(vec![source("fs", "server", json!({}))], json!({})));
    mcp::set_disabled(&mut app, &client, true);
    assert_eq!(status(&app, "fs"), MCPSourceStatus::Disabled);
    mcp::apply_event(&mut app, &client, toggle_failed("fs", None));
    assert_eq!(status(&app, "fs"), MCPSourceStatus::Connected);
    assert_eq!(app.overlays.toasts.back().unwrap().text, "rejected");
}

fn toggle_failed(name: &str, tool_name: Option<&str>) -> mcp::Event {
    mcp::Event::ToggleFailed {
        toggle: mcp::Toggle {
            name: name.to_owned(),
            kind: MCPSourceKind::Server,
            tool_name: tool_name.map(str::to_owned),
        },
        message: "rejected".to_owned(),
    }
}

#[test]
fn rejected_toggle_keeps_the_paint_of_toggles_still_in_flight() {
    let client = stub();
    let sources = vec![
        source("a", "server", json!({})),
        source("b", "server", json!({})),
    ];
    let mut app = open_app(&runtime(sources, json!({})));
    mcp::set_disabled(&mut app, &client, true);
    mcp::navigate(&mut app, true);
    mcp::set_disabled(&mut app, &client, true);
    mcp::apply_event(&mut app, &client, toggle_failed("b", None));
    assert_eq!(status(&app, "a"), MCPSourceStatus::Disabled);
    assert_eq!(status(&app, "b"), MCPSourceStatus::Connected);
}

#[test]
fn rejected_tool_toggle_restores_only_that_tool() {
    let client = stub();
    let tools = json!({"tools": [{"name": "read"}, {"name": "write"}]});
    let mut app = open_app(&runtime(vec![source("fs", "server", tools)], json!({})));
    mcp::select(&mut app);
    mcp::set_disabled(&mut app, &client, true);
    mcp::navigate(&mut app, true);
    mcp::set_disabled(&mut app, &client, true);
    mcp::apply_event(&mut app, &client, toggle_failed("fs", Some("write")));
    let enabled: Vec<bool> = app.mcp.state.sources[0]
        .tools
        .iter()
        .map(|tool| tool.enabled)
        .collect();
    assert_eq!(enabled, [false, true]);
}

#[test]
fn enable_waits_for_the_server_before_painting_a_status() {
    let disabled = json!({"status": "disabled"});
    let mut app = open_app(&runtime(vec![source("fs", "server", disabled)], json!({})));
    mcp::set_disabled(&mut app, &stub(), false);
    assert_eq!(status(&app, "fs"), MCPSourceStatus::Disabled);
}

#[test]
fn plugin_server_toggles_are_refused_locally() {
    let plugin = json!({"pluginName": "devtools"});
    let mut app = open_app(&runtime(vec![source("fs", "server", plugin)], json!({})));
    mcp::set_disabled(&mut app, &stub(), true);
    assert_eq!(status(&app, "fs"), MCPSourceStatus::Connected);
    let toast = &app.overlays.toasts.back().unwrap().text;
    assert_eq!(
        toast,
        "fs is managed by the devtools plugin and cannot be toggled here."
    );
}

fn opened(app: &mut App, reopened: bool) {
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    let state =
        serde_json::from_value(json!({"sources": [source("weather", "server", needs_auth)]}));
    let event = mcp::Event::Opened {
        response: None,
        state: state.unwrap(),
        initial_source: "weather".to_owned(),
        reopened,
    };
    mcp::apply_event(app, &stub(), event);
}

#[test]
fn named_source_awaiting_auth_opens_its_login() {
    let mut app = App::default();
    let mut rx = with_channel(&mut app);
    opened(&mut app, false);
    drain(&mut app, &mut rx);
    assert!(app.mcp_oauth.open);
    assert!(!app.mcp.open);
}

#[test]
fn viewed_source_that_starts_awaiting_auth_hands_off_to_its_login() {
    let mut app = open_app(&runtime(
        vec![source("weather", "server", json!({}))],
        json!({}),
    ));
    let mut rx = with_channel(&mut app);
    app.mcp.viewing_name = Some("weather".to_owned());
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    mcp::apply_runtime(
        &mut app,
        &runtime(vec![source("weather", "server", needs_auth)], json!({})),
    );
    drain(&mut app, &mut rx);
    assert!(app.mcp_oauth.open);
    assert_eq!(app.mcp_oauth.server_name, "weather");
    assert!(!app.mcp.open);
}

#[test]
fn auth_request_is_dropped_once_the_user_left_the_view() {
    let mut app = App::default();
    let mut rx = with_channel(&mut app);
    opened(&mut app, false);
    mcp::back(&mut app);
    drain(&mut app, &mut rx);
    assert!(!app.mcp_oauth.open);
    assert!(app.mcp.open);
}

#[test]
fn help_offers_connect_only_for_sources_enter_hands_to_auth() {
    let errored = json!({"status": "needs_auth", "error": "bootstrap failed"});
    let app = open_app(&runtime(
        vec![source("gh", "connector", errored)],
        json!({}),
    ));
    assert_eq!(mcp::help_text(&app)[1].1, "show tools");
    let needs_auth = json!({"status": "needs_auth"});
    let app = open_app(&runtime(
        vec![source("gh", "connector", needs_auth)],
        json!({}),
    ));
    assert_eq!(mcp::help_text(&app)[1].1, "connect");
}

/// DeepWiki is connecting; weather and wiki still need a login and a connection.
fn unusable_runtime() -> Value {
    let needs_auth = json!({"status": "needs_auth", "tools": []});
    let needs_connect = json!({"status": "needs_connect", "tools": []});
    runtime(
        vec![
            source("weather", "server", needs_auth),
            source("deepwiki", "connector", needs_connect.clone()),
            source("wiki", "connector", needs_connect),
        ],
        json!({}),
    )
}

fn connecting_app() -> (App, tokio::sync::mpsc::Receiver<UiEvent>) {
    let mut app = open_app(&unusable_runtime());
    let rx = with_channel(&mut app);
    app.mcp.connecting = Some("deepwiki".to_owned());
    (app, rx)
}

fn view(app: &mut App, name: &str, kind: MCPSourceKind) {
    app.mcp.viewing_name = Some(name.to_owned());
    app.mcp.viewing_kind = Some(kind);
}

fn notes(app: &App) -> Vec<String> {
    rows::rows(&app.mcp)
        .into_iter()
        .filter_map(|row| match row {
            Row::Note(note) => Some(note),
            _ => None,
        })
        .collect()
}

#[test]
fn pending_action_names_the_flow_each_unusable_source_starts() {
    let (app, _rx) = connecting_app();
    let actions: Vec<_> = app
        .mcp
        .state
        .sources
        .iter()
        .map(|source| rows::pending_action(&app.mcp.state, source))
        .collect();
    assert_eq!(
        actions,
        [
            Some(rows::SourceAction::McpOAuth),
            Some(rows::SourceAction::Connect),
            Some(rows::SourceAction::Connect),
        ]
    );
}

#[test]
fn only_the_connector_in_flight_reads_connecting() {
    let (mut app, _rx) = connecting_app();
    view(&mut app, "deepwiki", MCPSourceKind::Connector);
    assert_eq!(notes(&app), ["Connecting..."]);
    view(&mut app, "wiki", MCPSourceKind::Connector);
    assert_eq!(notes(&app), ["Not connected"]);
}

#[test]
fn a_connect_in_flight_does_not_block_another_source_login() {
    let (mut app, mut rx) = connecting_app();
    view(&mut app, "weather", MCPSourceKind::Server);
    mcp::apply_runtime(&mut app, &unusable_runtime());
    drain(&mut app, &mut rx);
    assert!(app.mcp_oauth.open);
    assert_eq!(app.mcp_oauth.server_name, "weather");
}

#[test]
fn a_failed_connect_leaves_another_viewed_connector_alone() {
    let (mut app, _rx) = connecting_app();
    view(&mut app, "wiki", MCPSourceKind::Connector);
    app.mcp.connecting = Some("wiki".to_owned());
    let failed = mcp::Event::ConnectFailed {
        name: "deepwiki".to_owned(),
        message: "Could not connect DeepWiki (HTTP 503).".to_owned(),
    };
    mcp::apply_event(&mut app, &stub(), failed);
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("wiki"));
    assert_eq!(app.mcp.connecting.as_deref(), Some("wiki"));
    assert_eq!(app.overlays.toasts.len(), 1);
}

#[test]
fn a_failed_connect_returns_from_its_own_view() {
    let (mut app, _rx) = connecting_app();
    view(&mut app, "deepwiki", MCPSourceKind::Connector);
    let failed = mcp::Event::ConnectFailed {
        name: "deepwiki".to_owned(),
        message: "Could not connect DeepWiki (HTTP 503).".to_owned(),
    };
    mcp::apply_event(&mut app, &stub(), failed);
    assert_eq!(app.mcp.viewing_name, None);
    assert_eq!(app.mcp.connecting, None);
}

fn open_named(query: &str) -> App {
    let gmail = source("gm_9c1b", "connector", json!({"displayName": "Gmail"}));
    let state = serde_json::from_value(json!({"sources": [gmail]})).unwrap();
    let mut app = App::default();
    let event = mcp::Event::Opened {
        response: None,
        state,
        initial_source: query.to_owned(),
        reopened: false,
    };
    mcp::apply_event(&mut app, &stub(), event);
    app
}

#[test]
fn unknown_source_error_lists_the_names_the_browser_shows() {
    let app = open_named("nope");
    assert!(!app.mcp.open);
    let transcript = &app.view.transcript;
    let error = transcript
        .lines()
        .filter_map(|entry| transcript.entry_content(entry.id))
        .map(|content| content.to_string())
        .find(|content| content.contains("Unknown MCP server or connector"));
    assert!(error.unwrap().contains("nope. Known: Gmail"));
}

#[test]
fn reopen_after_auth_lands_on_the_list_instead_of_looping() {
    let mut app = App::default();
    opened(&mut app, true);
    assert!(!app.mcp_oauth.open);
    assert!(app.mcp.open && app.mcp.viewing_name.is_none());
}

#[test]
fn sources_sort_by_casefolded_label() {
    let app = open_app(&runtime(
        vec![
            source("sz", "server", json!({})),
            source("ßa", "server", json!({})),
        ],
        json!({}),
    ));

    let names: Vec<String> = source_rows(&app).into_iter().map(|row| row.name).collect();
    assert_eq!(names, ["ßa", "sz"]);
}

#[tokio::test]
async fn toggle_response_without_runtime_rereads_the_catalog() {
    let mut app = open_app(&runtime(vec![source("fs", "server", json!({}))], json!({})));
    let mut rx = with_channel(&mut app);
    app.session.session_id = Some("session".to_owned());

    mcp::apply_event(&mut app, &stub(), mcp::Event::Toggled(json!({})));

    let event = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv()).await;
    assert!(matches!(
        event,
        Ok(Some(UiEvent::Mcp(mcp::Event::Error(_))))
    ));
}
