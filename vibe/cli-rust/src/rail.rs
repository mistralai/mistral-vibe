//! The status rail on the loading row: subagents, background tasks, PRs, goal and plan, keyboard-reachable.

use std::sync::Arc;

use crate::app::App;
use crate::focus::Focus;
use crate::server::{Client, SessionStatus};

mod keys;
mod window;

pub use keys::{direction, handle_key, is_toggle_key, nav_key};
pub use window::{close_window, enter_from_box, handle_window_key, leave_box, scroll};

/// One rail item, in the order it is painted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Item {
    Agents,
    Tasks,
    Prs,
    Goal,
    Plan,
}

impl Item {
    /// The letter that jumps to the item while the rail has focus.
    pub fn letter(self) -> char {
        match self {
            Item::Agents => 's',
            Item::Goal => 'g',
            Item::Plan => 't',
            Item::Tasks => 'b',
            Item::Prs => 'p',
        }
    }
}

/// Rail focus and the item hit map of the latest frame.
#[derive(Default)]
pub struct Rail {
    /// Whether the rail owns the keyboard; panels it opens stack above it.
    pub focused: bool,
    /// The highlighted item while focused.
    pub selected: Option<Item>,
    /// Jump letters show from Ctrl+X in the prompt until an item opens.
    pub letters: bool,
    /// Painted items as `(item, x, width)` inside `area`, for mouse routing.
    pub hits: Vec<(Item, u16, u16)>,
    /// Items the latest frame had no room for; the keyboard skips them too.
    pub hidden: Vec<Item>,
    pub area: ratatui::layout::Rect,
    /// The open window of the tasks, PR, goal or plan item; the subagent list has its own focus.
    pub window: Option<Item>,
    /// Where the goal or plan text starts; the frame resolves it, wrap width known.
    pub scroll: Scroll,
    /// The row the last frame started at, and the largest it allowed.
    pub scroll_top: usize,
    pub scroll_max: usize,
}

/// The goal or plan window's first row: a row, or the in-progress todo's.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scroll {
    Row(usize),
    Current,
}

impl Default for Scroll {
    fn default() -> Self {
        Scroll::Row(0)
    }
}

/// The open tasks, PR, goal or plan window, while its item is still on the rail.
pub fn window(app: &App) -> Option<Item> {
    app.rail.window.filter(|item| items(app).contains(item))
}

/// Close a window whose item left the rail, so it never comes back with the item; an emptied rail hands the keyboard back.
pub fn prune(app: &mut App) {
    if app.rail.window.is_some() && window(app).is_none() {
        app.rail.window = None;
    }
    if app.rail.focused && items(app).is_empty() {
        leave(app);
    }
}

/// Subagent counts the rail shows: `(running, listed)`, waiting ones included in `listed`.
pub fn agent_counts(app: &App) -> (usize, usize) {
    let running = app
        .subagents
        .sessions
        .iter()
        .filter(|child| child.status == SessionStatus::Running)
        .count();
    let listed = app.subagents.list.rows.len();
    (running, listed)
}

/// The items the rail paints now, left to right; the todo list is always last.
pub fn items(app: &App) -> Vec<Item> {
    let agents = app.subagents.status_list_enabled
        && app.subagents.list.shown
        && app.subagents.viewed_subagent_id.is_none()
        && agent_counts(app).1 > 0;
    [
        (agents, Item::Agents),
        (!app.session.background_tasks.is_empty(), Item::Tasks),
        (
            app.artifacts_modal.enabled && !app.session.artifacts.is_empty(),
            Item::Prs,
        ),
        // The resume prompt's row already shows the goal.
        (
            crate::ui::goal_row::height(app) == 0 && app.goal.goal.is_some(),
            Item::Goal,
        ),
        (!app.todo_tracker.todos().is_empty(), Item::Plan),
    ]
    .into_iter()
    .filter_map(|(shown, item)| shown.then_some(item))
    .collect()
}

/// The items on the rail minus those the latest frame had no room for.
pub(crate) fn painted(app: &App) -> Vec<Item> {
    let mut items = items(app);
    items.retain(|item| !app.rail.hidden.contains(item));
    items
}

/// The highlighted item, falling back to the first painted one when it left the rail.
pub fn selected(app: &App) -> Option<Item> {
    let items = painted(app);
    app.rail
        .selected
        .filter(|item| items.contains(item))
        .or_else(|| items.first().copied())
}

/// Enter the rail with its letters, on the open window's item or else the first one.
pub fn enter(app: &mut App) -> bool {
    let Some(first) = painted(app).first().copied() else {
        return false;
    };
    crate::completion_manager::dismiss(app);
    crate::selection::blur_composer(app);
    let open = open_item(app);
    app.rail.focused = true;
    app.rail.letters = true;
    app.rail.selected = open.or(Some(first));
    true
}

/// Hand the keyboard back to the prompt; the jump letters go with the focus.
pub fn leave(app: &mut App) {
    app.rail.focused = false;
    app.rail.letters = false;
}

/// Leave the letters: back to the open window, which still closes to the rail, else to the prompt.
pub fn back(app: &mut App) {
    match window_open(app) {
        true => app.rail.letters = false,
        false => leave(app),
    }
}

/// Open what an item stands for; the rail keeps focus underneath, so closing it comes back here.
pub fn open(app: &mut App, client: &Arc<Client>, item: Item) {
    crate::completion_manager::dismiss(app);
    app.rail.selected = Some(item);
    app.rail.letters = false;
    crate::selection::clear_region(app, crate::selection::RegionId::RailWindow);
    if app.subagents.viewed_subagent_id.is_some() && item != Item::Agents {
        crate::subagents::show_main_chat(app, true);
    } else if app.subagents.list.focused && app.subagents.viewed_subagent_id.is_none() {
        crate::subagents::leave_list(app);
    }
    app.rail.window = None;
    match item {
        Item::Agents => {
            crate::subagents::focus_first(app);
        }
        Item::Tasks => {
            app.tasks_panel.list.reset();
            app.rail.window = Some(item);
        }
        Item::Prs => crate::artifacts_modal::open(app, client),
        Item::Goal => window::show_text(app, item, Scroll::Row(0)),
        Item::Plan => window::show_text(app, item, Scroll::Current),
    }
}

/// The openable item painted at `(column, row)` in the latest frame.
pub fn item_at(app: &App, column: u16, row: u16) -> Option<Item> {
    // A tool approval or a question waits for its answer, so the rail under it is inert.
    let answering = matches!(app.focus(), Focus::Approval | Focus::Question);
    if row != app.rail.area.y || answering {
        return None;
    }
    app.rail
        .hits
        .iter()
        .find(|(_, x, width)| (*x..x + width).contains(&column))
        .map(|(item, _, _)| *item)
}

/// The openable item under the mouse, tinted like a hovered button.
pub fn hovered(app: &App) -> Option<Item> {
    let (column, row) = app.view.mouse_position?;
    item_at(app, column, row)
}

/// A click on a painted item opens it, or closes its open window; either way the keyboard goes back to the prompt.
pub fn click(app: &mut App, client: &Arc<Client>, column: u16, row: u16) {
    if let Some(item) = item_at(app, column, row) {
        leave(app);
        // A second click on the open window's chip closes it.
        if open_item(app) == Some(item) && window::close(app, item) {
            return;
        }
        open(app, client, item);
        if item == Item::Prs {
            crate::telemetry::slash_command_clicked(app, "/pr", "rail");
        }
    }
}

/// Whether an item's window is open: the subagent list or a child view counts too.
pub fn window_open(app: &App) -> bool {
    open_item(app).is_some()
}

/// The item whose window is open: the tasks, PR, goal or plan window, or the subagent list and child view.
pub fn open_item(app: &App) -> Option<Item> {
    let list = app.subagents.list.focused || app.subagents.viewed_subagent_id.is_some();
    window(app).or_else(|| list.then_some(Item::Agents))
}

/// ←→ in an open window: open the previous or next item's window, wrapping at both ends.
pub fn switch(app: &mut App, client: &Arc<Client>, right: bool) {
    let items = painted(app);
    if items.is_empty() {
        return;
    }
    // A window whose item the rail had no room for steps onto the painted ends.
    let next = match open_item(app).and_then(|item| items.iter().position(|i| *i == item)) {
        Some(current) if right => (current + 1) % items.len(),
        Some(current) => (current + items.len() - 1) % items.len(),
        None if right => 0,
        None => items.len() - 1,
    };
    open(app, client, items[next]);
}
