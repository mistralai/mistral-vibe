//! Collapsed pastes and image placeholders across composer round trips:
//! history and queue drafts, line deletion, undo, and rewind.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::input::handle_paste;
use vibe_rs::long_paste::placeholder;

use crate::support::send_key;

struct Harness {
    app: App,
}

impl Harness {
    fn new() -> Self {
        Self {
            app: App::default(),
        }
    }

    fn press(&mut self, code: KeyCode, modifiers: KeyModifiers) {
        send_key(&mut self.app, KeyEvent::new(code, modifiers));
    }

    fn pastes(&self) -> Vec<String> {
        let input = &self.app.chat_input;
        input
            .mentions
            .spans(&input.input)
            .iter()
            .filter_map(|mention| mention.paste.as_deref().map(str::to_owned))
            .collect()
    }
}

fn long(tag: &str) -> String {
    (0..12)
        .map(|number| format!("{tag} {number}"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn a_draft_keeps_its_collapsed_paste_through_history_recall() {
    let mut harness = Harness::new();
    harness.app.chat_input.history.add("earlier prompt");
    let paste = long("row");
    handle_paste(&mut harness.app, paste.clone());
    harness.press(KeyCode::Home, KeyModifiers::NONE);

    harness.press(KeyCode::Up, KeyModifiers::NONE);
    assert_eq!(harness.app.chat_input.input, "earlier prompt");
    harness.press(KeyCode::Down, KeyModifiers::NONE);

    assert_eq!(harness.app.chat_input.input, placeholder(&paste));
    assert_eq!(harness.pastes(), [paste.as_str()]);
    assert_eq!(harness.app.chat_input.submitted_text(), paste);
}

#[test]
fn deleting_a_line_removes_that_lines_paste_not_the_next_one() {
    let mut harness = Harness::new();
    let (first, second) = (long("aaa"), long("bbb"));
    assert_eq!(placeholder(&first), placeholder(&second));
    handle_paste(&mut harness.app, first);
    harness.press(KeyCode::Char('j'), KeyModifiers::CONTROL);
    handle_paste(&mut harness.app, second.clone());
    harness.app.chat_input.cursor = placeholder(&second).len();

    harness.press(
        KeyCode::Char('K'),
        KeyModifiers::CONTROL | KeyModifiers::SHIFT,
    );

    assert_eq!(harness.pastes(), [second]);
}

#[test]
fn an_undo_checkpoint_taken_before_a_sync_keeps_the_placeholder() {
    let mut harness = Harness::new();
    let paste = long("row");
    handle_paste(&mut harness.app, paste.clone());
    let input = &mut harness.app.chat_input;
    let before = vibe_rs::edit_history::Snapshot::capture(input);
    input.input.push_str(" dictated");
    input.cursor = input.input.len();

    input.record_edit(before, true, vibe_rs::clock::now());
    assert!(input.restore_edit(false));
    assert!(input.restore_edit(true));

    assert_eq!(input.submitted_text(), format!("{paste} dictated"));
}

#[test]
fn a_rewound_prompt_gets_its_resumed_images_back() {
    let mut app = App::default();
    let images = vec![vibe_rs::server::ImageAttachment {
        source: vibe_rs::server::ImageSource::Inline {
            data: "aW1hZ2U=".into(),
        },
        alias: "image".into(),
        mime_type: "image/png".into(),
    }];
    let text = "compare [Image #3]";
    app.chat_input.load_full_text(text.into());

    vibe_rs::inline_images::restore_placeholders(&mut app, text, &images);

    let attached = app.chat_input.pasted_images.attach(text, Vec::new());
    assert_eq!(attached.len(), 1);
    let vibe_rs::server::ImageSource::File { path } = &attached[0].source else {
        panic!("a restored image is a file");
    };
    assert_eq!(std::fs::read(path).expect("read back"), b"image");
    let spans = app.chat_input.mentions.spans(&app.chat_input.input);
    assert_eq!((spans[0].start, spans[0].end), (8, 18));
    assert_eq!(
        app.chat_input.pasted_images.register("/tmp/a.png", 0),
        "[Image #4]"
    );
    let _ = std::fs::remove_file(path);
}

#[test]
fn a_draft_starting_like_a_mode_prefix_keeps_its_mode_through_recall() {
    let mut harness = Harness::new();
    harness.app.chat_input.history.add("earlier prompt");
    let paste = format!("/var/log/{}", long("row"));
    handle_paste(&mut harness.app, paste.clone());
    harness.press(KeyCode::Home, KeyModifiers::NONE);

    harness.press(KeyCode::Up, KeyModifiers::NONE);
    harness.press(KeyCode::Down, KeyModifiers::NONE);

    assert_eq!(
        harness.app.chat_input.mode,
        vibe_rs::input_modes::InputMode::Prompt
    );
    assert_eq!(harness.app.chat_input.input, placeholder(&paste));
    assert_eq!(harness.pastes(), [paste.as_str()]);
}

#[test]
fn a_recalled_prompt_keeps_the_image_path_its_collapsed_paste_hides() {
    let mut harness = Harness::new();
    let paste = format!("see /tmp/shot.png\n{}", long("row"));
    harness
        .app
        .chat_input
        .collapsed_pastes
        .remember(paste.as_str().into());
    harness.app.chat_input.history.add(&format!("look {paste}"));

    harness.press(KeyCode::Up, KeyModifiers::NONE);

    assert_eq!(
        harness.app.chat_input.input,
        format!("look {}", placeholder(&paste))
    );
    assert_eq!(
        harness.app.chat_input.submitted_text(),
        format!("look {paste}")
    );
}

#[test]
fn a_recalled_prompt_marks_its_known_image_placeholders() {
    let mut harness = Harness::new();
    let label = harness
        .app
        .chat_input
        .pasted_images
        .register("/tmp/a.png", 0);
    harness.app.chat_input.history.add(&format!("see {label}"));

    harness.press(KeyCode::Up, KeyModifiers::NONE);

    let input = &harness.app.chat_input;
    let spans = input.mentions.spans(&input.input);
    assert_eq!((spans[0].start, spans[0].end), (4, 4 + label.len()));
}

#[test]
fn a_large_paste_stays_undoable() {
    let mut harness = Harness::new();
    let paste = "x".repeat(2_200_000);
    handle_paste(&mut harness.app, paste.clone());
    harness.press(KeyCode::Char('y'), KeyModifiers::NONE);

    harness.press(KeyCode::Char('z'), KeyModifiers::SUPER);

    assert_eq!(harness.app.chat_input.input, placeholder(&paste));
    assert_eq!(harness.pastes(), [paste.as_str()]);
}

#[test]
fn a_rewound_placeholder_names_the_rewound_image_not_an_older_paste() {
    let mut app = App::default();
    let label = app.chat_input.pasted_images.register("/tmp/older.png", 0);
    let images = vec![vibe_rs::server::ImageAttachment {
        source: vibe_rs::server::ImageSource::File {
            path: "/tmp/rewound.png".into(),
        },
        alias: "image".into(),
        mime_type: "image/png".into(),
    }];
    let text = format!("compare {label}");
    app.chat_input.load_full_text(text.clone());

    vibe_rs::inline_images::restore_placeholders(&mut app, &text, &images);

    let attached = app.chat_input.pasted_images.attach(&text, Vec::new());
    assert_eq!(
        attached[0].source,
        vibe_rs::server::ImageSource::File {
            path: "/tmp/rewound.png".into()
        }
    );
}

fn recalled(paste: &str, entry: &str) -> Harness {
    let mut harness = Harness::new();
    let input = &mut harness.app.chat_input;
    input.collapsed_pastes.remember(paste.into());
    input.history.add(entry);
    harness.press(KeyCode::Up, KeyModifiers::NONE);
    harness
}

#[test]
fn a_recalled_prompt_keeps_its_image_paths_as_sent() {
    let paste = long("row");
    let label = placeholder(&paste);

    let prompt = recalled(
        &paste,
        &format!("compare /tmp/a.png{paste} and /tmp/b.png then"),
    );
    assert_eq!(
        prompt.app.chat_input.input,
        format!("compare /tmp/a.png{label} and /tmp/b.png then")
    );
    assert_eq!(prompt.pastes(), [paste.as_str()]);

    let shell = recalled(&paste, &format!("!cat /tmp/c.png {paste}"));
    assert_eq!(
        shell.app.chat_input.input,
        format!("cat /tmp/c.png {label}")
    );
}

#[test]
fn moving_within_history_keeps_the_draft_for_when_recall_ends() {
    let mut harness = Harness::new();
    harness.app.completion.skills = vec![vibe_rs::skill::Skill::new("lint", "Run the linters.")];
    harness.app.chat_input.history.add("first");
    harness.app.chat_input.history.add("second");
    for character in "run /li".chars() {
        harness.press(KeyCode::Char(character), KeyModifiers::NONE);
    }
    harness.press(KeyCode::Tab, KeyModifiers::NONE);
    harness.press(KeyCode::Home, KeyModifiers::NONE);

    harness.press(KeyCode::Up, KeyModifiers::NONE);
    harness.press(KeyCode::Up, KeyModifiers::NONE);
    assert_eq!(harness.app.chat_input.input, "first");
    harness.press(KeyCode::Down, KeyModifiers::NONE);
    assert_eq!(harness.app.chat_input.input, "second");
    harness.press(KeyCode::Down, KeyModifiers::NONE);

    let input = &harness.app.chat_input;
    assert_eq!(input.input, "run /lint ");
    let spans = input.mentions.spans(&input.input);
    assert_eq!((spans[0].start, spans[0].end), (4, 9));
}
