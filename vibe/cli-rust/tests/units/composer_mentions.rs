//! Accepted composer mentions: coloring spans, atomic caret moves and deletes.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::config;
use vibe_rs::input::{handle_key, handle_paste};
use vibe_rs::server::Client;

struct Harness {
    app: App,
    client: Arc<Client>,
    config_tx: tokio::sync::mpsc::Sender<config::Loaded>,
    _config_rx: tokio::sync::mpsc::Receiver<config::Loaded>,
}

impl Harness {
    fn new() -> Self {
        let mut app = App::default();
        app.completion.skills = vec![
            ("lint".into(), "Run the linters.".into()),
            ("code-review".into(), "Review the change.".into()),
        ];
        let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);
        Self {
            app,
            client: Arc::new(Client::stub()),
            config_tx,
            _config_rx,
        }
    }

    fn press(&mut self, code: KeyCode, modifiers: KeyModifiers) {
        let key = KeyEvent::new(code, modifiers);
        handle_key(&mut self.app, &self.client, &self.config_tx, key);
    }

    fn key(&mut self, code: KeyCode) {
        self.press(code, KeyModifiers::NONE);
    }

    fn type_text(&mut self, text: &str) {
        for character in text.chars() {
            self.key(KeyCode::Char(character));
        }
    }

    fn mentions(&self) -> Vec<(usize, usize)> {
        let input = &self.app.chat_input;
        input
            .mentions
            .spans(&input.input)
            .iter()
            .map(|mention| (mention.start, mention.end))
            .collect()
    }

    fn with_accepted_lint() -> Self {
        let mut harness = Self::new();
        harness.type_text("run /li");
        harness.key(KeyCode::Tab);
        harness
    }
}

#[tokio::test]
async fn accepting_a_completion_marks_the_mention() {
    let harness = Harness::with_accepted_lint();

    assert_eq!(harness.app.chat_input.input, "run /lint ");
    assert_eq!(harness.mentions(), [(4, 9)]);
}

#[tokio::test]
async fn escape_keeps_the_typed_text_plain() {
    let mut harness = Harness::new();
    harness.type_text("run /lint");
    harness.key(KeyCode::Esc);
    harness.type_text(" now");

    assert_eq!(harness.app.chat_input.input, "run /lint now");
    assert!(harness.mentions().is_empty());
}

#[tokio::test]
async fn backspace_at_the_end_of_a_mention_deletes_it_whole() {
    let mut harness = Harness::with_accepted_lint();

    harness.key(KeyCode::Backspace);
    assert_eq!(harness.app.chat_input.input, "run /lint");
    assert_eq!(harness.mentions(), [(4, 9)]);

    harness.key(KeyCode::Backspace);
    assert_eq!(harness.app.chat_input.input, "run ");
    assert_eq!(harness.app.chat_input.cursor, 4);
    assert!(harness.mentions().is_empty());
}

#[tokio::test]
async fn delete_before_a_mention_and_word_delete_remove_it_whole() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Left);
    harness.key(KeyCode::Left);
    assert_eq!(harness.app.chat_input.cursor, 4);

    harness.key(KeyCode::Delete);
    assert_eq!(harness.app.chat_input.input, "run  ");

    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Backspace);
    harness.press(KeyCode::Char('w'), KeyModifiers::CONTROL);
    assert_eq!(harness.app.chat_input.input, "run ");
}

#[tokio::test]
async fn the_caret_jumps_over_a_mention() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Left);
    assert_eq!(harness.app.chat_input.cursor, 9);

    harness.key(KeyCode::Left);
    assert_eq!(harness.app.chat_input.cursor, 4);

    harness.key(KeyCode::Right);
    assert_eq!(harness.app.chat_input.cursor, 9);
}

#[tokio::test]
async fn a_selection_touching_a_mention_covers_it_whole() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Backspace);
    harness.press(KeyCode::Left, KeyModifiers::SHIFT);
    harness.type_text("x");

    assert_eq!(harness.app.chat_input.input, "run x");
    assert!(harness.mentions().is_empty());
}

#[tokio::test]
async fn text_glued_to_a_mention_turns_it_back_into_plain_text() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Backspace);
    harness.type_text("s");

    assert_eq!(harness.app.chat_input.input, "run /lints");
    assert!(harness.mentions().is_empty());
}

#[tokio::test]
async fn a_placeholder_glued_to_the_next_word_keeps_its_hidden_text() {
    let mut harness = Harness::new();
    let pasted = (0..30)
        .map(|number| format!("line {number}"))
        .collect::<Vec<_>>()
        .join("\n");
    handle_paste(&mut harness.app, pasted.clone());
    harness.type_text(" summarize");
    for _ in 0.."summarize".len() {
        harness.key(KeyCode::Left);
    }

    harness.key(KeyCode::Backspace);

    let label = vibe_rs::long_paste::placeholder(&pasted);
    assert_eq!(harness.app.chat_input.input, format!("{label}summarize"));
    assert_eq!(harness.mentions(), [(0, label.len())]);
    assert_eq!(
        harness.app.chat_input.submitted_text(),
        format!("{pasted}summarize")
    );
}

#[tokio::test]
async fn edits_before_a_mention_shift_it() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Home);
    harness.type_text("please ");

    assert_eq!(harness.app.chat_input.input, "please run /lint ");
    assert_eq!(harness.mentions(), [(11, 16)]);
}

#[tokio::test]
async fn undo_restores_a_deleted_mention() {
    let mut harness = Harness::with_accepted_lint();
    harness.key(KeyCode::Backspace);
    harness.key(KeyCode::Backspace);
    harness.press(KeyCode::Char('z'), KeyModifiers::SUPER);

    assert_eq!(harness.app.chat_input.input, "run /lint ");
    assert_eq!(harness.mentions(), [(4, 9)]);
}

#[tokio::test]
async fn a_pasted_image_path_becomes_a_placeholder_mention() {
    let mut harness = Harness::new();
    harness.type_text("see");
    handle_paste(&mut harness.app, "/tmp/shot.png".into());

    assert_eq!(harness.app.chat_input.input, "see [Image #1] ");
    assert_eq!(harness.mentions(), [(4, 14)]);
}

#[tokio::test]
async fn a_shell_command_keeps_a_pasted_image_path() {
    let mut harness = Harness::new();
    harness.app.chat_input.mode = vibe_rs::input_modes::InputMode::Bash;
    harness.type_text("open ");
    handle_paste(&mut harness.app, "/tmp/shot.png".into());

    assert_eq!(harness.app.chat_input.input, "open /tmp/shot.png");
    assert!(harness.mentions().is_empty());
}

#[tokio::test]
async fn clearing_the_composer_forgets_its_mentions() {
    let mut harness = Harness::with_accepted_lint();
    harness.app.chat_input.clear();
    harness.app.chat_input.sync_mentions();

    assert!(harness.mentions().is_empty());
}

fn long_text() -> String {
    (0..12)
        .map(|number| format!("row {number}"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn text_starting_like_a_placeholder_typed_before_it_keeps_the_placeholder() {
    let mut harness = Harness::new();
    let paste = long_text();
    handle_paste(&mut harness.app, paste.clone());
    harness.key(KeyCode::Home);

    harness.type_text("[");

    let label = vibe_rs::long_paste::placeholder(&paste);
    assert_eq!(harness.app.chat_input.input, format!("[{label}"));
    assert_eq!(harness.mentions(), [(1, 1 + label.len())]);
    assert_eq!(harness.app.chat_input.submitted_text(), format!("[{paste}"));

    harness.key(KeyCode::Backspace);

    assert_eq!(harness.app.chat_input.input, label);
    assert_eq!(harness.mentions(), [(0, label.len())]);
}

#[test]
fn a_second_collapsed_paste_before_the_first_keeps_both() {
    let mut harness = Harness::new();
    let first = long_text();
    let second = format!("{}!", long_text());
    handle_paste(&mut harness.app, first.clone());
    harness.key(KeyCode::Home);

    handle_paste(&mut harness.app, second.clone());

    assert_eq!(harness.mentions().len(), 2);
    assert_eq!(
        harness.app.chat_input.submitted_text(),
        format!("{second}{first}")
    );
}
