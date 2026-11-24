//! A task's output in the tasks window: `s` stops the task shown, Esc steps back one level, and the output outlives its task.

use crossterm::event::KeyCode;
use vibe_rs::app::App;
use vibe_rs::focus::Focus;
use vibe_rs::rail::{self, Item, Scroll};

use crate::rail::{with_items, with_tasks};
use crate::support::{press, stub};

fn showing_output() -> App {
    let mut app = with_items(false);
    with_tasks(&mut app, &["kubectl get jobs -w", "tail -f capture.log"]);
    rail::open(&mut app, &stub(), Item::Tasks);
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Enter);
    assert!(app.tasks_panel.shown().is_some(), "Enter shows the output");
    app
}

#[test]
fn s_in_a_task_output_stops_the_task_shown() {
    let mut app = showing_output();
    app.tasks_panel.list.select(0);
    press(&mut app, KeyCode::Char('s'));
    assert_eq!(app.session.background_tasks.len(), 1);
    assert_eq!(
        app.session.background_tasks[0].command,
        "kubectl get jobs -w"
    );
    assert!(
        app.tasks_panel.shown().is_some(),
        "the output stays on screen"
    );
}

#[test]
fn esc_goes_back_to_the_list_then_closes() {
    let mut app = showing_output();
    press(&mut app, KeyCode::Esc);
    assert!(app.tasks_panel.shown().is_none());
    assert_eq!(rail::window(&app), Some(Item::Tasks));
    press(&mut app, KeyCode::Esc);
    assert_eq!(rail::window(&app), None);
}

#[test]
fn esc_in_a_window_switched_to_from_an_output_closes_it() {
    let mut app = showing_output();
    rail::open(&mut app, &stub(), Item::Goal);
    press(&mut app, KeyCode::Esc);
    assert_eq!(rail::window(&app), None);
}

#[test]
fn the_output_stays_once_its_last_task_ended() {
    let mut app = showing_output();
    app.session.background_tasks.clear();
    rail::prune(&mut app);
    assert_eq!(rail::window(&app), Some(Item::Tasks));
    assert_eq!(app.focus(), Focus::RailWindow);
    press(&mut app, KeyCode::Esc);
    assert_eq!(rail::window(&app), None);
}

#[test]
fn a_scroll_that_moves_nothing_keeps_following_the_output() {
    let mut app = showing_output();
    app.rail.scroll = Scroll::End;
    rail::scroll(&mut app, false, 1);
    assert_eq!(app.rail.scroll, Scroll::End);
}

#[test]
fn a_session_switch_closes_the_old_session_output() {
    let mut app = showing_output();
    app.set_session_id("session-a".into());
    app.set_session_id("session-b".into());
    rail::prune(&mut app);
    assert_eq!(rail::window(&app), None);
}

#[test]
fn a_scrollbar_drag_to_the_end_follows_the_output_again() {
    let mut app = showing_output();
    app.rail.scroll_max = 4;
    rail::scroll_to(&mut app, 0);
    assert_eq!(app.rail.scroll, Scroll::Row(0));
    rail::scroll_to(&mut app, 4);
    assert_eq!(app.rail.scroll, Scroll::End);
}

fn output_selection(anchor: i32, head: i32) -> vibe_rs::app::Selection {
    vibe_rs::app::Selection {
        owner: vibe_rs::selection::RegionId::RailWindow,
        anchor: (4, anchor),
        head: (20, head),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: vibe_rs::selection::ScrollTarget::Output,
        table_cell: None,
        text: String::new(),
    }
}

#[test]
fn trimmed_output_rows_move_a_selection_up_with_its_text() {
    let mut app = showing_output();
    let owner = vibe_rs::selection::RegionId::RailWindow;
    app.selection.region = Some(output_selection(5, 7));
    vibe_rs::selection::shift(&mut app, owner, -3);
    let selection = app.selection.region.as_ref().unwrap();
    assert_eq!((selection.anchor.1, selection.head.1), (2, 4));
    vibe_rs::selection::shift(&mut app, owner, -3);
    let selection = app.selection.region.as_ref().unwrap();
    assert_eq!(
        selection.anchor.1, 0,
        "the trimmed start clamps to the first row"
    );
    vibe_rs::selection::shift(&mut app, owner, -5);
    assert!(
        app.selection.region.is_none(),
        "a selection trimmed whole goes away"
    );
}

#[test]
fn scrolling_the_output_keeps_its_selection() {
    let mut app = showing_output();
    crate::support::render(100, 40, |f| app.draw(f));
    app.selection.region = Some(output_selection(5, 7));
    app.rail.scroll_max = 10;
    app.rail.scroll_top = 10;
    rail::scroll(&mut app, false, 3);
    assert!(app.selection.region.is_some());
    rail::open(&mut app, &stub(), Item::Goal);
    crate::support::render(100, 40, |f| app.draw(f));
    app.selection.region = Some(output_selection(5, 7));
    app.rail.scroll_max = 10;
    rail::scroll(&mut app, true, 3);
    assert!(
        app.selection.region.is_none(),
        "a goal selection stays on screen, not on text"
    );
}
