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

    assert_eq!(classify("!", &skills, false), ClassifiedInput::EmptyBash);
    assert_eq!(
        classify("!printf hello", &skills, false),
        ClassifiedInput::Bash {
            command: "printf hello".to_owned(),
        }
    );
    assert_eq!(
        classify("/review-pr concise", &skills, false),
        ClassifiedInput::Skill {
            command: "/review-pr concise".to_owned(),
            name: "Review-PR".to_owned(),
        }
    );
    assert_eq!(
        classify("/help", &skills, false),
        ClassifiedInput::SlashCommand { command: "/help" }
    );
    assert_eq!(
        classify("/not-a-skill", &skills, false),
        ClassifiedInput::Prompt {
            text: "/not-a-skill".to_owned(),
        }
    );
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
