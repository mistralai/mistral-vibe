//! Slash-command popup matching: fuzzy, case-insensitive, prefix-first, live skill entries.

use vibe_rs::app::App;
use vibe_rs::completion_manager as completion;
use vibe_rs::input_modes::InputMode;

fn type_slash(app: &mut App, query: &str) -> Vec<String> {
    app.chat_input.mode = InputMode::Slash;
    app.chat_input.input = query.into();
    app.chat_input.cursor = query.len();
    completion::input_changed(app);
    app.completion
        .entries
        .iter()
        .map(|entry| entry.label.clone())
        .collect()
}

#[test]
fn a_substring_or_a_later_hyphen_segment_matches() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "retention")
            .first()
            .map(String::as_str),
        Some("/data-retention")
    );
    assert_eq!(
        type_slash(&mut app, "level").first().map(String::as_str),
        Some("/log-level")
    );
}

#[test]
fn scattered_characters_match_and_rank_below_prefixes() {
    let mut app = App::default();
    assert!(type_slash(&mut app, "dtrt").contains(&"/data-retention".to_owned()));

    let labels = type_slash(&mut app, "re");
    let first_other = labels
        .iter()
        .position(|label| !label.starts_with("/re"))
        .unwrap_or(labels.len());
    assert!(first_other > 0, "{labels:?}");
    assert!(
        labels[first_other..]
            .iter()
            .all(|label| !label.starts_with("/re")),
        "every prefix match ranks first: {labels:?}"
    );
}

#[test]
fn matching_ignores_case() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "HELP").first().map(String::as_str),
        Some("/help")
    );
}

#[test]
fn an_alias_is_suggested_under_its_own_name() {
    let mut app = App::default();
    assert_eq!(
        type_slash(&mut app, "new").first().map(String::as_str),
        Some("/new")
    );
    let clear = app.completion.entries[0].description.clone();
    assert!(clear.starts_with("Start a new conversation"), "{clear}");
}

#[test]
fn no_match_closes_the_popup() {
    let mut app = App::default();
    assert!(type_slash(&mut app, "zzqx").is_empty());
    assert!(!completion::is_open(&app));
}

#[test]
fn skills_join_the_menu_as_they_are_enabled_and_leave_when_disabled() {
    let mut app = App::default();
    app.completion.skills = vec![("deploy-app".into(), "Deploy the app".into())];
    assert_eq!(
        type_slash(&mut app, "deploy").first().map(String::as_str),
        Some("/deploy-app")
    );
    assert_eq!(app.completion.entries[0].description, "Deploy the app");

    app.completion.skills.clear();
    assert!(!type_slash(&mut app, "deploy").contains(&"/deploy-app".to_owned()));
}
