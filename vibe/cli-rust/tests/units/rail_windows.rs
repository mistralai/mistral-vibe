//! Rail windows: Ctrl+C and paste over a hidden draft, scrolling, wrapping, and the rail's inert and narrow states.

use crossterm::event::KeyCode;
use ratatui::buffer::Buffer;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::input::{handle_paste, handle_priority_key};
use vibe_rs::rail::{self, Item};
use vibe_rs::todo_tracker::{TodoItem, TodoStatus};

use crate::support::{click, ctrl, press, render, rows, stub};

fn with_goal(objective: &str) -> App {
    let mut app = App::default();
    app.goal.goal = Some(
        serde_json::from_value(json!({
            "goalId": "goal-1", "revision": 1, "objectiveRevision": 1,
            "objective": objective, "status": "active", "tokenBudget": 50000,
            "tokensUsed": 12500, "timeUsedSeconds": 240,
        }))
        .unwrap(),
    );
    app
}

fn with_todos(app: &mut App, count: usize) {
    let todos = (0..count)
        .map(|index| TodoItem {
            id: index.to_string(),
            content: format!("todo number {index}"),
            status: TodoStatus::Pending,
        })
        .collect();
    app.todo_tracker.record(todos);
}

fn with_tasks(app: &mut App, count: usize) {
    app.session.background_tasks = (0..count)
        .map(|index| {
            let command = format!("task-{index:02}");
            serde_json::from_value(json!({
                "processId": command, "command": command, "status": "running",
            }))
            .unwrap()
        })
        .collect();
}

fn draw(app: &mut App) -> Buffer {
    render(80, 30, |frame| app.draw(frame))
}

fn row_of(buffer: &Buffer, text: &str) -> Option<u16> {
    rows(buffer)
        .iter()
        .position(|row| row.contains(text))
        .map(|y| y as u16)
}

#[test]
fn ctrl_c_closes_a_rail_window_and_keeps_the_draft_it_hides() {
    let mut app = with_goal("Ship it");
    with_todos(&mut app, 2);
    app.chat_input.input = "secret draft".into();
    rail::open(&mut app, &stub(), Item::Plan);

    assert_eq!(
        handle_priority_key(&mut app, &stub(), ctrl('c')),
        Some(false)
    );
    assert_eq!(rail::window(&app), None);
    assert_eq!(app.chat_input.full_text(), "secret draft");
}

#[test]
fn a_paste_over_a_rail_window_skips_the_hidden_composer() {
    let mut app = with_goal("Ship it");
    rail::open(&mut app, &stub(), Item::Goal);
    rail::enter(&mut app);
    rail::leave(&mut app);

    handle_paste(&mut app, "pasted".into());
    assert_eq!(app.chat_input.full_text(), "");
}

#[test]
fn the_tasks_window_paints_and_highlights_the_scrolled_rows() {
    let mut app = App::default();
    with_tasks(&mut app, 14);
    rail::open(&mut app, &stub(), Item::Tasks);
    draw(&mut app);
    for _ in 0..12 {
        press(&mut app, KeyCode::Down);
    }
    let buffer = draw(&mut app);
    let y = row_of(&buffer, "task-12").expect("the highlighted task is painted");

    let below = row_of(&buffer, "task-11").expect("the row above is painted");
    click(&mut app, 10, below);
    assert_eq!(
        app.session.background_tasks.len(),
        14,
        "a click never stops a task"
    );
    assert_eq!(
        app.tasks_panel.list.selected(),
        11,
        "it highlights the painted row"
    );

    click(&mut app, 10, y);
    press(&mut app, KeyCode::Char('s'));
    let left: Vec<_> = app
        .session
        .background_tasks
        .iter()
        .map(|task| task.command.clone())
        .collect();
    assert!(!left.contains(&"task-12".to_owned()), "{left:?}");
    assert_eq!(left.len(), 13);
}

#[test]
fn the_goal_window_fits_a_wrapped_objective_and_its_usage() {
    let mut app = with_goal(&"word ".repeat(48));
    rail::open(&mut app, &stub(), Item::Goal);

    assert!(row_of(&draw(&mut app), "tokens").is_some());
}

#[test]
fn the_todo_window_scrolls_to_its_last_item() {
    let mut app = App::default();
    with_todos(&mut app, 14);
    rail::open(&mut app, &stub(), Item::Plan);
    assert!(row_of(&draw(&mut app), "todo number 13").is_none());

    for _ in 0..20 {
        press(&mut app, KeyCode::Char('j'));
    }
    let buffer = draw(&mut app);
    assert!(row_of(&buffer, "todo number 13").is_some());
    assert!(row_of(&buffer, "↑↓/jk scroll").is_some());

    press(&mut app, KeyCode::Up);
    assert!(row_of(&draw(&mut app), "todo number 13").is_none());
}

#[test]
fn the_todo_window_wraps_and_opens_on_the_current_todo_below_wrapped_ones() {
    let mut app = App::default();
    let todo = |index: usize, status| TodoItem {
        id: index.to_string(),
        content: format!("{}tail {index}", "word ".repeat(20)),
        status,
    };
    let mut todos: Vec<_> = (0..16).map(|i| todo(i, TodoStatus::Completed)).collect();
    todos.push(todo(16, TodoStatus::InProgress));
    app.todo_tracker.record(todos);
    rail::open(&mut app, &stub(), Item::Plan);
    assert!(row_of(&draw(&mut app), "tail 16").is_some());
}

#[test]
fn a_chip_click_closes_the_picker_in_the_box_and_opens_its_window() {
    let mut app = crate::support::with_picker();
    with_todos(&mut app, 1);
    draw(&mut app);
    let &(_, x, _) = app
        .rail
        .hits
        .first()
        .expect("the rail paints under the picker");

    let y = app.rail.area.y;
    click(&mut app, x, y);
    assert!(!app.theme_picker.open);
    assert_eq!(rail::window(&app), Some(Item::Plan));
}

#[test]
fn a_chip_click_waits_for_a_tool_approval() {
    let mut app = App::default();
    with_todos(&mut app, 1);
    draw(&mut app);
    let (_, x, _) = app.rail.hits[0];
    app.approval.open = true;

    let y = app.rail.area.y;
    click(&mut app, x, y);
    assert!(app.approval.open);
    assert_eq!(rail::window(&app), None);
}

#[test]
fn ctrl_x_on_a_narrow_rail_selects_a_painted_item() {
    let mut app = with_goal("Ship it");
    with_todos(&mut app, 1);
    with_tasks(&mut app, 2);
    crate::support::rail(&mut app, 8);

    rail::enter(&mut app);
    assert_eq!(rail::selected(&app), Some(Item::Plan));
}

#[test]
fn ctrl_x_skips_a_rail_too_narrow_to_paint() {
    let mut app = with_goal("Ship it");
    crate::support::rail(&mut app, 4);

    assert!(!rail::enter(&mut app));
}

#[test]
fn ctrl_d_leaves_the_draft_a_rail_window_hides() {
    let mut app = with_goal("Ship it");
    app.chat_input.input = "secret".into();
    app.chat_input.cursor = 0;
    rail::open(&mut app, &stub(), Item::Goal);

    assert_eq!(
        handle_priority_key(&mut app, &stub(), ctrl('d')),
        Some(false)
    );
    assert_eq!(app.chat_input.full_text(), "secret");
}

#[test]
fn scrolling_a_window_drops_its_selection() {
    let mut app = App::default();
    with_todos(&mut app, 14);
    rail::open(&mut app, &stub(), Item::Plan);
    let y = row_of(&draw(&mut app), "todo number 0").expect("first todo");
    vibe_rs::selection::press_owned(&mut app, (4, y), vibe_rs::selection::RegionId::RailWindow);
    vibe_rs::selection::drag(&mut app, (12, y));
    vibe_rs::selection::release(&mut app);
    assert!(app.selection.region.is_some());

    rail::scroll(&mut app, true, 2);
    assert!(app.selection.region.is_none());
}

#[test]
fn window_hints_drop_from_the_front_to_keep_the_border() {
    let mut app = with_goal(&"word ".repeat(40));
    let buffer = render(30, 30, |frame| {
        rail::open(&mut app, &stub(), Item::Goal);
        app.draw(frame)
    });
    let bottom = rows(&buffer)
        .into_iter()
        .find(|row| row.contains("Esc close"))
        .expect("hints");
    assert!(bottom.trim_end().ends_with('┘'), "{bottom}");
}

#[test]
fn ctrl_c_closes_the_goal_edit_over_its_window_and_keeps_the_draft() {
    let mut app = with_goal("Ship it");
    app.chat_input.input = "secret draft".into();
    rail::open(&mut app, &stub(), Item::Goal);
    press(&mut app, KeyCode::Char('e'));
    assert!(app.goal.panel.is_some());

    assert_eq!(
        handle_priority_key(&mut app, &stub(), ctrl('c')),
        Some(false)
    );
    assert!(app.goal.panel.is_none());
    assert_eq!(rail::window(&app), Some(Item::Goal));
    assert_eq!(app.chat_input.full_text(), "secret draft");
}

#[test]
fn a_window_whose_item_leaves_drops_its_selection() {
    let mut app = App::default();
    with_todos(&mut app, 3);
    rail::open(&mut app, &stub(), Item::Plan);
    let y = row_of(&draw(&mut app), "todo number 0").expect("first todo");
    vibe_rs::selection::press_owned(&mut app, (4, y), vibe_rs::selection::RegionId::RailWindow);
    vibe_rs::selection::drag(&mut app, (12, y));
    vibe_rs::selection::release(&mut app);
    assert!(app.selection.region.is_some());

    app.todo_tracker.record(Vec::new());
    draw(&mut app);
    assert!(app.selection.region.is_none());
}

#[test]
fn the_keyboard_chip_is_the_button_and_the_hovered_one_only_tints() {
    let mut app = with_goal("Ship it");
    with_todos(&mut app, 1);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Right);
    crate::support::rail(&mut app, 80);
    let goal = app.rail.hits[0];
    app.view.mouse_position = Some((goal.1, app.rail.area.y));

    let buffer = render(80, 1, |frame| {
        let area = frame.area();
        frame
            .buffer_mut()
            .set_style(area, vibe_rs::ui::theme::screen_style());
        vibe_rs::ui::rail::draw(&mut app, frame, area)
    });
    let plan = app.rail.hits[1];
    let base = buffer[(0, 0)].bg;
    let bold = |(_, x, width): (Item, u16, u16)| {
        buffer[(x + width - 1, 0)]
            .modifier
            .contains(ratatui::style::Modifier::BOLD)
    };
    assert_eq!(rail::selected(&app), Some(Item::Plan));
    assert!(bold(plan), "the keyboard's chip is the button");
    assert!(!bold(goal), "the hovered chip is not");
    assert_ne!(buffer[(goal.1, 0)].bg, base, "but it still tints");
}

#[test]
fn a_chip_under_the_mouse_tints_while_a_window_is_open() {
    let mut app = with_goal("Ship it");
    with_todos(&mut app, 1);
    crate::support::rail(&mut app, 80);
    let goal = app.rail.hits[0];
    rail::open(&mut app, &stub(), Item::Plan);
    app.view.mouse_position = Some((goal.1, app.rail.area.y));

    let buffer = render(80, 1, |frame| {
        let area = frame.area();
        frame
            .buffer_mut()
            .set_style(area, vibe_rs::ui::theme::screen_style());
        vibe_rs::ui::rail::draw(&mut app, frame, area)
    });
    assert_ne!(buffer[(goal.1, 0)].bg, buffer[(0, 0)].bg);
}
