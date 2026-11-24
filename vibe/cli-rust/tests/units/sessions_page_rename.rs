//! The rename modal wraps a long title, puts the caret where a click lands, and saves a trimmed title.

use crossterm::event::KeyCode;
use vibe_rs::app::{App, Status};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, keys, Trigger};

use crate::support::stub;

const LONG: &str = "Docs refresh for the new resume flag and the Sessions page keys, with examples";

fn page() -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    app.sessions_page.loading = false;
    app.sessions_page.sessions = vec![Session {
        id: "s1".into(),
        title: LONG.into(),
        cwd: Some("/w".into()),
        ..Session::default()
    }];
    sessions_page::folders::resized(&mut app, 120);
    sessions_page::relayout(&mut app);
    app
}

fn screen(app: &mut App) -> Vec<String> {
    crate::support::rows(&crate::support::render(120, 40, |frame| app.draw(frame)))
}

#[test]
fn a_long_title_wraps_and_a_click_puts_the_caret_under_the_pointer() {
    let mut app = page();
    keys::handle_key(&mut app, &stub(), crate::support::key(KeyCode::Char('r')));
    let rows = screen(&mut app);
    let y = rows
        .iter()
        .position(|row| row.contains("▎ with examples"))
        .expect("the title wraps onto a second row");
    let x = crate::support::column(&rows[y], "with examples");
    crate::support::click(&mut app, x, y as u16);
    let rename = app
        .sessions_page
        .rename
        .as_ref()
        .expect("the modal stays open");
    assert_eq!(&rename.text[rename.cursor..], "with examples");
}

#[test]
fn newline_keys_insert_nothing_in_a_title() {
    let mut app = page();
    keys::handle_key(&mut app, &stub(), crate::support::key(KeyCode::Char('r')));
    let ctrl_j = crossterm::event::KeyEvent::new(
        KeyCode::Char('j'),
        crossterm::event::KeyModifiers::CONTROL,
    );
    keys::handle_key(&mut app, &stub(), ctrl_j);
    let rename = app.sessions_page.rename.as_ref().expect("open");
    assert_eq!(rename.text, LONG);
}
