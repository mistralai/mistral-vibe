//! Input mode and composer navigation behavior.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, ChatInput};
use vibe_rs::input;
use vibe_rs::input_modes::{
    classify, classify_submitted, submitted_value, ClassifiedInput, InputMode,
};

#[test]
fn full_text_keeps_mode_prefix_outside_the_editable_body() {
    let mut input = ChatInput::default();
    input.load_full_text("!printf hello".to_owned());

    assert_eq!(input.mode, InputMode::Bash);
    assert_eq!(input.input, "printf hello");
    assert_eq!(input.cursor, "printf hello".len());
    assert_eq!(input.full_text(), "!printf hello");

    input.clear();
    assert_eq!(input.mode, InputMode::Prompt);
    assert!(input.full_text().is_empty());
}

#[test]
fn a_loaded_message_stays_a_prompt_whatever_its_first_character() {
    let mut input = ChatInput::default();
    input.load_prompt_text("/config".to_owned());

    assert_eq!(input.mode, InputMode::Prompt);
    assert_eq!(input.full_text(), "/config");
    assert_eq!(input.cursor, "/config".len());
}

#[test]
fn input_classification_distinguishes_bash_skill_and_prompt() {
    let skills = vec![("Review-PR".to_owned(), "Review the current PR".to_owned())];

    assert_eq!(classify("!", &skills), ClassifiedInput::EmptyBash);
    assert_eq!(
        classify("!printf hello", &skills),
        ClassifiedInput::Bash {
            command: "printf hello".to_owned(),
        }
    );
    assert_eq!(
        classify("/review-pr concise", &skills),
        ClassifiedInput::Skill {
            command: "/review-pr concise".to_owned(),
            name: "Review-PR".to_owned(),
        }
    );
    assert_eq!(
        classify("/help", &skills),
        ClassifiedInput::SlashCommand { command: "/help" }
    );
    assert_eq!(
        classify("/not-a-skill", &skills),
        ClassifiedInput::Prompt {
            text: "/not-a-skill".to_owned(),
        }
    );
    assert_eq!(
        classify("/demo", &skills),
        ClassifiedInput::Prompt {
            text: "/demo".to_owned(),
        }
    );
}

#[test]
fn teleport_commands_are_classified_as_commands_without_configuration() {
    for command in ["/teleport", "/remote-project"] {
        assert_eq!(
            classify(command, &[]),
            ClassifiedInput::SlashCommand { command }
        );
        assert!(!vibe_rs::commands::is_side_channel(command));
    }
}

#[test]
fn prompt_mode_text_is_a_message_whatever_its_first_character() {
    for value in ["/help", "/loop 5m check", "!ls", "&deploy"] {
        assert_eq!(
            classify_submitted(InputMode::Prompt, value, &[]),
            ClassifiedInput::Prompt {
                text: value.to_owned()
            },
            "{value:?}"
        );
    }
    assert_eq!(
        classify_submitted(InputMode::Slash, "/help", &[]),
        ClassifiedInput::SlashCommand { command: "/help" }
    );
    assert_eq!(
        classify_submitted(InputMode::Prompt, "exit", &[]),
        ClassifiedInput::SlashCommand { command: "/exit" }
    );
}

#[test]
fn leading_whitespace_before_a_mode_character_survives_submission_and_reload() {
    let value = submitted_value(InputMode::Prompt, " /help \n");
    assert_eq!(value, " /help");
    assert_eq!(
        classify_submitted(InputMode::Prompt, &value, &[]),
        ClassifiedInput::Prompt {
            text: " /help".to_owned()
        }
    );

    let mut input = ChatInput::default();
    input.load_full_text(value);
    assert_eq!(
        (input.mode, input.input.as_str()),
        (InputMode::Prompt, " /help")
    );

    assert_eq!(submitted_value(InputMode::Prompt, "  hello "), "hello");
    assert_eq!(submitted_value(InputMode::Slash, " /help "), "/help");
}

#[test]
fn a_mode_character_opens_its_mode_only_over_empty_or_fully_selected_text() {
    let mut input = ChatInput::default();
    assert!(input.apply_mode_key(&key(KeyCode::Char('/'))));
    assert_eq!((input.mode, input.input.as_str()), (InputMode::Slash, ""));

    let mut input = prompt("config", 0, None);
    assert!(!input.apply_mode_key(&key(KeyCode::Char('/'))));
    assert_eq!(input.mode, InputMode::Prompt);

    let mut input = prompt("hello", 5, Some(1));
    assert!(!input.apply_mode_key(&key(KeyCode::Char('!'))));

    let mut input = prompt("hello", 5, Some(0));
    assert!(input.apply_mode_key(&key(KeyCode::Char('!'))));
    assert_eq!((input.mode, input.input.as_str()), (InputMode::Bash, ""));
    assert_eq!((input.cursor, input.anchor), (0, None));

    let mut input = ChatInput::default();
    let ctrl = KeyEvent::new(KeyCode::Char('/'), KeyModifiers::CONTROL);
    assert!(!input.apply_mode_key(&ctrl));
    assert_eq!(input.mode, InputMode::Prompt);
}

#[test]
fn backspace_at_the_start_leaves_the_mode_and_keeps_the_text() {
    for mode in [InputMode::Slash, InputMode::Bash, InputMode::Teleport] {
        let mut input = prompt("config", 0, None);
        input.mode = mode;
        assert!(input.apply_mode_key(&key(KeyCode::Backspace)));
        assert_eq!(
            (input.mode, input.input.as_str()),
            (InputMode::Prompt, "config")
        );
    }

    let mut input = prompt("config", 1, None);
    input.mode = InputMode::Slash;
    assert!(!input.apply_mode_key(&key(KeyCode::Backspace)));

    let mut input = prompt("config", 0, Some(3));
    input.mode = InputMode::Slash;
    assert!(!input.apply_mode_key(&key(KeyCode::Backspace)));
    assert_eq!(input.mode, InputMode::Slash);

    let mut input = prompt("config", 0, None);
    assert!(!input.apply_mode_key(&key(KeyCode::Backspace)));
}

#[test]
fn pasting_into_an_empty_prompt_opens_the_mode_it_starts_with() {
    for (pasted, mode, body) in [
        ("/he", InputMode::Slash, "he"),
        ("!ls -la", InputMode::Bash, "ls -la"),
        ("&deploy", InputMode::Prompt, "&deploy"),
        (
            "/usr/bin/tool --help",
            InputMode::Prompt,
            "/usr/bin/tool --help",
        ),
        ("// note", InputMode::Prompt, "// note"),
        (" /config", InputMode::Prompt, " /config"),
    ] {
        let mut app = App::default();
        input::handle_paste(&mut app, pasted.to_owned());
        assert_eq!(
            (app.chat_input.mode, app.chat_input.input.as_str()),
            (mode, body),
            "{pasted:?}"
        );
    }

    let mut app = App::default();
    input::handle_paste(&mut app, "/he".to_owned());
    assert!(vibe_rs::completion_manager::is_open(&app));
    assert!(vibe_rs::completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.full_text(), "/help");
}

#[test]
fn pasting_over_the_whole_prompt_opens_the_mode() {
    let mut app = App::default();
    app.chat_input.input = "draft".to_owned();
    app.chat_input.cursor = 5;
    app.chat_input.anchor = Some(0);

    input::handle_paste(&mut app, "/model".to_owned());

    assert_eq!(app.chat_input.mode, InputMode::Slash);
    assert_eq!(app.chat_input.input, "model");
}

#[test]
fn a_literal_leading_slash_opens_no_command_menu() {
    let mut app = App::default();
    app.chat_input.input = "/he".to_owned();
    app.chat_input.cursor = 3;

    vibe_rs::completion_manager::input_changed(&mut app);

    assert!(!vibe_rs::completion_manager::is_open(&app));
}

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn prompt(text: &str, cursor: usize, anchor: Option<usize>) -> ChatInput {
    ChatInput {
        input: text.to_owned(),
        cursor,
        anchor,
        ..ChatInput::default()
    }
}

#[test]
fn paste_into_existing_text_keeps_prefixes_literal() {
    let mut app = App::default();
    app.chat_input.input = "before ".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();

    input::handle_paste(&mut app, "/after".to_owned());

    assert_eq!(app.chat_input.mode, InputMode::Prompt);
    assert_eq!(app.chat_input.input, "before /after");
}

#[test]
fn wrapped_prompt_navigation_keeps_a_leading_slash_in_the_body() {
    let mut app = wrapped_input(InputMode::Prompt, "/abcdefgh");

    assert_eq!(vibe_rs::ui::chat_input::page_cursor(&app, true), 5);
    assert_eq!(
        vibe_rs::ui::chat_input::vertical_cursor(&app, true),
        (5, true)
    );

    app.chat_input.cursor = 5;
    assert_eq!(vibe_rs::ui::chat_input::page_cursor(&app, false), 0);
    assert_eq!(
        vibe_rs::ui::chat_input::vertical_cursor(&app, false),
        (0, true)
    );
}

#[test]
fn wrapped_bash_navigation_keeps_an_absolute_path_in_the_body() {
    let mut app = wrapped_input(InputMode::Bash, "/usr/bin/tool");

    assert_eq!(vibe_rs::ui::chat_input::page_cursor(&app, true), 5);
    assert_eq!(
        vibe_rs::ui::chat_input::vertical_cursor(&app, true),
        (5, true)
    );

    app.chat_input.cursor = 5;
    assert_eq!(vibe_rs::ui::chat_input::page_cursor(&app, false), 0);
    assert_eq!(
        vibe_rs::ui::chat_input::vertical_cursor(&app, false),
        (0, true)
    );
}

fn wrapped_input(mode: InputMode, input: &str) -> App {
    let mut app = App::default();
    app.view.input_area.width = 8;
    app.view.input_area.height = 3;
    app.chat_input.mode = mode;
    app.chat_input.input = input.to_owned();
    app
}
