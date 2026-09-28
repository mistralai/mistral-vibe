//! Input mode and composer navigation behavior.

use vibe_rs::app::{App, ChatInput};
use vibe_rs::input;
use vibe_rs::input_modes::{classify, ClassifiedInput, InputMode};

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
fn pasted_slash_text_opens_completion_without_changing_the_mode() {
    let mut app = App::default();

    input::handle_paste(&mut app, "/he".to_owned());

    assert_eq!(app.chat_input.mode, InputMode::Prompt);
    assert_eq!(app.chat_input.input, "/he");
    assert!(vibe_rs::completion_manager::is_open(&app));
    assert!(vibe_rs::completion_manager::accept(&mut app));
    assert_eq!(app.chat_input.full_text(), "/help");
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
