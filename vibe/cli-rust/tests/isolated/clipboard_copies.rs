//! Copy keys and copy notices against a recording clipboard sink.

use std::cell::{Cell, RefCell};
use std::sync::{Arc, Once};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::clipboard::Clipboard;
use vibe_rs::input::handle_priority_key;
use vibe_rs::server::Client;
use vibe_rs::ui::notice;

thread_local! {
    static COPIED: RefCell<Vec<String>> = const { RefCell::new(Vec::new()) };
    static VERIFIED: Cell<bool> = const { Cell::new(false) };
}

struct ThreadRecordingClipboard;

impl Clipboard for ThreadRecordingClipboard {
    fn copy(&self, text: &str) -> bool {
        COPIED.with(|copied| copied.borrow_mut().push(text.to_owned()));
        VERIFIED.with(Cell::get)
    }
}

fn recording(verified: bool) {
    static INSTALL: Once = Once::new();
    INSTALL.call_once(|| vibe_rs::clipboard::set_sink(Arc::new(ThreadRecordingClipboard)));
    VERIFIED.with(|cell| cell.set(verified));
}

fn copied() -> Vec<String> {
    COPIED.with(|copied| copied.borrow().clone())
}

fn notice_text(app: &App) -> &str {
    app.overlays
        .notice
        .as_ref()
        .map_or("", |notice| notice.text.as_str())
}

#[test]
fn ctrl_shift_c_copies_the_composer_selection_in_either_letter_case() {
    recording(false);
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.chat_input.input = "copy me".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();
    app.chat_input.anchor = Some(0);

    for letter in ['c', 'C'] {
        let key = KeyEvent::new(
            KeyCode::Char(letter),
            KeyModifiers::CONTROL | KeyModifiers::SHIFT,
        );
        assert_eq!(handle_priority_key(&mut app, &client, key), Some(false));
    }

    assert_eq!(copied(), ["copy me", "copy me"]);
    assert_eq!(app.chat_input.input, "copy me");
}

#[test]
fn a_verified_copy_shows_the_plain_notice_and_an_unverified_one_adds_the_hint() {
    let mut app = App::default();
    recording(true);
    notice::copied(&mut app, "selected text");
    assert_eq!(notice_text(&app), "Copied to clipboard");

    recording(false);
    notice::copied(&mut app, "selected text");
    assert!(
        notice_text(&app).starts_with("Copied · if paste fails, "),
        "{}",
        notice_text(&app)
    );
    assert_eq!(copied(), ["selected text", "selected text"]);
}
