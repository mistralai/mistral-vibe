//! `/mcp <name>` views the source it resolved, even when another kind shares its alias.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::mcp::{self, rows};
use vibe_rs::server::{Client, MCPSourceKind};

#[test]
fn named_open_keeps_the_kind_of_the_resolved_source() {
    let source = |kind: &str, display_name: &str| {
        json!({
            "name": "buildkite",
            "displayName": display_name,
            "kind": kind,
            "transport": "stdio",
            "status": "connected",
        })
    };
    let state = serde_json::from_value(json!({"sources": [
        source("server", ""),
        source("connector", "Buildkite CI"),
    ]}))
    .unwrap();
    let mut app = App::default();
    let event = mcp::Event::Opened {
        response: None,
        state,
        initial_source: "buildkite ci".to_owned(),
        reopened: false,
    };
    mcp::apply_event(&mut app, &Arc::new(Client::stub()), event);

    let viewed = rows::viewing_source(&app.mcp).unwrap();
    assert_eq!(viewed.kind, MCPSourceKind::Connector);
    assert_eq!(rows::title(&app.mcp), "Connector: Buildkite CI");
}

#[test]
fn reopen_racing_an_open_browser_shows_the_list_instead_of_another_login() {
    let weather = |status: &str| json!({"name": "weather", "kind": "server", "transport": "stdio", "status": status});
    let mut app = App::default();
    let (tx, mut rx) = tokio::sync::mpsc::channel(8);
    app.mcp.tx = Some(tx);
    app.mcp.open = true;
    mcp::apply_runtime(
        &mut app,
        &json!({"runtime": {"mcp": {"sources": [weather("connected")]}}}),
    );
    app.mcp.viewing_name = Some("weather".to_owned());
    let sources = json!({"sources": [weather("needs_auth")]});
    let event = mcp::Event::Opened {
        response: Some(json!({"runtime": {"mcp": sources}})),
        state: serde_json::from_value(sources).unwrap(),
        initial_source: "weather".to_owned(),
        reopened: true,
    };
    let client = Arc::new(Client::stub());
    mcp::apply_event(&mut app, &client, event);
    while let Ok(event) = rx.try_recv() {
        mcp::apply_event(&mut app, &client, event);
    }

    assert!(!app.mcp_oauth.open);
    assert!(app.mcp.open);
    assert_eq!(app.mcp.viewing_name, None);
    assert!(rows::awaits_auth(&app.mcp.state.sources[0]));
}

#[test]
fn reopen_keeps_the_detail_view_of_an_authenticated_source() {
    let weather = |status: &str| json!({"name": "weather", "kind": "server", "transport": "stdio", "status": status});
    let mut app = App::default();
    app.mcp.open = true;
    mcp::apply_runtime(
        &mut app,
        &json!({"runtime": {"mcp": {"sources": [weather("needs_auth")]}}}),
    );
    app.mcp.viewing_name = Some("weather".to_owned());
    let event = mcp::Event::Opened {
        response: None,
        state: serde_json::from_value(json!({"sources": [weather("connected")]})).unwrap(),
        initial_source: "weather".to_owned(),
        reopened: true,
    };
    mcp::apply_event(&mut app, &Arc::new(Client::stub()), event);

    assert!(app.mcp.open);
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("weather"));
}
