//! Composer mode edges: literal-looking pastes, undo of a mode switch, submitted whitespace.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::input;
use vibe_rs::input_modes::{classify_submitted, submitted_value, ClassifiedInput, InputMode};
use vibe_rs::voice::VoiceEvent;

use crate::teleport_support::stub;

#[test]
fn pastes_that_only_look_prefixed_stay_literal_prompts() {
    for (pasted, mode) in [
        ("![diagram](https://example.com/a.png)", InputMode::Prompt),
        ("/* block comment */", InputMode::Prompt),
        ("/** doc */", InputMode::Prompt),
        ("/Users/me/notes.md", InputMode::Prompt),
        ("/tmp", InputMode::Slash),
        ("/vibe:skill-creator now", InputMode::Slash),
        ("!git status", InputMode::Bash),
        ("/", InputMode::Slash),
        ("&mut self", InputMode::Prompt),
    ] {
        let mut app = App::default();
        input::handle_paste(&mut app, pasted.to_owned());
        assert_eq!(app.chat_input.mode, mode, "{pasted:?}");
        assert_eq!(app.chat_input.full_text(), pasted, "{pasted:?}");
    }
}

#[test]
fn dictation_never_switches_the_mode() {
    let mut app = App::default();

    app.apply_voice_event(VoiceEvent::TextDelta("/".to_owned()));
    app.apply_voice_event(VoiceEvent::TextDelta("usr/local/bin".to_owned()));

    assert_eq!(app.chat_input.mode, InputMode::Prompt);
    assert_eq!(app.chat_input.input, "/usr/local/bin");
}

#[test]
fn undo_after_typing_a_mode_over_selected_text_restores_it() {
    let mut app = App::default();
    let (tx, _rx) = tokio::sync::mpsc::channel(1);
    input::handle_paste(&mut app, "draft".to_owned());
    app.chat_input.anchor = Some(0);

    input::handle_key(&mut app, &stub(), &tx, KeyCode::Char('!').into());
    assert_eq!(
        (app.chat_input.mode, app.chat_input.input.as_str()),
        (InputMode::Bash, "")
    );

    assert!(app.chat_input.restore_edit(false));
    assert_eq!(
        (app.chat_input.mode, app.chat_input.input.as_str()),
        (InputMode::Prompt, "draft")
    );
}

#[test]
fn modified_backspace_at_the_start_also_leaves_the_mode() {
    let mut app = App::default();
    let (tx, _rx) = tokio::sync::mpsc::channel(1);
    app.chat_input.load_full_text("/config".to_owned());
    app.chat_input.cursor = 0;

    let alt_backspace = KeyEvent::new(KeyCode::Backspace, KeyModifiers::ALT);
    input::handle_key(&mut app, &stub(), &tx, alt_backspace);

    assert_eq!(
        (app.chat_input.mode, app.chat_input.input.as_str()),
        (InputMode::Prompt, "config")
    );
}

#[test]
fn a_literal_leading_skill_keeps_its_skill_classification() {
    let skills = vec![("review-pr".to_owned(), "Review the PR".to_owned())];

    assert_eq!(
        classify_submitted(InputMode::Prompt, " /review-pr now", &skills),
        ClassifiedInput::Skill {
            command: " /review-pr now".to_owned(),
            name: "review-pr".to_owned(),
        }
    );
    assert_eq!(
        classify_submitted(InputMode::Prompt, "/config", &skills),
        ClassifiedInput::Prompt {
            text: "/config".to_owned()
        }
    );
}

#[test]
fn leading_blank_lines_before_a_literal_prefix_collapse_to_one_space() {
    assert_eq!(
        submitted_value(InputMode::Prompt, "\n\n  /help\n"),
        " /help"
    );
    assert_eq!(submitted_value(InputMode::Prompt, "/help "), "/help");
    assert_eq!(submitted_value(InputMode::Prompt, "\n hello"), "hello");
}
