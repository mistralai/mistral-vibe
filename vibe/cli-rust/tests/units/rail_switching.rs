//! Moving between rail windows: clicks, arrows, Ctrl+X then a letter, and Ctrl+X after a window closes.

use crossterm::event::KeyCode;
use vibe_rs::app::App;
use vibe_rs::focus::Focus;
use vibe_rs::rail::{self, Item};

use crate::rail::{with_items, with_tasks};
use crate::support::{ctrl, press, send_key, stub};

#[test]
fn a_click_opens_a_window_that_closes_back_to_the_prompt() {
    let mut app = with_items(false);
    rail::enter(&mut app);
    let painted = vibe_rs::ui::rail::width(&app, u16::MAX);
    app.rail.area = ratatui::layout::Rect::new(0, 5, painted, 1);
    app.rail.hits = vec![(Item::Plan, 10, 5)];
    rail::click(&mut app, &stub(), 12, 5);
    assert_eq!(rail::window(&app), Some(Item::Plan));
    assert!(
        !app.rail.focused,
        "a mouse open drops the rail's keyboard focus"
    );

    press(&mut app, KeyCode::Right);
    assert_eq!(
        rail::window(&app),
        Some(Item::Goal),
        "Right wraps to the first window"
    );
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Composer);
}

#[test]
fn ctrl_x_s_focuses_the_subagent_list_and_arrows_walk_the_windows() {
    let mut app = with_items(true);
    with_tasks(&mut app, &["kubectl get jobs -w"]);
    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Char('s'));
    assert_eq!(app.focus(), Focus::SubagentList);
    assert_eq!(
        rail::open_item(&app),
        Some(Item::Agents),
        "its button stays lit"
    );

    press(&mut app, KeyCode::Right);
    assert_eq!(rail::open_item(&app), Some(Item::Tasks));
    assert!(!app.subagents.list.focused);
    press(&mut app, KeyCode::Right);
    assert_eq!(rail::open_item(&app), Some(Item::Goal));
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Left);
    assert_eq!(
        app.focus(),
        Focus::SubagentList,
        "Left walks back to the list"
    );
}

#[test]
fn ctrl_x_after_closing_a_window_shows_the_letters_again() {
    let mut app = with_items(true);
    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Char('t'));
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Rail);
    assert!(!app.rail.letters);

    send_key(&mut app, ctrl('x'));
    assert!(app.rail.letters, "Ctrl+X always shows the letters");
}

#[test]
fn arrows_in_a_window_skip_items_the_rail_had_no_room_for() {
    let mut app = with_items(false);
    with_tasks(&mut app, &["a", "b"]);
    rail::open(&mut app, &stub(), Item::Plan);
    app.rail.hidden = vec![Item::Tasks];

    press(&mut app, KeyCode::Right);
    assert_eq!(
        rail::open_item(&app),
        Some(Item::Goal),
        "Right wraps past the hidden tasks"
    );
}

#[test]
fn opening_an_item_dismisses_the_completion_popup() {
    let mut app = with_items(false);
    vibe_rs::input::handle_paste(&mut app, "/he".to_owned());
    assert!(vibe_rs::completion_manager::is_open(&app));

    rail::open(&mut app, &stub(), Item::Plan);
    assert!(!vibe_rs::completion_manager::is_open(&app));
}

#[test]
fn the_wheel_over_the_tasks_window_scrolls_without_moving_the_highlight() {
    let mut app = with_items(false);
    let tasks: Vec<String> = (0..14).map(|index| format!("task-{index:02}")).collect();
    with_tasks(
        &mut app,
        &tasks.iter().map(String::as_str).collect::<Vec<_>>(),
    );
    rail::open(&mut app, &stub(), Item::Tasks);
    let painted = |app: &mut App, text: &str| {
        let buffer = crate::support::redraw(app);
        crate::support::rows(&buffer)
            .iter()
            .position(|row| row.contains(text))
            .map(|y| y as u16)
    };
    let y = painted(&mut app, "task-01").expect("task row");

    for _ in 0..4 {
        let wheel = crate::support::mouse(crossterm::event::MouseEventKind::ScrollDown, 10, y);
        crate::support::send_mouse(&mut app, wheel);
    }
    assert_eq!(
        painted(&mut app, "task-00"),
        None,
        "the wheel scrolled the list"
    );
    assert_eq!(app.tasks_panel.list.selected(), 0);
}

#[test]
fn a_second_click_on_the_open_chip_closes_its_window() {
    let mut app = with_items(false);
    app.rail.area = ratatui::layout::Rect::new(0, 5, 40, 1);
    app.rail.hits = vec![(Item::Plan, 10, 5), (Item::Goal, 2, 6)];
    rail::click(&mut app, &stub(), 12, 5);
    assert_eq!(rail::window(&app), Some(Item::Plan));

    rail::click(&mut app, &stub(), 12, 5);
    assert_eq!(rail::window(&app), None);
    assert_eq!(app.focus(), Focus::Composer);

    rail::click(&mut app, &stub(), 12, 5);
    rail::click(&mut app, &stub(), 3, 5);
    assert_eq!(
        rail::window(&app),
        Some(Item::Goal),
        "another chip switches windows"
    );
}

#[test]
fn the_subagent_list_closes_when_its_last_row_leaves() {
    let mut app = with_items(true);
    rail::open(&mut app, &stub(), Item::Agents);
    assert_eq!(app.focus(), Focus::SubagentList);

    app.subagents
        .replace_child_session(vibe_rs::server::PublicChildSession {
            id: "a".into(),
            name: "explore-1".into(),
            agent_type: "explore".into(),
            status: vibe_rs::server::SessionStatus::Archived,
            ..Default::default()
        });
    vibe_rs::subagents::refresh(&mut app);
    assert!(app.subagents.list.rows.is_empty());
    assert_eq!(
        app.focus(),
        Focus::Composer,
        "focus is back before the next frame lays out"
    );
}

#[test]
fn leaving_the_letters_over_a_window_keeps_the_rail_under_it() {
    let mut app = with_items(true);
    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Char('t'));
    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::RailWindow);

    press(&mut app, KeyCode::Esc);
    assert_eq!(
        app.focus(),
        Focus::Rail,
        "closing the window comes back to the rail"
    );
}

#[test]
fn an_arrow_after_ctrl_x_hides_the_letters_and_still_moves() {
    let mut app = with_items(true);
    send_key(&mut app, ctrl('x'));
    assert!(app.rail.letters);
    assert_eq!(rail::selected(&app), Some(Item::Agents));

    press(&mut app, KeyCode::Right);
    assert!(!app.rail.letters, "any key hides the letters");
    assert_eq!(rail::selected(&app), Some(Item::Goal));
    assert_eq!(app.focus(), Focus::Rail);
}

#[test]
fn arrows_after_ctrl_x_over_a_window_switch_windows() {
    let mut app = with_items(false);
    with_tasks(&mut app, &["watch"]);
    rail::open(&mut app, &stub(), Item::Tasks);
    send_key(&mut app, ctrl('x'));
    assert!(app.rail.letters);

    press(&mut app, KeyCode::Right);
    assert!(!app.rail.letters);
    assert_eq!(rail::open_item(&app), Some(Item::Goal));
}

#[test]
fn a_chip_click_closes_rewind_without_walking_it_back() {
    let mut app = with_items(false);
    app.rewind.open = true;
    crate::support::render(80, 30, |frame| app.draw(frame));
    let (_, x, _) = app.rail.hits[0];
    let y = app.rail.area.y;

    crate::support::click(&mut app, x, y);
    assert!(!app.rewind.open);
    assert_eq!(rail::open_item(&app), Some(app.rail.hits[0].0));
}

#[test]
fn the_hover_tint_follows_the_rail_when_it_moves() {
    let mut app = with_items(false);
    let draw_at = |app: &mut App, y: u16| {
        crate::support::render(80, 10, |frame| {
            let area = frame.area();
            let style = vibe_rs::ui::theme::screen_style();
            frame.buffer_mut().set_style(area, style);
            vibe_rs::ui::rail::draw(app, frame, ratatui::layout::Rect::new(0, y, 80, 1))
        })
    };
    draw_at(&mut app, 2);
    let (_, x, _) = app.rail.hits[0];
    app.view.mouse_position = Some((x, 2));

    let buffer = draw_at(&mut app, 6);
    assert_eq!(
        buffer[(x, 6)].bg,
        buffer[(0, 6)].bg,
        "the mouse is not on row 6"
    );
}

#[test]
fn tab_and_shift_tab_walk_the_rail_and_its_windows() {
    let mut app = with_items(true);
    let shift_tab =
        crossterm::event::KeyEvent::new(KeyCode::BackTab, crossterm::event::KeyModifiers::SHIFT);
    send_key(&mut app, ctrl('x'));
    assert_eq!(rail::selected(&app), Some(Item::Agents));
    press(&mut app, KeyCode::Tab);
    assert_eq!(rail::selected(&app), Some(Item::Goal));
    assert_eq!(
        vibe_rs::input::handle_priority_key(&mut app, &stub(), shift_tab),
        None,
        "Shift+Tab walks the rail instead of cycling agents"
    );
    send_key(&mut app, shift_tab);
    assert_eq!(rail::selected(&app), Some(Item::Agents));

    rail::open(&mut app, &stub(), Item::Plan);
    press(&mut app, KeyCode::Tab);
    assert_eq!(
        rail::open_item(&app),
        Some(Item::Agents),
        "Tab wraps to the first window"
    );
    send_key(&mut app, shift_tab);
    assert_eq!(
        rail::open_item(&app),
        Some(Item::Plan),
        "Shift+Tab steps back from the list"
    );
}

#[test]
fn ctrl_x_reaches_the_rail_from_a_picker_in_the_box() {
    let mut app = crate::support::with_picker();
    with_tasks(&mut app, &["watch"]);
    crate::support::redraw(&mut app);

    assert!(rail::enter_from_box(&mut app, &stub(), &ctrl('x')));
    assert!(
        !app.resume_picker.open,
        "the picker closes like for a chip click"
    );
    assert_eq!(app.focus(), Focus::Rail);
    assert!(app.rail.letters);

    app.approval.open = true;
    crate::support::redraw(&mut app);
    assert!(
        !rail::enter_from_box(&mut app, &stub(), &ctrl('x')),
        "an approval waits"
    );
}

#[test]
fn a_chip_click_keeps_a_goal_edit_in_progress() {
    let mut app = with_items(false);
    rail::open(&mut app, &stub(), Item::Goal);
    press(&mut app, KeyCode::Char('e'));

    assert!(!rail::leave_box(&mut app, &stub()));
    assert!(app.goal.panel.is_some(), "Esc would drop the edit");
}
