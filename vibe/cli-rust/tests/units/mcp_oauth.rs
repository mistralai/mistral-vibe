//! A successful MCP login keeps its panel up until the reopened browser answers.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::Client;
use vibe_rs::{mcp, mcp_oauth};

fn logged_in() -> App {
    let mut app = App::default();
    app.mcp_oauth.open = true;
    app.mcp_oauth.logging_in = true;
    app.mcp_oauth.server_name = "weather".to_owned();
    app.commit_started();
    let event = mcp_oauth::Event {
        generation: app.mcp_oauth.generation,
        error: None,
    };
    mcp_oauth::apply_event(&mut app, &Arc::new(Client::stub()), event);
    app
}

#[test]
fn login_shows_connecting_until_the_browser_reopens() {
    let mut app = logged_in();
    assert!(app.mcp_oauth.open && app.mcp_oauth.logging_in);
    assert!(mcp_oauth::help_text(&app)[0].0.starts_with("Connecting..."));
    let weather =
        json!({"name": "weather", "kind": "server", "transport": "http", "status": "connected"});
    let event = mcp::Event::Opened {
        response: None,
        state: serde_json::from_value(json!({"sources": [weather]})).unwrap(),
        initial_source: "weather".to_owned(),
        reopened: true,
    };
    mcp::apply_event(&mut app, &Arc::new(Client::stub()), event);
    assert!(!app.mcp_oauth.open);
    assert!(app.mcp.open);
    assert_eq!(app.mcp.viewing_name.as_deref(), Some("weather"));
}

#[test]
fn failed_reopen_takes_the_login_panel_down() {
    let mut app = logged_in();
    let event = mcp::Event::Error("Failed to read MCP servers: boom".to_owned());
    mcp::apply_event(&mut app, &Arc::new(Client::stub()), event);
    assert!(!app.mcp_oauth.open);
}
