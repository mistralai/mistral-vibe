//! Where the highlight lands when the list changes under it, and that the
//! preview follows: an archive, a reload, a fold.

use crossterm::event::{KeyCode, KeyEvent};
use vibe_rs::app::App;
use vibe_rs::sessions_page::actions::{self, Event};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, keys};

use crate::support::stub;

fn session(id: &str, activity: u64) -> Session {
    Session {
        id: id.into(),
        title: id.into(),
        cwd: Some("/work/app".into()),
        repository_root: Some("/work/app".into()),
        created_at: activity,
        updated_at: activity,
        bumped_at: Some(activity),
        ..Default::default()
    }
}

/// A page over `sessions`, listed in the order given (newest first).
fn page(sessions: Vec<Session>) -> App {
    let mut app = App::default();
    app.sessions_page.open = true;
    app.sessions_page.sessions = sessions;
    sessions_page::relayout(&mut app);
    app
}

fn highlighted(app: &App) -> Option<String> {
    sessions_page::highlighted_id(app)
}

fn press(app: &mut App, code: KeyCode) {
    keys::handle_key(app, &stub(), KeyEvent::from(code));
}

fn archived(id: &str) -> Event {
    Event::Archived {
        id: id.into(),
        answer: Ok(Some(1)),
    }
}

#[tokio::test]
async fn archiving_a_row_keeps_the_highlight_on_the_list() {
    let mut app = page(vec![session("s1", 30), session("s2", 20)]);
    press(&mut app, KeyCode::Down);
    actions::apply_event(&mut app, &stub(), archived("s2"));
    assert_eq!(highlighted(&app).as_deref(), Some("s1"));
}

#[tokio::test]
async fn a_reload_keeps_the_highlighted_session() {
    let mut app = page(vec![
        session("s1", 30),
        session("s2", 20),
        session("s3", 10),
    ]);
    press(&mut app, KeyCode::Down);
    assert_eq!(highlighted(&app).as_deref(), Some("s2"));
    // A reload brings one more row on top.
    app.sessions_page.reloading = true;
    let generation = app.sessions_page.generation;
    let sessions = vec![
        session("new", 40),
        session("s1", 30),
        session("s2", 20),
        session("s3", 10),
    ];
    let landed = sessions_page::Event::Page {
        generation,
        sessions,
        next: None,
    };
    sessions_page::apply_event(&mut app, &stub(), landed);
    assert_eq!(highlighted(&app).as_deref(), Some("s2"));
}

#[tokio::test]
async fn folding_a_family_from_a_child_highlights_its_top() {
    let mut sessions = vec![
        session("top", 40),
        session("c1", 30),
        session("c2", 20),
        session("other", 10),
    ];
    sessions[1].started_by_session_id = Some("top".into());
    sessions[2].started_by_session_id = Some("top".into());
    let mut app = page(sessions);
    // Families start folded: Space unfolds the top's, then highlight c2.
    press(&mut app, KeyCode::Char(' '));
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Down);
    assert_eq!(highlighted(&app).as_deref(), Some("c2"));
    press(&mut app, KeyCode::Char(' '));
    assert_eq!(highlighted(&app).as_deref(), Some("top"));
}

#[tokio::test]
async fn a_highlight_moved_without_a_key_asks_for_its_preview() {
    let mut app = page(vec![session("s1", 30), session("s2", 20)]);
    press(&mut app, KeyCode::Down);
    app.sessions_page.preview.at = None;
    actions::apply_event(&mut app, &stub(), archived("s2"));
    assert_eq!(highlighted(&app).as_deref(), Some("s1"));
    assert!(
        app.sessions_page.preview.at.is_some(),
        "s1's preview is asked for"
    );
}

#[tokio::test]
async fn a_relayout_on_the_same_highlight_keeps_its_preview_deadline() {
    let mut app = page(vec![session("s1", 30), session("s2", 20)]);
    let deadline = app.sessions_page.preview.at;
    assert!(
        deadline.is_some(),
        "the first highlight asks for its preview"
    );
    // A landed page rebuilds the list while the highlight stays on s1.
    app.sessions_page.sessions.push(session("s3", 10));
    sessions_page::relayout(&mut app);
    assert_eq!(highlighted(&app).as_deref(), Some("s1"));
    assert_eq!(app.sessions_page.preview.at, deadline);
}
