//! `/plugins` detail view: what a plugin does, its facts under labels, its components by kind, long values wrapped.

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::plugins::{self, text, Event};

use crate::drawn::drawn_value;
use crate::plugins::{catalog, entry};
use crate::support::{key, render, rows, stub};

/// A detail line as plain text, indented as drawn.
fn plain(line: &text::DetailLine) -> String {
    let width = text::LABEL_WIDTH;
    match line {
        text::DetailLine::Blank => String::new(),
        text::DetailLine::Description(text) => format!("  {text}"),
        text::DetailLine::Fact { label, value } => format!("  {label:<width$}{value}"),
        text::DetailLine::Heading(heading) => format!("  {heading}"),
        text::DetailLine::Components { kind, names } => format!("  {kind:<width$}{names}"),
    }
}

#[test]
fn detail_lines_group_components_and_flag_unknown_facts() {
    let mut value = entry("devtools", "0123456789abcdef");
    value["description"] = json!("Developer helpers");
    value["installedRoot"] = Value::Null;
    value["scope"] = Value::Null;
    value["components"] = json!([
        {"kind": "skill", "name": "lint"},
        {"kind": "tool", "name": "run", "status": "stale"},
        {"kind": "skill", "name": "fmt"},
    ]);
    let state = catalog(json!({"plugins": [value]}));
    let lines: Vec<String> = text::detail_lines(&state.plugins[0])
        .iter()
        .map(plain)
        .collect();
    assert_eq!(
        lines,
        [
            "  Developer helpers",
            "",
            "  Author      —",
            "  Version     1.0.0",
            "  Scope       —",
            "  Location    (uninstalled since pin)",
            "  Format      agent_plugins_1_0",
            "  Pinned      01234567",
            "",
            "  Components",
            "  Skills      lint, fmt",
            "  Tools       run (stale)",
        ]
    );
    assert_eq!(
        text::entry_facts(&state.plugins[0]),
        "— · agent_plugins_1_0 · 01234567"
    );
}

#[test]
fn a_long_location_wraps_under_its_label_instead_of_being_cut() {
    let root = "/opt/plugins/an-unusually-long-directory-name/that-keeps-going/devtools";
    let mut value = entry("devtools", "0");
    value["installedRoot"] = json!(root);
    let mut app = App::default();
    plugins::apply_event(
        &mut app,
        Event::Read(Ok(Some(catalog(json!({"plugins": [value]}))))),
    );
    plugins::handle_key(&mut app, &stub(), key(KeyCode::Enter));
    let screen = rows(&render(48, 40, |frame| app.draw(frame)));
    assert_eq!(drawn_value(&screen, "/opt/plugins"), root);
}
