//! History recall, completion popup state, and config field parsing.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::completion_manager as completion;
use vibe_rs::config::{self, ConfigField};
use vibe_rs::config_fields::{format_value, parse};
use vibe_rs::utils::history_manager::HistoryManager;

#[test]
fn history_skips_blanks_and_immediate_repeats() {
    let mut history = HistoryManager::default();
    history.add("first");
    history.add("   ");
    history.add("");
    history.add("first");
    history.add("second");
    assert_eq!(history.entries().len(), 2);
}

#[test]
fn history_trims_entries_and_caps_the_limit() {
    let mut history = HistoryManager::default();
    for i in 0..105 {
        history.add(&format!("prompt {i}"));
    }
    assert_eq!(history.entries().len(), 100, "capped at MAX_ENTRIES");
    assert_eq!(
        history.entries()[0],
        "prompt 5",
        "the oldest overflow dropped"
    );
    assert_eq!(history.entries()[99], "prompt 104");
}

#[test]
fn history_navigation_stashes_the_live_input_and_restores_it() {
    let mut history = HistoryManager::default();
    history.add("older");
    history.add("newer");

    assert!(!history.is_navigating());
    assert_eq!(
        history.get_previous("draft text"),
        Some("newer".to_string())
    );
    assert_eq!(
        history.get_previous("still navigating"),
        Some("older".to_string())
    );
    assert_eq!(
        history.get_previous("at the oldest"),
        None,
        "clamped at the head"
    );

    assert_eq!(
        history.get_next(),
        Some("newer".to_string()),
        "steps back toward the newest"
    );
    assert_eq!(
        history.get_next(),
        Some("draft text".to_string()),
        "temp input restored"
    );
    assert!(
        !history.is_navigating(),
        "past the newest, navigation resets"
    );
    assert_eq!(history.get_next(), None, "no navigation to continue");
}

#[test]
fn history_reset_clears_navigation_mid_walk() {
    let mut history = HistoryManager::default();
    history.add("only");
    assert_eq!(history.get_previous("draft"), Some("only".to_string()));
    assert!(history.is_navigating());
    history.reset_navigation();
    assert!(!history.is_navigating());
    assert_eq!(history.get_next(), None);
}

#[test]
fn slash_completion_ranks_help_first_and_opens_the_popup() {
    let mut app = App::default();
    app.chat_input.input = "/he".into();
    app.chat_input.cursor = 3;
    completion::input_changed(&mut app);

    assert!(completion::is_open(&app));
    let labels: Vec<&str> = app
        .completion
        .entries
        .iter()
        .map(|entry| entry.label.as_str())
        .collect();
    assert_eq!(
        labels.first(),
        Some(&"/help"),
        "the boost outranks fuzzy ties"
    );
}

#[test]
fn completion_closes_on_no_trigger_and_clears_entries() {
    let mut app = App::default();
    app.chat_input.input = "plain text".into();
    completion::input_changed(&mut app);
    assert!(!completion::is_open(&app));
    assert!(app.completion.entries.is_empty());
}

#[test]
fn completion_navigation_cycles_with_wraparound() {
    let mut app = App::default();
    app.chat_input.input = "/".into();
    app.chat_input.cursor = 1;
    completion::input_changed(&mut app);
    let count = app.completion.entries.len();
    assert!(count >= 2, "the empty slash query lists commands");

    completion::navigate(&mut app, true);
    assert_eq!(app.completion.selected, 1);
    for _ in 0..count - 1 {
        completion::navigate(&mut app, true);
    }
    assert_eq!(app.completion.selected, 0, "down wraps to the top");

    completion::navigate(&mut app, false);
    assert_eq!(app.completion.selected, count - 1, "up wraps to the bottom");
}

#[test]
fn completion_dismiss_closes_and_remembers_the_dismissal() {
    let mut app = App::default();
    app.chat_input.input = "/he".into();
    app.chat_input.cursor = 3;
    completion::input_changed(&mut app);
    assert!(completion::dismiss(&mut app));
    assert!(!completion::is_open(&app));

    // A dismissed popup does not reopen on refresh without a real edit.
    completion::refresh(&mut app);
    assert!(app.completion.entries.is_empty());
    assert!(!completion::dismiss(&mut app), "nothing to dismiss");
}

#[test]
fn slash_accept_replaces_the_typed_prefix() {
    let mut app = App::default();
    app.chat_input.input = "/he".into();
    app.chat_input.cursor = 3;
    completion::input_changed(&mut app);
    assert!(completion::accept(&mut app));
    assert_eq!(app.chat_input.input, "/help");
    assert!(!completion::is_open(&app), "accepting closes the popup");
    assert_eq!(app.completion.selected, 0);
}

fn field(name: &str, popular: bool) -> ConfigField {
    ConfigField {
        name: name.into(),
        popular,
        path: name.into(),
        raw_value: json!("x"),
        kind: "string".into(),
        writable: true,
        overridden: false,
        enum_choices: vec![],
        description: String::new(),
        value_labels: Default::default(),
        layers: vec![],
    }
}

#[test]
fn config_screen_filter_resolves_fields_by_name_popular_first() {
    let mut app = App::default();
    app.config_screen.fields = vec![
        field("alpha", false),
        field("Theme", true),
        field("beta_theme", true),
        field("gamma", false),
    ];

    let all: Vec<&str> = config::filtered(&app)
        .iter()
        .map(|field| field.name.as_str())
        .collect();
    assert_eq!(
        all,
        ["Theme", "beta_theme", "alpha", "gamma"],
        "popular first"
    );

    app.config_screen.query = "theme".into();
    let found: Vec<&str> = config::filtered(&app)
        .iter()
        .map(|field| field.name.as_str())
        .collect();
    assert_eq!(
        found,
        ["Theme", "beta_theme"],
        "case-insensitive name match"
    );
}

fn config_field_wire() -> serde_json::Value {
    json!({
        "fields": [
            {
                "name": "theme",
                "value": "dark",
                "kind": "enum",
                "popular": true,
                "path": "/theme",
                "enumChoices": ["dark", "light"],
                "layerValues": [
                    {"layer": "user-toml", "value": "dark"}
                ]
            },
            {
                "name": "locked",
                "value": true,
                "kind": "bool",
                "path": "/locked",
                "layerValues": [{"layer": "admin", "value": true}]
            }
        ],
        "targets": ["user-toml", "local-toml"]
    })
}

#[test]
fn config_fields_parse_resolves_names_layers_and_choices() {
    let loaded = parse(&config_field_wire());
    assert_eq!(loaded.targets, vec!["user-toml", "local-toml"]);
    assert_eq!(loaded.fields.len(), 2);

    let theme = &loaded.fields[0];
    assert_eq!(theme.name, "theme");
    assert!(theme.popular);
    assert!(theme.writable, "the first layer is not admin");
    assert!(theme.overridden, "a non-default layer set it");
    assert_eq!(theme.enum_choices, vec!["dark", "light"]);
    assert_eq!(theme.layers, vec![("user-toml".to_owned(), json!("dark"))]);

    let locked = &loaded.fields[1];
    assert!(!locked.writable, "admin-owned fields are read-only");
}

#[test]
fn config_fields_parse_rejects_payloads_without_fields() {
    let loaded = parse(&json!({"targets": ["user-toml"]}));
    assert!(loaded.fields.is_empty());
    let empty = parse(&json!({}));
    assert!(empty.fields.is_empty() && empty.targets.is_empty());
}

#[test]
fn config_format_value_renders_each_kind_like_python() {
    assert_eq!(format_value(&json!(true)), "True");
    assert_eq!(format_value(&json!(false)), "False");
    assert_eq!(format_value(&json!("")), "\"\"");
    assert_eq!(format_value(&json!("dark")), "dark");
    assert_eq!(format_value(&json!(42)), "42");
    assert_eq!(format_value(&json!(null)), "—");
    assert_eq!(format_value(&json!([1])), "[1 item]");
    assert_eq!(format_value(&json!([1, 2])), "[2 items]");
    assert_eq!(format_value(&json!({"a": 1})), "{1 entry}");
    assert_eq!(format_value(&json!({"a": 1, "b": 2})), "{2 entries}");
}
