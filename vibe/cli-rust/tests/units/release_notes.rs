//! The `/release-notes` pager: opens over the full history, searches, scrolls within bounds, and closes on Esc.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::focus::Focus;
use vibe_rs::release_notes;

fn press(app: &mut App, code: KeyCode) {
    release_notes::handle_key(app, KeyEvent::new(code, KeyModifiers::NONE));
}

fn opened() -> App {
    let mut app = App::default();
    release_notes::open(&mut app);
    app.release_notes.page = 10;
    app.release_notes.total = 25;
    app
}

#[test]
fn opening_takes_the_focus_with_every_release() {
    let mut app = opened();
    assert_eq!(app.focus(), Focus::ReleaseNotes);
    let text: String = app
        .release_notes
        .lines(80)
        .iter()
        .map(|line| line.to_string())
        .collect();
    let newest = &vibe_rs::whats_new::entries()[0].version;
    assert!(text.contains(&format!("v{newest}")));
    assert!(text.contains("v2.0.0"));
}

#[test]
fn keys_scroll_within_the_document() {
    let mut app = opened();
    press(&mut app, KeyCode::Up);
    assert_eq!(app.release_notes.scroll, 0);
    press(&mut app, KeyCode::Char('j'));
    press(&mut app, KeyCode::Down);
    assert_eq!(app.release_notes.scroll, 2);
    press(&mut app, KeyCode::PageDown);
    assert_eq!(app.release_notes.scroll, 12);
    press(&mut app, KeyCode::End);
    assert_eq!(app.release_notes.scroll, 15);
    press(&mut app, KeyCode::PageDown);
    assert_eq!(app.release_notes.scroll, 15);
    press(&mut app, KeyCode::Home);
    assert_eq!(app.release_notes.scroll, 0);
    release_notes::wheel(&mut app, true);
    assert_eq!(app.release_notes.scroll, 2);
}

#[test]
fn typing_a_search_filters_and_returns_to_the_top() {
    let mut app = opened();
    press(&mut app, KeyCode::End);
    press(&mut app, KeyCode::Char('/'));
    for ch in "teleport".chars() {
        press(&mut app, KeyCode::Char(ch));
    }
    assert_eq!(app.release_notes.search.query, "teleport");
    assert_eq!(app.release_notes.scroll, 0);
    let text: String = app
        .release_notes
        .lines(80)
        .iter()
        .map(|line| line.to_string() + "\n")
        .collect();
    assert!(text.to_lowercase().contains("teleport"));
    assert!(!text.contains("v2.0.0"));
}

#[test]
fn esc_leaves_the_search_then_clears_it_then_closes() {
    let mut app = opened();
    press(&mut app, KeyCode::Char('/'));
    press(&mut app, KeyCode::Char('x'));
    press(&mut app, KeyCode::Esc);
    assert!(!app.release_notes.search.focused);
    assert_eq!(app.release_notes.search.query, "x");
    press(&mut app, KeyCode::Esc);
    assert!(app.release_notes.search.query.is_empty());
    assert!(app.release_notes.open);
    press(&mut app, KeyCode::Esc);
    assert!(!app.release_notes.open);
    assert_eq!(app.focus(), Focus::Composer);
}
