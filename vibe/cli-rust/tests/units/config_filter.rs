//! Config search uses fuzzy relevance without losing section or selection ordering.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::{config, config_fields};

fn app_with(fields: &[(&str, &str, bool)], query: &str) -> App {
    let mut app = App::default();
    let loaded = config_fields::parse(&json!({
        "fields": fields.iter().map(|(name, description, popular)| json!({
            "name": name, "path": format!("/{name}"), "kind": "int", "value": 42,
            "description": description, "popular": popular,
        })).collect::<Vec<_>>(),
    }));
    config::apply_loaded(&mut app, loaded);
    app.config_screen.query = query.into();
    app
}

fn names(app: &App) -> Vec<&str> {
    config::filtered(app)
        .into_iter()
        .map(|field| field.name.as_str())
        .collect()
}

#[test]
fn abbreviations_match_case_insensitively_with_surrounding_whitespace() {
    let app = app_with(&[("theme", "", true), ("api_timeout", "", false)], " APTO ");
    assert_eq!(names(&app), ["api_timeout"]);
}

#[test]
fn name_matches_outrank_popular_description_matches() {
    let app = app_with(
        &[("theme", "Color scheme", true), ("color_depth", "", false)],
        "color",
    );
    assert_eq!(names(&app), ["color_depth", "theme"]);
}

#[test]
fn popular_matches_win_near_ties_in_a_short_list() {
    let app = app_with(&[("foobar", "", false), ("foobare", "", true)], "foobar");
    assert_eq!(names(&app), ["foobare", "foobar"]);
}

#[test]
fn equal_scores_keep_the_server_order() {
    let app = app_with(&[("theme_b", "", false), ("theme_a", "", false)], "theme");
    assert_eq!(names(&app), ["theme_b", "theme_a"]);
}

#[test]
fn empty_query_keeps_popular_then_advanced_without_ranking() {
    let app = app_with(
        &[
            ("z_advanced", "", false),
            ("popular_b", "", true),
            ("a_advanced", "", false),
            ("popular_a", "", true),
        ],
        "  ",
    );
    assert_eq!(
        names(&app),
        ["popular_b", "popular_a", "z_advanced", "a_advanced"]
    );
}

#[test]
fn larger_result_sets_rank_within_each_section() {
    let app = app_with(
        &[
            ("retry_api", "", true),
            ("api_timeout", "", false),
            ("api_retries", "", false),
            ("api_base", "", false),
            ("api_headers", "", false),
            ("api_key", "", false),
            ("api_mode", "", true),
        ],
        "api",
    );
    assert_eq!(
        names(&app),
        [
            "api_mode",
            "retry_api",
            "api_timeout",
            "api_retries",
            "api_base",
            "api_headers",
            "api_key",
        ]
    );
}

#[test]
fn zero_score_and_missing_matches_are_excluded() {
    let name = format!("a{}b", "_".repeat(200));
    let app = app_with(&[(&name, "", true), ("theme", "", false)], "ab");
    assert!(names(&app).is_empty());
}

#[test]
fn refresh_preserves_the_field_when_fuzzy_ranks_change() {
    let mut app = app_with(&[("api_timeout", "", false), ("api_key", "", false)], "api");
    app.config_screen.selected = 1;
    let fields = app_with(&[("api_timeout", "", false), ("api_key", "", true)], "")
        .config_screen
        .fields;
    config::apply_loaded(
        &mut app,
        config::Loaded {
            fields,
            targets: Vec::new(),
        },
    );
    assert_eq!(app.config_screen.query, "api");
    assert_eq!(names(&app)[app.config_screen.selected], "api_key");
    assert_eq!(app.config_screen.selected, 0);
}
