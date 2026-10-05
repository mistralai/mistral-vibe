//! Clipboard images use Codex-style persistent temporary files and spaced placeholder tokens.

use std::fs;
use std::path::{Path, PathBuf};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, Status};
use vibe_rs::paste_files::{image_files, parse_clipboard_files};
use vibe_rs::paste_image::{
    apply_event, clipboard_image_path, insert_image_token, is_paste_image_key,
    write_clipboard_image, Event, PNG_MAGIC,
};

#[test]
fn writes_python_named_images_under_the_pasted_images_dir() {
    let path = write_clipboard_image(&[PNG_MAGIC, b"payload"].concat()).expect("write image");

    assert!(path.is_file());
    assert_eq!(
        path.parent().and_then(Path::file_name),
        Some("vibe-pasted-images".as_ref())
    );
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("");
    let stamp = name
        .strip_prefix("clipboard-")
        .and_then(|rest| rest.strip_suffix(".png"))
        .unwrap_or("");
    assert_eq!(stamp.get(8..9), Some("T"), "{name}");
    assert!(stamp[..15]
        .chars()
        .enumerate()
        .all(|(index, ch)| index == 8 || ch.is_ascii_digit()));

    fs::remove_file(path).expect("remove image fixture");
}

#[test]
fn same_second_pastes_get_a_numeric_suffix() {
    let dir = Path::new("/tmp/pasted");
    let taken = [
        dir.join("clipboard-20260928T150304.png"),
        dir.join("clipboard-20260928T150304-1.png"),
    ];

    assert_eq!(
        clipboard_image_path(dir, "20260928T150304", |path| taken
            .contains(&path.to_path_buf())),
        dir.join("clipboard-20260928T150304-2.png")
    );
    assert_eq!(
        clipboard_image_path(dir, "20260928T150305", |_| false),
        dir.join("clipboard-20260928T150305.png")
    );
}

#[test]
fn inserts_a_token_at_the_current_cursor() {
    let mut input = "look here".to_owned();
    let mut cursor = 4;
    let mut anchor = None;

    let span = insert_image_token(&mut input, &mut cursor, &mut anchor, "[Image #3]");

    assert_eq!(input, "look [Image #3] here");
    assert_eq!(cursor, "look [Image #3]".len());
    assert_eq!(span, (5, "look [Image #3]".len()));
    assert_eq!(anchor, None);
}

#[test]
fn image_token_replaces_the_active_selection() {
    let mut input = "replace me".to_owned();
    let mut cursor = input.len();
    let mut anchor = Some(0);

    insert_image_token(&mut input, &mut cursor, &mut anchor, "[Image #1]");

    assert_eq!(input, "[Image #1] ");
    assert_eq!(cursor, input.len());
    assert_eq!(anchor, None);
}

#[test]
fn clipboard_result_uses_the_current_model_capability() {
    let event = || Event::Pasted {
        path: "/tmp/image.png".into(),
    };
    let mut supported = App::default();
    supported.session.status = Status::Ready;
    supported.session.startup_config.images_supported = true;

    apply_event(&mut supported, event());

    assert_eq!(supported.chat_input.input, "[Image #1] ");

    let mut unsupported = App::default();
    unsupported.session.status = Status::Ready;
    unsupported.session.startup_config.active_model_display_name = "Text only".to_owned();

    apply_event(&mut unsupported, event());

    assert!(unsupported.chat_input.input.is_empty());

    let mut starting = App::default();
    apply_event(&mut starting, event());

    assert_eq!(starting.chat_input.input, "[Image #1] ");

    let mut shell = App::default();
    shell.chat_input.mode = vibe_rs::input_modes::InputMode::Bash;
    apply_event(&mut shell, event());

    assert_eq!(shell.chat_input.input, "/tmp/image.png ");
}

#[test]
fn ctrl_v_is_image_paste_only_on_supported_platforms() {
    let key = KeyEvent::new(KeyCode::Char('v'), KeyModifiers::CONTROL);

    assert!(is_paste_image_key(&key, true));
    assert!(!is_paste_image_key(&key, false));
    assert!(!is_paste_image_key(
        &KeyEvent::new(KeyCode::Char('v'), KeyModifiers::SUPER),
        true
    ));
    assert!(!is_paste_image_key(
        &KeyEvent::new(
            KeyCode::Char('v'),
            KeyModifiers::CONTROL | KeyModifiers::SHIFT,
        ),
        true
    ));
}

#[test]
fn clipboard_image_files_become_placeholders() {
    let mut app = App::default();
    app.chat_input.input = "see".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();

    apply_event(
        &mut app,
        Event::ImageFiles(vec!["/tmp/a (1).png".into(), "/tmp/b.jpg".into()]),
    );

    assert_eq!(app.chat_input.input, "see [Image #1] [Image #2] ");

    let mut shell = App::default();
    shell.chat_input.mode = vibe_rs::input_modes::InputMode::Bash;
    apply_event(&mut shell, Event::ImageFiles(vec!["/tmp/a.png".into()]));
    assert_eq!(shell.chat_input.input, "/tmp/a.png ");
}

#[test]
fn clipboard_image_files_respect_the_model_capability() {
    let mut app = App::default();
    app.session.status = Status::Ready;

    apply_event(&mut app, Event::ImageFiles(vec!["/tmp/a.png".into()]));

    assert!(app.chat_input.input.is_empty());
}

#[test]
fn clipboard_file_lists_keep_only_images() {
    let files = vec![
        PathBuf::from("/tmp/a.PNG"),
        PathBuf::from("/tmp/doc.pdf"),
        PathBuf::from("/tmp/dir"),
        PathBuf::from("/tmp/b.webp"),
    ];

    assert_eq!(
        image_files(files),
        vec![PathBuf::from("/tmp/a.PNG"), PathBuf::from("/tmp/b.webp")]
    );
    assert!(image_files(vec![PathBuf::from("/tmp/doc.pdf")]).is_empty());
}

#[test]
fn parses_one_clipboard_file_per_line() {
    assert_eq!(
        parse_clipboard_files("/tmp/a (1).png\n/tmp/b.png\n"),
        vec![Path::new("/tmp/a (1).png"), Path::new("/tmp/b.png")]
    );
    assert!(parse_clipboard_files("\n").is_empty());
}
