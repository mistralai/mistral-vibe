//! Code preview hover: the popup anchors to the hovered link, keeps a selection until copied and numbers its lines.

use crossterm::event::MouseEventKind;
use ratatui::buffer::Buffer;
use vibe_rs::app::{App, Status};
use vibe_rs::code_preview::load::read;
use vibe_rs::code_preview::target::parse;
use vibe_rs::selection::RegionId;

use crate::support::{down, drag, message, mouse, redraw, row, send_mouse, session_state, up};

/// An app whose answer links `f.py` twice on stacked rows, with the session in `dir`.
fn linked(dir: &tempfile::TempDir, file: &str) -> App {
    answered(dir, file, "- [f.py](f.py:2)\n- [f.py](f.py:2)")
}

fn answered(dir: &tempfile::TempDir, file: &str, answer: &str) -> App {
    std::fs::write(dir.path().join("f.py"), file).unwrap();
    let mut app = App::default();
    app.set_session_id("s".to_owned());
    app.set_status(Status::Ready);
    app.session.cwd = Some(dir.path().to_str().unwrap().to_owned());
    app.view
        .transcript
        .load_snapshot(&session_state("s", vec![message("a", "assistant", answer)]));
    redraw(&mut app);
    app
}

fn link_cells(app: &App) -> Vec<(u16, u16)> {
    app.view
        .link_hitmap
        .iter()
        .map(|link| (link.rows()[0].1, link.rows()[0].0))
        .collect()
}

fn hover(app: &mut App, at: (u16, u16)) {
    send_mouse(app, mouse(MouseEventKind::Moved, at.0, at.1));
}

/// Hover `at` and open its preview with the file loaded, as the delay and the load would.
fn open(app: &mut App, at: (u16, u16)) -> Buffer {
    hover(app, at);
    let (target, _) = app.code_preview.link.clone().unwrap();
    let cwd = app.session.cwd.clone();
    app.code_preview.snippet = read(&parse(&target, cwd.as_deref()).unwrap(), cwd.as_deref()).ok();
    app.code_preview.shown = true;
    redraw(app)
}

#[test]
fn the_popup_anchors_to_the_second_of_two_stacked_links_to_one_file() {
    let dir = tempfile::tempdir().unwrap();
    let mut app = linked(&dir, "a = 1\nb = 2\nc = 3\n");
    let cells = link_cells(&app);
    assert_eq!(cells.len(), 2);
    hover(&mut app, cells[0]);
    open(&mut app, cells[1]);
    assert_eq!(app.code_preview.link.as_ref().unwrap().1, cells[1]);
    assert!(!app.code_preview.area.is_empty());
    assert!(!app.code_preview.area.contains(cells[1].into()));
}

#[test]
fn a_selection_dragged_out_of_the_popup_survives_until_copied() {
    let dir = tempfile::tempdir().unwrap();
    let mut app = linked(&dir, "alpha = 1\nbeta = 2\ngamma = 3\n");
    let cells = link_cells(&app);
    open(&mut app, cells[0]);
    let area = app.code_preview.area;
    let (x, y) = (area.x + 10, area.y + 1);
    send_mouse(&mut app, down(x, y));
    send_mouse(&mut app, drag(x + 4, area.bottom()));
    send_mouse(&mut app, up(x + 4, area.bottom()));
    hover(&mut app, (x + 4, area.bottom() + 1));
    let selection = app.selection.region.as_ref().unwrap();
    assert_eq!(selection.owner, RegionId::CodePreview);
    assert!(app.code_preview.link.is_some());
    redraw(&mut app);
    hover(&mut app, (x + 4, area.bottom() + 1));
    assert!(app.code_preview.link.is_none());
}

#[test]
fn the_popup_of_a_wrapped_link_is_reachable_from_its_first_row() {
    let dir = tempfile::tempdir().unwrap();
    let answer = "The entry point that you asked about lives in [the greet module of the workspace](f.py:2) here.";
    let mut app = answered(&dir, "a = 1\nb = 2\nc = 3\n", answer);
    let rows = app.view.link_hitmap[0].rows().to_vec();
    assert_eq!(rows.len(), 2);
    let (y, x) = (rows[0].0, rows[0].1 + rows[0].2 - 1);
    open(&mut app, (x, y));
    assert_eq!(app.code_preview.area.y, y + 1);
    hover(&mut app, (x, y + 1));
    assert!(app.code_preview.link.is_some());
}

#[test]
fn a_press_on_the_link_closes_the_popup_so_a_drag_selects_the_transcript() {
    let dir = tempfile::tempdir().unwrap();
    let mut app = linked(&dir, "a = 1\nb = 2\nc = 3\n");
    let cells = link_cells(&app);
    open(&mut app, cells[0]);
    assert!(!app.code_preview.area.is_empty());
    send_mouse(&mut app, down(cells[0].0, cells[0].1));
    send_mouse(&mut app, drag(cells[0].0 + 5, cells[0].1 + 3));
    redraw(&mut app);
    assert!(app.code_preview.link.is_none());
    assert!(app.code_preview.area.is_empty());
}

#[test]
fn the_popup_numbers_each_line_beside_its_code() {
    let dir = tempfile::tempdir().unwrap();
    let mut app = linked(&dir, "a = 1\nb = 2\nc = 3\n");
    let cells = link_cells(&app);
    let buffer = open(&mut app, cells[0]);
    let area = app.code_preview.area;
    let code: Vec<String> = (area.y + 1..area.bottom() - 1)
        .map(|y| row(&buffer, y).trim_matches(['│', ' ']).to_owned())
        .collect();
    assert_eq!(code, ["1  a = 1", "2  b = 2", "3  c = 3"]);
}

#[test]
fn a_wheel_at_the_edge_keeps_the_selection() {
    let dir = tempfile::tempdir().unwrap();
    let mut app = linked(&dir, "alpha = 1\nbeta = 2\ngamma = 3\n");
    let cells = link_cells(&app);
    open(&mut app, cells[0]);
    let area = app.code_preview.area;
    let (x, y) = (area.x + 10, area.y + 1);
    send_mouse(&mut app, down(x, y));
    send_mouse(&mut app, drag(x + 4, y + 1));
    send_mouse(&mut app, up(x + 4, y + 1));
    send_mouse(&mut app, mouse(MouseEventKind::ScrollDown, x, y));
    let selection = app.selection.region.as_ref().unwrap();
    assert_eq!(selection.owner, RegionId::CodePreview);
}
