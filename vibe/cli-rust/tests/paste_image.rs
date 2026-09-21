//! Clipboard images use Codex-style persistent temporary files and Python-style tokens.

use std::fs;
use std::path::Path;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, Status};
use vibe_rs::paste_image::{
    apply_event, insert_image_token, is_paste_image_key, write_clipboard_image, Event, PNG_MAGIC,
};

#[test]
fn writes_a_unique_persistent_temp_png() {
    let path = write_clipboard_image(&[PNG_MAGIC, b"payload"].concat()).expect("write image");

    assert!(path.is_file());
    assert!(path
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with("vibe-clipboard-") && name.ends_with(".png")));

    fs::remove_file(path).expect("remove image fixture");
}

#[test]
fn inserts_a_quoted_token_at_the_current_cursor() {
    let mut input = "look here".to_owned();
    let mut cursor = 4;
    let mut anchor = None;

    insert_image_token(
        &mut input,
        &mut cursor,
        &mut anchor,
        Path::new("/tmp/image with spaces.png"),
    );

    assert_eq!(input, "look @'/tmp/image with spaces.png' here");
    assert_eq!(cursor, "look @'/tmp/image with spaces.png'".len());
    assert_eq!(anchor, None);
}

#[test]
fn image_token_replaces_the_active_selection() {
    let mut input = "replace me".to_owned();
    let mut cursor = input.len();
    let mut anchor = Some(0);

    insert_image_token(
        &mut input,
        &mut cursor,
        &mut anchor,
        Path::new("/tmp/image.png"),
    );

    assert_eq!(input, "@/tmp/image.png ");
    assert_eq!(cursor, input.len());
    assert_eq!(anchor, None);
}

#[test]
fn clipboard_result_uses_the_current_model_capability() {
    let event = || Event::Pasted {
        path: "/tmp/image.png".into(),
        size: 42,
    };
    let mut supported = App::default();
    supported.session.status = Status::Ready;
    supported
        .session
        .startup_config
        .active_model_supports_images = true;

    apply_event(&mut supported, event());

    assert_eq!(supported.chat_input.input, "@/tmp/image.png ");

    let mut unsupported = App::default();
    unsupported.session.status = Status::Ready;
    unsupported.session.startup_config.active_model_display_name = "Text only".to_owned();

    apply_event(&mut unsupported, event());

    assert!(unsupported.chat_input.input.is_empty());

    let mut starting = App::default();
    apply_event(&mut starting, event());

    assert_eq!(starting.chat_input.input, "@/tmp/image.png ");
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
