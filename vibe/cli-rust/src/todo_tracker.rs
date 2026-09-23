//! Pinned todo state: the latest written list, its summary line, and the wire parse.

use serde_json::Value;

/// One item of the latest `todo write` result (Python `TodoEffectItem`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TodoItem {
    pub id: String,
    pub content: String,
    pub status: TodoStatus,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TodoStatus {
    Pending,
    InProgress,
    Completed,
    Cancelled,
}

impl TodoStatus {
    fn from_wire(status: &str) -> Option<Self> {
        match status {
            "pending" => Some(Self::Pending),
            "in_progress" => Some(Self::InProgress),
            "completed" => Some(Self::Completed),
            "cancelled" => Some(Self::Cancelled),
            _ => None,
        }
    }
}

/// Python `todo_tracker.status_icon`: the status row and plan panel glyphs.
pub fn status_icon(status: TodoStatus) -> &'static str {
    match status {
        TodoStatus::Pending => "☐",
        TodoStatus::InProgress => "▶",
        TodoStatus::Completed => "☑",
        TodoStatus::Cancelled => "☒",
    }
}

/// Python `progress_label`: `done/total`, cancelled items out of the denominator.
pub fn progress_label(todos: &[TodoItem]) -> String {
    let countable = todos
        .iter()
        .filter(|todo| todo.status != TodoStatus::Cancelled)
        .count();
    let done = todos
        .iter()
        .filter(|todo| todo.status == TodoStatus::Completed)
        .count();
    format!("{done}/{countable}")
}

/// Python `summary_line`: the pinned row's one line, or `None` with no todos.
pub fn summary_line(todos: &[TodoItem]) -> Option<String> {
    if todos.is_empty() {
        return None;
    }
    let progress = progress_label(todos);
    if let Some(focus) = todos
        .iter()
        .find(|todo| todo.status == TodoStatus::InProgress)
        .or_else(|| todos.iter().find(|todo| todo.status == TodoStatus::Pending))
    {
        return Some(format!(
            "{} {progress} · {}",
            status_icon(focus.status),
            focus.content
        ));
    }
    if todos
        .iter()
        .all(|todo| todo.status == TodoStatus::Cancelled)
    {
        return Some(format!(
            "{} {progress} · All todos cancelled",
            status_icon(TodoStatus::Cancelled)
        ));
    }
    Some(format!(
        "{} {progress} · All todos complete",
        status_icon(TodoStatus::Completed)
    ))
}

/// Python `TodoTracker`: the latest written list, from settled todo effects.
#[derive(Default)]
pub struct TodoTracker {
    todos: Vec<TodoItem>,
}

impl TodoTracker {
    pub fn todos(&self) -> &[TodoItem] {
        &self.todos
    }

    pub fn record(&mut self, todos: Vec<TodoItem>) {
        self.todos = todos;
    }

    pub fn clear(&mut self) {
        self.todos.clear();
    }

    pub fn summary(&self) -> Option<String> {
        summary_line(&self.todos)
    }

    /// Adopting a session adopts its newest settled todo effect; live events never backfill.
    pub fn seed_from_history(&mut self, history: Option<&Vec<Value>>) {
        self.todos = history
            .and_then(|entries| entries.iter().rev().find_map(todos_from_entry))
            .unwrap_or_default();
    }
}

/// The full-plan right panel (Cmd+\): a non-modal dock fed by the pinned row's list.
#[derive(Default)]
pub struct TodoSidebar {
    /// While set the panel docks on the right of the chat area.
    pub open: bool,
    /// Top line offset of the panel list, clamped at paint time.
    pub scroll: usize,
    /// Whether the last frame actually docked it; narrow terminals drop the column.
    pub visible: bool,
}

/// Python `_record_todos` only ever sees a `PublicEffectEntry`; this port reads raw wire JSON, so it checks the tag itself.
pub fn todos_from_entry(raw: &Value) -> Option<Vec<TodoItem>> {
    if raw.pointer("/type") != Some(&Value::String("effect".into())) {
        return None;
    }
    if raw.pointer("/detail/kind") != Some(&Value::String("todo".into())) {
        return None;
    }
    if raw.pointer("/state/status") != Some(&Value::String("completed".into())) {
        return None;
    }
    raw.pointer("/state/output/todos")?
        .as_array()?
        .iter()
        .map(|todo| {
            Some(TodoItem {
                id: todo.get("id")?.as_str()?.to_owned(),
                content: todo.get("content")?.as_str()?.to_owned(),
                status: TodoStatus::from_wire(todo.get("status")?.as_str()?)?,
            })
        })
        .collect()
}
