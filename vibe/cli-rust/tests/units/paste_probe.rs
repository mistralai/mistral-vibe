//! Pasted path lists with non-image paths: the existence probe and its in-place upgrade.

use std::fs;

use vibe_rs::app::App;
use vibe_rs::input;
use vibe_rs::paste_image::{apply_event, Event};
use vibe_rs::ui_event::UiEvent;

#[test]
fn probed_pastes_upgrade_the_raw_text_in_place() {
    let mut app = App::default();
    input::handle_paste(&mut app, "/tmp/doc.pdf\n/tmp/a.png\n".to_owned());
    for ch in "ok".chars() {
        app.chat_input.input.insert(app.chat_input.cursor, ch);
        app.chat_input.cursor += 1;
    }
    assert_eq!(app.chat_input.input, "/tmp/doc.pdf\n/tmp/a.png\nok");

    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: "/tmp/doc.pdf\n/tmp/a.png\n".to_owned(),
            paths: Some(vec!["/tmp/doc.pdf".to_owned(), "/tmp/a.png".to_owned()]),
        },
    );

    assert_eq!(app.chat_input.input, "@/tmp/doc.pdf [Image #1] ok");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
}

#[test]
fn probed_pastes_leave_edited_or_unresolved_text_alone() {
    let mut app = App::default();
    app.chat_input.input = "/help".to_owned();

    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: "/help".to_owned(),
            paths: None,
        },
    );
    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: "/tmp/gone.pdf".to_owned(),
            paths: Some(vec!["/tmp/gone.pdf".to_owned()]),
        },
    );

    assert_eq!(app.chat_input.input, "/help");
}

#[test]
fn probed_pastes_replace_only_their_own_standalone_text() {
    let probed = |start| Event::Probed {
        start,
        raw: "/p".to_owned(),
        paths: Some(vec!["/p".to_owned()]),
    };
    let mut app = App::default();
    app.chat_input.input = "/p/a.txt /p".to_owned();

    apply_event(&mut app, probed(9));
    assert_eq!(app.chat_input.input, "/p/a.txt @/p ");

    app.chat_input.input = "x /p/a.txt /p".to_owned();
    apply_event(&mut app, probed(0));
    assert_eq!(app.chat_input.input, "x /p/a.txt @/p ");
}

#[test]
fn pasted_path_lists_keep_the_neighbouring_text() {
    let mut app = App::default();
    app.chat_input.input = "/x".to_owned();
    app.chat_input.cursor = 0;

    input::handle_paste(&mut app, "/a.txt".to_owned());
    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: "/a.txt".to_owned(),
            paths: Some(vec!["/a.txt".to_owned()]),
        },
    );

    assert_eq!(app.chat_input.input, "@/a.txt /x");
}

#[tokio::test(flavor = "current_thread")]
async fn probed_mixed_lists_survive_keystrokes_and_glued_words() {
    let dir = tempfile::tempdir().expect("create test directory");
    let [a, notes, b] = ["a.png", "notes.md", "b.png"].map(|name| {
        let path = dir.path().join(name);
        fs::write(&path, b"fixture").expect("write fixture");
        path.display().to_string()
    });
    let (tx, mut rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };
    app.chat_input.input = "see:".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();

    input::handle_paste(&mut app, format!("{a} {notes} {b}"));
    assert_eq!(app.chat_input.input, format!("see:{a} {notes} {b}"));
    app.chat_input.input = format!("{} ok", app.chat_input.input);
    let Some(UiEvent::PasteImage(event)) = rx.recv().await else {
        panic!("probe result");
    };
    apply_event(&mut app, event);

    assert_eq!(
        app.chat_input.input,
        format!("see: [Image #1] @{notes} [Image #2] ok")
    );
}

#[tokio::test(flavor = "current_thread")]
async fn tall_mixed_lists_skip_collapsing_and_become_mentions() {
    let dir = tempfile::tempdir().expect("create test directory");
    let paths: Vec<String> = (0..12)
        .map(|index| {
            let path = dir.path().join(format!("note{index}.md"));
            fs::write(&path, b"notes").expect("write fixture");
            path.display().to_string()
        })
        .collect();
    let (tx, mut rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };

    input::handle_paste(&mut app, paths.join("\n"));
    assert!(!app.chat_input.input.contains("[Pasted"));
    let Some(UiEvent::PasteImage(event)) = rx.recv().await else {
        panic!("probe result");
    };
    apply_event(&mut app, event);

    let mentions: Vec<String> = paths.iter().map(|path| format!("@{path}")).collect();
    assert_eq!(app.chat_input.input, format!("{} ", mentions.join(" ")));
}

async fn probed(pasted: &str, edit: impl FnOnce(&mut App)) -> App {
    let dir = tempfile::tempdir().expect("create test directory");
    let notes = dir.path().join("notes.md");
    fs::write(&notes, b"notes").expect("write fixture");
    let pasted = pasted.replace("NOTES", &notes.display().to_string());
    let (tx, mut rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };
    input::handle_paste(&mut app, pasted.clone());
    edit(&mut app);
    let Some(UiEvent::PasteImage(event)) = rx.recv().await else {
        panic!("probe result");
    };
    apply_event(&mut app, event);
    input::handle_paste(&mut app, pasted);
    app
}

#[tokio::test(flavor = "current_thread")]
async fn pasting_a_probed_list_again_keeps_it_as_text() {
    let app = probed("NOTES /tmp/a.png", |_| {}).await;

    assert!(app.chat_input.input.ends_with("notes.md /tmp/a.png"));
    assert!(!app.chat_input.input.contains('@'));
}

#[tokio::test(flavor = "current_thread")]
async fn an_edit_before_the_probe_answers_ends_the_offer() {
    let app = probed("NOTES /tmp/a.png", |app| {
        vibe_rs::long_paste::dismiss(app);
    })
    .await;

    let input = &app.chat_input.input;
    assert!(input.starts_with('@'));
    assert!(input.contains("notes.md [Image #1] /"));
    assert!(input.ends_with("notes.md /tmp/a.png"));
}

#[tokio::test(flavor = "current_thread")]
async fn a_second_paste_before_the_probe_answers_adds_a_copy() {
    let (tx, _rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };
    let pasted = "/tmp/doc.pdf /tmp/a.png";
    input::handle_paste(&mut app, pasted.to_owned());

    input::handle_paste(&mut app, pasted.to_owned());

    assert_eq!(app.chat_input.input, format!("{pasted}{pasted}"));
}

#[tokio::test(flavor = "current_thread")]
async fn a_failed_probe_leaves_no_offer() {
    let (tx, _rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };
    let pasted = "/tmp/gone.pdf\n";
    input::handle_paste(&mut app, pasted.to_owned());
    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: pasted.to_owned(),
            paths: None,
        },
    );

    assert!(app.chat_input.expandable_paste.is_none());
    input::handle_paste(&mut app, pasted.to_owned());
    assert_eq!(app.chat_input.input, format!("{pasted}{pasted}"));
}

#[tokio::test(flavor = "current_thread")]
async fn a_probe_answering_before_the_paste_again_keeps_the_offer_on_its_placeholder() {
    let (tx, _rx) = tokio::sync::mpsc::channel(8);
    let mut app = App {
        events: Some(tx),
        ..Default::default()
    };
    let list = "/tmp/doc.pdf /tmp/notes.md";
    input::handle_paste(&mut app, list.to_owned());
    input::handle_paste(&mut app, "/tmp/a.png".to_owned());
    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: list.to_owned(),
            paths: Some(vec!["/tmp/doc.pdf".to_owned(), "/tmp/notes.md".to_owned()]),
        },
    );

    input::handle_paste(&mut app, "/tmp/a.png".to_owned());

    assert_eq!(
        app.chat_input.input,
        "@/tmp/doc.pdf @/tmp/notes.md/tmp/a.png"
    );
}
