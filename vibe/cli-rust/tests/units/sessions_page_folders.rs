//! The folders column: the flat list it filters, the keys of each column and the folder kept across opens.

use std::collections::HashSet;

use crossterm::event::{KeyCode, KeyEvent};
use vibe_rs::app::{App, Status};
use vibe_rs::sessions_page::model::{build, Line, Query, Sort};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, keys, Column, Trigger};

use crate::support::stub;

fn session(id: &str, cwd: &str, activity: u64) -> Session {
    Session {
        id: id.into(),
        title: id.into(),
        cwd: Some(cwd.into()),
        repository_root: Some(cwd.into()),
        created_at: activity,
        updated_at: activity,
        bumped_at: Some(activity),
        ..Default::default()
    }
}

fn sessions() -> Vec<Session> {
    vec![
        session("a1", "/w/alpha", 10),
        session("b1", "/w/beta", 30),
        session("a2", "/w/alpha", 20),
    ]
}

fn flat<'a>(toggled: &'a HashSet<String>, group: Option<&'a str>) -> Query<'a> {
    Query {
        sort: Sort::Activity,
        search: "",
        folder: None,
        archived: false,
        gone: false,
        current_id: None,
        current_group: None,
        toggled,
        flat: true,
        group,
    }
}

fn ids(view: &vibe_rs::sessions_page::model::View, sessions: &[Session]) -> Vec<String> {
    view.rows
        .iter()
        .map(|&index| sessions[index].id.clone())
        .collect()
}

#[test]
fn all_lists_every_session_by_activity_without_headers() {
    let sessions = sessions();
    let toggled = HashSet::new();
    let view = build(&sessions, &[], &flat(&toggled, None));
    assert_eq!(ids(&view, &sessions), ["b1", "a2", "a1"]);
    assert!(view
        .lines
        .iter()
        .all(|line| matches!(line, Line::Row { .. })));
    let folders: Vec<_> = view
        .folders
        .iter()
        .map(|folder| (folder.label.as_str(), folder.sessions.len()))
        .collect();
    assert_eq!(folders, [("beta", 1), ("alpha", 2)]);
    assert_eq!(view.folder, None);
}

#[test]
fn a_folder_lists_its_own_sessions_and_an_unknown_one_lists_all() {
    let sessions = sessions();
    let toggled = HashSet::new();
    let view = build(&sessions, &[], &flat(&toggled, Some("/w/alpha")));
    assert_eq!(ids(&view, &sessions), ["a2", "a1"]);
    assert_eq!(view.folder, Some(1));
    let view = build(&sessions, &[], &flat(&toggled, Some("/w/gone")));
    assert_eq!(ids(&view, &sessions), ["b1", "a2", "a1"]);
}

/// A page drawn wide enough for the folders column.
fn page() -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    app.sessions_page.loading = false;
    app.sessions_page.sessions = sessions();
    sessions_page::folders::resized(&mut app, 120);
    app
}

fn press(app: &mut App, code: KeyCode) {
    keys::handle_key(app, &stub(), KeyEvent::from(code));
}

fn highlighted(app: &App) -> Option<String> {
    sessions_page::highlighted_id(app)
}

#[test]
fn left_focuses_the_folders_where_arrows_pick_a_folder_and_right_comes_back() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    assert_eq!(app.sessions_page.browse.focus, Column::Folders);
    press(&mut app, KeyCode::Down);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/beta"));
    assert_eq!(highlighted(&app).as_deref(), Some("b1"));
    press(&mut app, KeyCode::Char('j'));
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/alpha"));
    assert_eq!(highlighted(&app).as_deref(), Some("a2"));
    // Enter in the folders hands the keys back instead of opening a session.
    press(&mut app, KeyCode::Enter);
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
    assert!(app.sessions_page.busy.is_none());
    press(&mut app, KeyCode::Char('j'));
    assert_eq!(highlighted(&app).as_deref(), Some("a1"));
    press(&mut app, KeyCode::Char('h'));
    press(&mut app, KeyCode::Char('l'));
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
}

#[test]
fn the_folders_wrap_back_to_all() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Up);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/alpha"));
    press(&mut app, KeyCode::Down);
    assert_eq!(app.sessions_page.browse.folder, None);
    assert_eq!(app.sessions_page.view.rows.len(), 3);
}

#[test]
fn esc_closes_from_the_folders_and_the_page_reopens_on_the_sessions_of_the_folder() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Esc);
    assert!(!app.sessions_page.open);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/beta"));
    assert_eq!(highlighted(&app).as_deref(), Some("b1"));
}

#[test]
fn without_the_folders_column_left_stays_on_the_sessions() {
    let mut app = page();
    sessions_page::folders::resized(&mut app, 100);
    press(&mut app, KeyCode::Left);
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
    // Grouped by folder again: beta's session, then alpha's newest.
    press(&mut app, KeyCode::Down);
    assert_eq!(highlighted(&app).as_deref(), Some("a2"));
}

fn draw(app: &mut App, width: u16) -> Vec<String> {
    let buffer = crate::support::render(width, 24, |frame| {
        vibe_rs::ui::sessions_page::draw(app, frame, frame.area())
    });
    crate::support::rows(&buffer)
}

/// The width decides the folders column before the frame paints: the first
/// frame after a resize already has its layout and its hints.
#[test]
fn a_resize_brings_the_folders_column_and_its_flat_list_before_the_frame() {
    let mut app = page();
    sessions_page::folders::resized(&mut app, 100);
    let narrow = draw(&mut app, 100).join("\n");
    assert!(!narrow.contains("All "), "{narrow}");
    assert!(!narrow.contains("← folders"), "{narrow}");
    assert!(
        narrow.contains("alpha  2"),
        "the list groups by folder: {narrow}"
    );
    sessions_page::folders::resized(&mut app, 120);
    let wide = draw(&mut app, 120).join("\n");
    assert!(wide.contains("All "), "{wide}");
    assert!(wide.contains("← folders"), "{wide}");
    assert!(!wide.contains("alpha  2"), "the list is flat: {wide}");
}

#[test]
fn a_narrowing_resize_gives_the_keys_back_to_the_sessions() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    assert_eq!(app.sessions_page.browse.focus, Column::Folders);
    sessions_page::folders::resized(&mut app, 100);
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
    sessions_page::folders::resized(&mut app, 120);
    press(&mut app, KeyCode::Down);
    assert_eq!(
        app.sessions_page.browse.folder, None,
        "the arrows move the sessions"
    );
}

#[test]
fn searching_gives_the_keys_to_the_sessions() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Char('/'));
    assert_eq!(app.sessions_page.browse.focus, Column::Sessions);
    press(&mut app, KeyCode::Down);
    assert_eq!(app.sessions_page.browse.folder, None);
    assert_eq!(highlighted(&app).as_deref(), Some("a2"));
}

#[test]
fn paging_keys_in_the_folders_go_to_all_and_the_last_folder() {
    let mut app = page();
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::End);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/alpha"));
    assert_eq!(app.sessions_page.browse.focus, Column::Folders);
    press(&mut app, KeyCode::PageUp);
    assert_eq!(app.sessions_page.browse.folder, None);
    press(&mut app, KeyCode::PageDown);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/alpha"));
    press(&mut app, KeyCode::Home);
    assert_eq!(app.sessions_page.browse.folder, None);
}

/// The screen cell of `text` in the frame drawn at 120 columns.
fn cell(app: &mut App, text: &str) -> (u16, u16) {
    let screen = crate::support::rows(&crate::support::render(120, 24, |frame| app.draw(frame)));
    let y = screen
        .iter()
        .position(|row| row.contains(text))
        .expect(text);
    (crate::support::column(&screen[y], text), y as u16)
}

#[test]
fn a_click_on_a_folder_lists_it() {
    let mut app = page();
    let (x, y) = cell(&mut app, "beta");
    crate::support::click(&mut app, x, y);
    assert_eq!(app.sessions_page.browse.folder.as_deref(), Some("/w/beta"));
    assert_eq!(app.sessions_page.browse.focus, Column::Folders);
}

#[test]
fn a_folder_click_waits_while_a_switch_or_a_rename_owns_the_page() {
    let mut app = page();
    let (x, y) = cell(&mut app, "beta");
    app.sessions_page.busy = Some("a1".into());
    crate::support::click(&mut app, x, y);
    assert_eq!(app.sessions_page.browse.folder, None);
    app.sessions_page.busy = None;
    press(&mut app, KeyCode::Char('r'));
    assert!(app.sessions_page.rename.is_some());
    crate::support::click(&mut app, x, y);
    assert_eq!(app.sessions_page.browse.folder, None);
    assert_eq!(
        highlighted(&app).as_deref(),
        Some("b1"),
        "the renamed row stays shown"
    );
}

/// The folders column needs room on the page itself: the plan panel docked
/// beside it on a terminal wide enough alone leaves the list and preview only.
#[test]
fn the_plan_panel_narrowing_the_page_drops_the_folders_column() {
    let mut app = page();
    assert!(app.sessions_page.folders_shown);
    app.todo_sidebar.open = true;
    crate::support::render(120, 24, |frame| app.draw(frame));
    assert!(!app.sessions_page.folders_shown);
    sessions_page::folders::resized(&mut app, 120);
    assert!(!app.sessions_page.folders_shown);
    app.todo_sidebar.open = false;
    crate::support::render(120, 24, |frame| app.draw(frame));
    assert!(app.sessions_page.folders_shown);
}
