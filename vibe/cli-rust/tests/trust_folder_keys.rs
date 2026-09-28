//! Trust-folder keyboard navigation behavior.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Rect;

use vibe_rs::app::{App, Selection};
use vibe_rs::selection::ScrollTarget;
use vibe_rs::trust_folders::{self, TRUST_CWD};

#[test]
fn up_and_down_scroll_the_detected_files() {
    let mut app = App::default();
    app.trust.scroll_max = 1;

    trust_folders::handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
    assert_eq!(app.trust.scroll, 1);

    trust_folders::handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
    assert_eq!(app.trust.scroll, 0);
}

#[test]
fn copy_shortcuts_do_not_quit_the_trust_gate() {
    let mut app = App::default();

    assert!(!trust_folders::handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::SUPER),
    ));
    assert!(!trust_folders::handle_key(
        &mut app,
        KeyEvent::new(
            KeyCode::Char('c'),
            KeyModifiers::CONTROL | KeyModifiers::SHIFT,
        ),
    ));
    assert!(!trust_folders::handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('y'), KeyModifiers::CONTROL),
    ));
    assert!(trust_folders::handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
    ));
}

#[test]
fn deciding_clears_the_trust_selection() {
    let mut app = App::default();
    app.trust.open = true;
    app.trust.options = vec![(TRUST_CWD, "Trust folder".to_owned())];
    app.selection.region = Some(Selection {
        owner: vibe_rs::selection::RegionId::Main,
        anchor: (10, 1),
        head: (20, 2),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Trust,
        table_cell: None,
        text: "selected".to_owned(),
    });
    app.view.selection_region.area = Rect::new(0, 0, 40, 10);

    assert!(!trust_folders::handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
    ));
    assert!(app.selection.region.is_none());
    assert!(!app.trust.open);
}
