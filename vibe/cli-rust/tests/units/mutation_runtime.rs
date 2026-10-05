//! Mutation responses apply their `runtime` like Python's `if response.runtime is not None`.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::Client;
use vibe_rs::{connector_auth, event_handler, mcp};

fn app_with_steps(steps: u64) -> App {
    let mut app = App::default();
    event_handler::apply_runtime_value(&mut app, &json!({"runtime": {"stats": {"steps": steps}}}));
    app
}

#[test]
fn connector_refresh_applies_the_runtime_its_response_carries() {
    let mut app = app_with_steps(1);
    app.connector_auth.open = true;
    app.commit_started();
    let event = connector_auth::Event::Refreshed {
        generation: app.connector_auth.generation,
        tool_count: 0,
        response: Some(json!({"toolCount": 0, "runtime": {"stats": {"steps": 3}}})),
    };

    connector_auth::apply_event(&mut app, &Arc::new(Client::stub()), event);

    assert_eq!(app.session.stats.steps, 3);
    assert_eq!(
        app.session.runtime.pointer("/runtime/stats/steps"),
        Some(&json!(3))
    );
}

#[test]
fn toggle_response_without_runtime_keeps_the_current_runtime() {
    let mut app = app_with_steps(2);
    let before = app.session.runtime.clone();

    mcp::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        mcp::Event::Toggled(json!({"runtime": null})),
    );

    assert_eq!(app.session.stats.steps, 2);
    assert_eq!(app.session.runtime, before);
}
