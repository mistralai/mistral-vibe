//! Left opens the Sessions page only on an empty, idle composer.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::sessions_page::left_opens;

fn left() -> KeyEvent {
    KeyEvent::from(KeyCode::Left)
}

#[test]
fn left_on_an_empty_composer_opens_the_page() {
    assert!(left_opens(&App::default(), &left()));
}

#[test]
fn left_with_text_or_modifiers_moves_the_caret() {
    let mut app = App::default();
    app.chat_input.input = "draft".into();
    assert!(!left_opens(&app, &left()));
    let shifted = KeyEvent::new(KeyCode::Left, KeyModifiers::SHIFT);
    assert!(!left_opens(&App::default(), &shifted));
}

#[test]
fn left_in_a_slash_or_shell_mode_stays_in_the_composer() {
    let mut app = App::default();
    app.chat_input.mode = vibe_rs::input_modes::InputMode::Slash;
    assert!(!left_opens(&app, &left()));
}

#[test]
fn left_in_vim_normal_mode_stays_a_motion() {
    let mut app = App::default();
    app.vim.enabled = true;
    app.vim.mode = vibe_rs::vim::Mode::Normal;
    assert!(!left_opens(&app, &left()));
    app.vim.mode = vibe_rs::vim::Mode::Insert;
    assert!(left_opens(&app, &left()));
}

#[test]
fn left_while_another_surface_has_focus_does_nothing() {
    let mut app = App::default();
    app.sessions_page.open = true;
    assert!(!left_opens(&app, &left()));
}
