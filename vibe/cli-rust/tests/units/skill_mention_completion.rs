//! `/skill` mention completion: caret-based trigger, ranking, and acceptance.

use vibe_rs::app::App;
use vibe_rs::completion_manager;
use vibe_rs::input_modes::InputMode;

fn app_with(input: &str) -> App {
    let mut app = App::default();
    app.completion.skills = vec![
        ("code-review".into(), "Review the current change.".into()),
        ("lint".into(), "Run the linters.".into()),
        ("vibe:skill-creator".into(), "Create a skill.".into()),
    ];
    app.chat_input.input = input.into();
    app.chat_input.cursor = input.len();
    completion_manager::input_changed(&mut app);
    app
}

fn labels(app: &App) -> Vec<&str> {
    app.completion
        .entries
        .iter()
        .map(|entry| entry.label.as_str())
        .collect()
}

#[test]
fn slash_mid_prompt_lists_matching_skills() {
    let app = app_with("please run /li");

    assert_eq!(labels(&app), ["/lint"]);
    assert_eq!(app.completion.entries[0].description, "Run the linters.");
    assert!(completion_manager::active_is_mention(&app));
    assert!(!completion_manager::active_is_file(&app));
}

#[test]
fn a_bare_slash_mid_prompt_lists_every_skill() {
    let app = app_with("run /");

    assert_eq!(
        labels(&app),
        ["/code-review", "/lint", "/vibe:skill-creator"]
    );
}

#[test]
fn a_leading_slash_keeps_the_slash_command_menu() {
    let app = app_with("/co");

    assert!(!completion_manager::active_is_mention(&app));
    assert!(labels(&app).contains(&"/code-review"));
    assert!(labels(&app).contains(&"/compact"));
}

#[test]
fn accepting_replaces_the_token_adds_a_space_and_marks_it() {
    let mut app = app_with("run /co then stop");
    app.chat_input.cursor = "run /co".len();
    completion_manager::refresh(&mut app);

    assert!(completion_manager::accept(&mut app));

    assert_eq!(app.chat_input.input, "run /code-review  then stop");
    assert_eq!(app.chat_input.cursor, "run /code-review ".len());
    assert!(!completion_manager::is_open(&app));
    let spans = app.chat_input.mentions.spans(&app.chat_input.input);
    assert_eq!((spans[0].start, spans[0].end), (4, 16));
}

#[test]
fn slash_after_a_slash_command_completes_the_mention() {
    let mut app = app_with("/code-review with /li");
    assert_eq!(labels(&app), ["/lint"]);

    app.chat_input.mode = InputMode::Slash;
    app.chat_input.input = "code-review with /li".into();
    app.chat_input.cursor = app.chat_input.input.len();
    completion_manager::refresh(&mut app);
    assert_eq!(labels(&app), ["/lint"]);
}

#[test]
fn a_dollar_word_is_plain_text() {
    let app = app_with("please run $li");

    assert!(!completion_manager::active_is_mention(&app));
    assert!(!completion_manager::is_open(&app));
}

#[test]
fn a_slash_inside_a_word_a_path_or_shell_mode_is_not_a_mention() {
    assert!(!completion_manager::active_is_mention(&app_with("and/li")));
    assert!(!completion_manager::is_open(&app_with("see /usr/bin/li")));

    let mut app = app_with("");
    app.chat_input.mode = InputMode::Bash;
    app.chat_input.input = "ls /li".into();
    app.chat_input.cursor = app.chat_input.input.len();
    completion_manager::refresh(&mut app);
    assert!(!completion_manager::is_open(&app));
}

#[test]
fn moving_the_caret_off_the_token_closes_the_popup() {
    let mut app = app_with("run /li and more");
    app.chat_input.cursor = "run /li".len();
    completion_manager::refresh(&mut app);
    assert_eq!(labels(&app), ["/lint"]);

    app.chat_input.cursor = app.chat_input.input.len();
    completion_manager::refresh(&mut app);
    assert!(!completion_manager::is_open(&app));
}
