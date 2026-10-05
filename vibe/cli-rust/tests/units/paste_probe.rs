//! Pasted path lists with non-image paths: the existence probe and its in-place upgrade.

use std::fs;

use vibe_rs::app::App;
use vibe_rs::input;
use vibe_rs::paste_image::{apply_event, Event};
use vibe_rs::paste_path::rewrite_bare_image_paths_in_text;

#[test]
fn probed_pastes_upgrade_the_raw_text_in_place() {
    let mut app = App::default();
    input::handle_paste(&mut app, "/tmp/doc.pdf\n/tmp/a.png\n".to_owned());
    for ch in "ok".chars() {
        app.chat_input.input.insert(app.chat_input.cursor, ch);
        app.chat_input.cursor += 1;
    }
    assert_eq!(app.chat_input.input, "/tmp/doc.pdf\n@/tmp/a.png\nok");

    apply_event(
        &mut app,
        Event::Probed {
            start: 0,
            raw: "/tmp/doc.pdf\n@/tmp/a.png\n".to_owned(),
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
    let mut app = App::default();
    app.paste_image.tx = Some(tx);
    app.chat_input.input = "see:".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();

    input::handle_paste(&mut app, format!("{a} {notes} {b}"));
    assert_eq!(app.chat_input.input, format!("see:{a} {notes} @{b}"));
    let typed = format!("{} ok", app.chat_input.input);
    app.chat_input.input = rewrite_bare_image_paths_in_text(&typed);
    let event = rx.recv().await.expect("probe result");
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
    let mut app = App::default();
    app.paste_image.tx = Some(tx);

    input::handle_paste(&mut app, paths.join("\n"));
    assert!(!app.chat_input.input.contains("[Pasted"));
    let event = rx.recv().await.expect("probe result");
    apply_event(&mut app, event);

    let mentions: Vec<String> = paths.iter().map(|path| format!("@{path}")).collect();
    assert_eq!(app.chat_input.input, format!("{} ", mentions.join(" ")));
}
