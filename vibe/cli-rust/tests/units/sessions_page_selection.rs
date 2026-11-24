//! Selecting text on the Sessions page: a drag keeps to the list or the
//! preview it started in, and a copy reads a row without its marks.

use vibe_rs::app::{App, Status};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, Trigger};

use crate::support::{column, down, drag, render, rows, send_mouse, stub, up};

const WIDTH: u16 = 120;
const HEIGHT: u16 = 30;
const FOLDER: &str = "/projects/app-old/packages/very-long-workspace-name/vibe";
const REPLY: &str = "The working directory moved to the feature worktree, so the next turn \
                     runs there and the tools see its files.\n\nNothing else changed.";

fn session(index: u64, title: &str, cwd: &str) -> Session {
    Session {
        id: format!("session-{index}"),
        title: title.into(),
        cwd: Some(cwd.into()),
        repository_root: Some("/projects/app".into()),
        created_at: 100 - index,
        updated_at: 100 - index,
        bumped_at: Some(100 - index),
        ..Session::default()
    }
}

fn page() -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some("/projects/app".into());
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    app.sessions_page.loading = false;
    app.sessions_page.sessions = vec![
        session(1, "Fix the login flow", FOLDER),
        session(2, "Docs refresh", "/projects/app"),
    ];
    sessions_page::relayout(&mut app);
    let state = serde_json::from_value(serde_json::json!({
        "eventId": 1,
        "session": { "id": "session-1" },
        "history": [{
            "id": "reply",
            "type": "message",
            "role": "assistant",
            "generationStatus": "completed",
            "content": [{ "type": "text", "text": REPLY }],
        }],
    }))
    .expect("state");
    sessions_page::preview::store(&mut app, "session-1".into(), 99, Some(&state));
    app
}

fn screen(app: &mut App) -> Vec<String> {
    rows(&render(WIDTH, HEIGHT, |frame| app.draw(frame)))
}

/// The first cell of `text` on screen, from column `from` on.
fn find(app: &mut App, text: &str, from: usize) -> (u16, u16) {
    find_in(app, text, from, usize::MAX)
}

/// The first cell of `text` between columns `from` and `to`.
fn find_in(app: &mut App, text: &str, from: usize, to: usize) -> (u16, u16) {
    let screen = screen(app);
    for (y, row) in screen.iter().enumerate() {
        let tail: String = row.chars().take(to).skip(from).collect();
        if tail.contains(text) {
            return (from as u16 + column(&tail, text), y as u16);
        }
    }
    panic!("`{text}` is not on screen:\n{}", screen.join("\n"));
}

/// Drag from `start` to `end`, then paint: the text the selection copies.
fn select(app: &mut App, start: (u16, u16), end: (u16, u16)) -> String {
    screen(app);
    send_mouse(app, down(start.0, start.1));
    send_mouse(app, drag(end.0, end.1));
    send_mouse(app, up(end.0, end.1));
    screen(app);
    app.selection
        .region
        .as_ref()
        .map(|selection| selection.text.clone())
        .unwrap_or_default()
}

/// The preview starts right of the last rule.
fn pane_column(app: &mut App) -> usize {
    let screen = screen(app);
    let row = screen
        .iter()
        .find(|row| row.contains('│'))
        .expect("the rule");
    let chars: Vec<char> = row.chars().collect();
    chars.iter().rposition(|&ch| ch == '│').expect("the rule") + 1
}

#[test]
fn a_drag_in_the_preview_never_reaches_into_the_list() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let start = find(&mut app, "The working", pane);
    let text = select(&mut app, start, (0, start.1 + 1));
    assert!(text.starts_with("The working"), "{text:?}");
    assert!(!text.contains("Fix the login"), "{text:?}");
    assert!(!text.contains('│'), "{text:?}");
}

#[test]
fn a_drag_over_rows_copies_their_text_without_the_marks() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let start = find_in(&mut app, "Fix the login", 0, pane);
    let end = find_in(&mut app, "Docs refresh", 0, pane);
    let text = select(&mut app, start, (end.0 + 11, end.1));
    assert!(text.starts_with("Fix the login flow"), "{text:?}");
    // The row reads as one line: title, then its time.
    assert!(
        text.lines()
            .next()
            .is_some_and(|line| line.ends_with("ago")),
        "{text:?}"
    );
    assert!(
        text.lines()
            .nth(1)
            .is_some_and(|line| line.starts_with("Docs refresh")),
        "{text:?}"
    );
    assert!(
        !text.contains('›') && !text.contains('○') && !text.contains('●'),
        "{text:?}"
    );
    // A drag is no click: the highlight stays where it was.
    assert_eq!(
        sessions_page::selected(&app).map(|session| session.id.as_str()),
        Some("session-1")
    );
}

#[test]
fn a_click_on_a_row_still_picks_it() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let (x, y) = find_in(&mut app, "Docs refresh", 0, pane);
    send_mouse(&mut app, down(x, y));
    send_mouse(&mut app, up(x, y));
    assert_eq!(
        sessions_page::selected(&app).map(|session| session.id.as_str()),
        Some("session-2")
    );
    assert!(app.selection.region.is_none());
}

#[test]
fn the_copy_key_copies_the_selection_and_leaves_the_page_open() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let start = find(&mut app, "The working", pane);
    select(&mut app, start, (start.0 + 10, start.1));
    let copy = crossterm::event::KeyEvent::new(
        crossterm::event::KeyCode::Char('c'),
        crossterm::event::KeyModifiers::SUPER,
    );
    crate::support::send_key(&mut app, copy);
    assert!(app.sessions_page.open);
}

#[test]
fn a_key_on_the_page_drops_the_selection() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let start = find(&mut app, "The working", pane);
    select(&mut app, start, (start.0 + 10, start.1));
    assert!(app.selection.region.is_some());
    let j = crate::support::key(crossterm::event::KeyCode::Char('j'));
    sessions_page::keys::handle_key(&mut app, &stub(), j);
    assert!(app.selection.region.is_none());
}

/// The wrapped rows of a reply rejoin into its sentence and its own blank
/// line stays, as a copy from the conversation reads it.
#[test]
fn a_wrapped_preview_reply_copies_as_its_own_paragraphs() {
    let mut app = page();
    let pane = pane_column(&mut app);
    let start = find(&mut app, "The working", pane);
    let end = find(&mut app, "Nothing else", pane);
    let text = select(&mut app, start, (end.0 + 20, end.1));
    assert_eq!(text, REPLY);
}
