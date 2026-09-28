//! Cursor-aware file completion detection and replacement.

use vibe_rs::app::App;
use vibe_rs::completion_manager::{self, CompletionEntry};
use vibe_rs::input_modes::InputMode;

fn composer(before: &str, after: &str) -> App {
    let mut app = App::default();
    app.chat_input.input = format!("{before}{after}");
    app.chat_input.cursor = before.len();
    app
}

fn offer(app: &mut App, label: &str) {
    app.completion.entries = vec![CompletionEntry {
        label: label.into(),
        description: String::new(),
    }];
}

#[test]
fn file_detection_uses_only_text_before_the_caret() {
    for (before, after, expected) in [
        ("", "@src", false),
        ("prefix ", "@src", false),
        ("@", "src", true),
        ("pré @sr", "c more text", true),
        ("@sr", " @later", true),
        ("@src ", "more", false),
        ("@first @se", "cond", true),
    ] {
        let app = composer(before, after);
        assert_eq!(
            completion_manager::active_is_file(&app),
            expected,
            "{before}|{after}"
        );
    }
}

#[test]
fn file_accept_replaces_only_through_the_caret() {
    let mut app = composer("pré @sr", "c suffix @later");
    app.chat_input.anchor = Some(0);
    offer(&mut app, "@src/main.rs");

    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "pré @src/main.rs c suffix @later");
    assert_eq!(app.chat_input.cursor, "pré @src/main.rs ".len());
    assert_eq!(app.chat_input.anchor, None);
    assert!(!completion_manager::is_open(&app));
}

#[test]
fn file_accept_keeps_a_following_space_and_multiline_suffix() {
    let mut app = composer("check @ré", " next\nline");
    offer(&mut app, "@résumé.md");

    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "check @résumé.md  next\nline");
    assert_eq!(app.chat_input.cursor, "check @résumé.md ".len());
}

#[test]
fn directory_accept_does_not_insert_a_space() {
    let mut app = composer("read @sr", " suffix");
    offer(&mut app, "@src/");

    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "read @src/ suffix");
    assert_eq!(app.chat_input.cursor, "read @src/".len());
}

#[test]
fn file_completion_clamps_external_caret_offsets_to_utf8_boundaries() {
    for (cursor, expected) in [(3, "@résumé.md é"), (usize::MAX, "@résumé.md ")] {
        let mut app = composer("@ré", "");
        app.chat_input.cursor = cursor;
        offer(&mut app, "@résumé.md");

        assert!(completion_manager::accept(&mut app));
        assert_eq!(app.chat_input.input, expected);
        assert_eq!(app.chat_input.cursor, "@résumé.md ".len());
    }
}

#[test]
fn file_accept_at_end_appends_a_space() {
    let mut app = composer("@sr", "");
    offer(&mut app, "@src/main.rs");

    assert!(completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "@src/main.rs ");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
}

#[test]
fn accept_without_an_active_token_leaves_input_unchanged() {
    let mut app = composer("", "@src");
    offer(&mut app, "@src/main.rs");

    assert!(!completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "@src");
    assert_eq!(app.chat_input.cursor, 0);
}

#[test]
fn accept_without_suggestions_leaves_input_unchanged() {
    let mut app = composer("@missing", " suffix");

    assert!(!completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.input, "@missing suffix");
    assert_eq!(app.chat_input.cursor, "@missing".len());
}

#[test]
fn refresh_resets_highlight_only_when_the_suggestion_list_changes() {
    let mut app = composer("/", "");
    completion_manager::refresh(&mut app);
    assert!(completion_manager::is_open(&app));
    completion_manager::navigate(&mut app, true);
    completion_manager::navigate(&mut app, true);
    let highlighted = app.completion.selected;
    assert!(highlighted > 0);

    // An identical rebuild (a caret move within the same query) keeps the
    // highlight, like Python's path_completion `_update_suggestions`.
    completion_manager::refresh(&mut app);
    assert_eq!(app.completion.selected, highlighted);

    // A different query rebuilds a different list: the highlight restarts at
    // the top suggestion instead of accepting a stale index.
    app.chat_input.input = "/he".into();
    app.chat_input.cursor = 3;
    completion_manager::refresh(&mut app);
    assert!(completion_manager::is_open(&app));
    assert_eq!(app.completion.selected, 0);
    assert_eq!(app.completion.scroll, 0);
}

#[test]
fn slash_accept_preserves_existing_replacement_and_caret_behavior() {
    for (mode, before, after, expected) in [
        (InputMode::Slash, "sta", "tus arg", "status arg"),
        (InputMode::Prompt, "/sta", "tus arg", "/status arg"),
    ] {
        let mut app = composer(before, after);
        app.chat_input.mode = mode;
        app.chat_input.anchor = Some(0);
        offer(&mut app, "/status");

        assert!(completion_manager::accept(&mut app));
        assert_eq!(app.chat_input.input, expected);
        assert_eq!(app.chat_input.cursor, expected.len());
        assert_eq!(app.chat_input.anchor, None);
    }
}
