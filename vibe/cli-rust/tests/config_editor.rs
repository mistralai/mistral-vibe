//! Config editor state keeps one authoritative value and invalidates stale geometry.

use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::config::{self, ConfigField};
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::ui;
use vibe_rs::{config_edit, config_fields};

fn enum_field(value: &str) -> ConfigField {
    ConfigField {
        name: "mode".into(),
        popular: true,
        path: "/mode".into(),
        raw_value: json!(value),
        kind: "enum".into(),
        writable: true,
        overridden: false,
        enum_choices: vec!["first".into(), "second".into()],
        description: String::new(),
        value_labels: Default::default(),
        layers: vec![],
    }
}

#[test]
fn unknown_choice_uses_the_highlighted_fallback_value() {
    let mut app = App::default();

    config_edit::open(&mut app, enum_field("removed"));

    let edit = app.config_screen.edit.as_ref().unwrap();
    assert_eq!(edit.draft, "first");
    assert_eq!(edit.cursor, edit.draft.len());
}

#[test]
fn resize_invalidates_editor_geometry() {
    let mut app = App::default();
    config_edit::open(&mut app, enum_field("first"));
    app.config_screen.edit.as_mut().unwrap().input_width = Some(42);

    config_edit::resized(&mut app);

    assert_eq!(app.config_screen.edit.unwrap().input_width, None);
}

#[test]
fn opening_a_setting_preselects_the_layer_holding_its_value() {
    let mut app = App::default();
    app.config_screen.targets = vec![
        "user-toml".into(),
        "project-toml".into(),
        "overrides".into(),
    ];
    let mut field = enum_field("first");
    field.layers = vec![
        ("overrides".into(), json!("first")),
        ("user-toml".into(), json!("second")),
        ("default".into(), json!("second")),
    ];

    config_edit::open(&mut app, field);

    assert_eq!(app.config_screen.edit.unwrap().target, 2);
}

#[test]
fn unwritable_layers_fall_back_to_the_default_save_target() {
    let mut app = App::default();
    app.config_screen.targets = vec!["user-toml".into(), "overrides".into()];
    let mut field = enum_field("first");
    field.layers = vec![("environment".into(), json!("first"))];

    config_edit::open(&mut app, field);

    assert_eq!(app.config_screen.edit.unwrap().target, 0);
}

#[test]
fn the_unpinned_model_reads_as_the_default_instead_of_an_empty_string() {
    let mut app = App::default();
    app.model_picker.default_display_name = "Mistral Medium 3.5".into();

    config::apply_loaded(
        &mut app,
        config_fields::parse(&json!({
            "fields": [{
                "name": "active_model", "path": "/active_model", "kind": "str", "value": "",
                "layerValues": [{"layer": "default", "value": ""}],
            }],
            "targets": ["user-toml"],
        })),
    );

    let field = config::filtered(&app)[0];
    assert_eq!(
        field.display_value(),
        "default (currently Mistral Medium 3.5)"
    );
    assert_eq!(
        field.labeled(&json!("")),
        "default (currently Mistral Medium 3.5)"
    );
}

#[test]
fn a_multiline_draft_scrolls_only_once_the_caret_leaves_the_view() {
    let mut app = App::default();
    let mut terminal = Terminal::new(TestBackend::new(100, 40)).unwrap();
    let lines: Vec<String> = (0..30).map(|index| format!("line{index:02}")).collect();
    let mut field = enum_field("first");
    field.kind = "list".into();
    field.enum_choices = Vec::new();
    field.raw_value = json!(lines);
    config_edit::open(&mut app, field);
    let draw = |app: &mut App, terminal: &mut Terminal<TestBackend>| {
        terminal
            .draw(|frame| ui::config::draw(app, frame, frame.area()))
            .unwrap();
        app.config_screen.edit.as_ref().unwrap().scroll
    };
    let caret_at = |line: usize| lines[..line].iter().map(|text| text.len() + 1).sum();

    let bottom = draw(&mut app, &mut terminal);

    assert_eq!(bottom, Some(20));
    app.config_screen.edit.as_mut().unwrap().cursor = caret_at(25);
    assert_eq!(draw(&mut app, &mut terminal), Some(20));
    app.config_screen.edit.as_mut().unwrap().cursor = caret_at(19);
    assert_eq!(draw(&mut app, &mut terminal), Some(19));
}

#[test]
fn tiny_config_browser_remains_visible_and_modal() {
    let mut app = App::default();
    let mut terminal = Terminal::new(TestBackend::new(19, 7)).unwrap();

    terminal
        .draw(|frame| ui::config::draw(&mut app, frame, frame.area()))
        .unwrap();

    assert_eq!(mouse::target_at(&app, (0, 0)), Some(MouseTarget::Blocked));
    let screen =
        terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .fold(String::new(), |mut text, cell| {
                text.push_str(cell.symbol());
                text
            });
    assert!(screen.contains("Enlarge terminal"));
}
