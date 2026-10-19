//! Typed editor behavior for `/config` fields.

mod input;

use std::sync::Arc;

use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseEvent};
use serde_json::Value;
use tokio::sync::mpsc;

use crate::app::App;
use crate::config::{self, ConfigField};
use crate::option_list::{OptionList, SingleAction};

pub const MAX_VISIBLE_CHOICES: usize = 14;

pub struct ConfigEdit {
    pub field: ConfigField,
    pub draft: String,
    pub cursor: usize,
    pub input_width: Option<u16>,
    /// First visible row of the text editor.
    pub scroll: usize,
    pub target: usize,
    pub error: Option<String>,
    /// Highlighted choice and the choice-list viewport.
    pub list: OptionList<SingleAction>,
}

pub fn open(app: &mut App, field: ConfigField) {
    let choices = choices(&field);
    let mut draft = raw_text(&field.raw_value, &field.kind);
    if !choices.is_empty() && !choices.contains(&draft) {
        draft.clone_from(&choices[0]);
    }
    let selected = choices.iter().position(|choice| *choice == draft);
    app.config_screen.edit = Some(ConfigEdit {
        cursor: draft.len(),
        input_width: None,
        draft,
        scroll: 0,
        target: default_target(&field, &app.config_screen.targets),
        field,
        error: None,
        list: OptionList::new(selected.unwrap_or_default()),
    });
}

/// Preselect the layer the value currently comes from, so saving rewrites it
/// instead of shadowing it from another layer. Layers are highest priority first.
fn default_target(field: &ConfigField, targets: &[String]) -> usize {
    field
        .layers
        .iter()
        .find_map(|(layer, _)| targets.iter().position(|target| target == layer))
        .unwrap_or(0)
}

pub fn handle_key(
    app: &mut App,
    client: &Arc<Client>,
    tx: &mpsc::Sender<config::Loaded>,
    key: KeyEvent,
) {
    if key.code == KeyCode::Esc {
        app.config_screen.edit = None;
        return;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('s') {
        commit(app, client, tx);
        return;
    }
    if key.code == KeyCode::Enter
        && app
            .config_screen
            .edit
            .as_ref()
            .is_some_and(|edit| !is_multiline(&edit.field) || !choices(&edit.field).is_empty())
    {
        commit(app, client, tx);
        return;
    }
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return;
    };
    let choices = choices(&edit.field);
    if choices.is_empty() && key.code != KeyCode::Tab {
        input::handle_key(edit, key);
        return;
    }
    match key.code {
        KeyCode::Tab if !app.config_screen.targets.is_empty() => {
            edit.target = (edit.target + 1) % app.config_screen.targets.len()
        }
        KeyCode::Up | KeyCode::Char('k') | KeyCode::Down | KeyCode::Char('j')
            if !choices.is_empty() =>
        {
            let down = matches!(key.code, KeyCode::Down | KeyCode::Char('j'));
            edit.list.step(down, choices.len(), true, |_| true);
            let index = edit.list.selected();
            select_choice(edit, &choices, index);
        }
        _ => {}
    }
}

pub fn resized(app: &mut App) {
    if let Some(edit) = app.config_screen.edit.as_mut() {
        edit.input_width = None;
    }
}

pub fn wheel(app: &mut App, delta: isize) {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return;
    };
    let choices = choices(&edit.field);
    if choices.is_empty() {
        return;
    }
    if edit.list.free_scroll() {
        edit.list.wheel(delta > 0, delta.unsigned_abs());
        return;
    }
    let index = edit
        .list
        .selected()
        .saturating_add_signed(delta)
        .min(choices.len() - 1);
    select_choice(edit, &choices, index);
}

/// A click on a choice picks it; returns whether to commit.
pub fn mouse(app: &mut App, event: MouseEvent) -> bool {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return false;
    };
    let Some(index) = edit.list.mouse(event) else {
        return false;
    };
    select_choice(edit, &choices(&edit.field), index);
    true
}

pub fn paste(app: &mut App, text: String) {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return;
    };
    if choices(&edit.field).is_empty() {
        crate::utils::input_edit::insert(&mut edit.draft, &mut edit.cursor, &text);
        edit.error = None;
    }
}

pub fn choices(field: &ConfigField) -> Vec<String> {
    match field.kind.as_str() {
        "bool" => vec!["True".to_owned(), "False".to_owned()],
        "enum" => field.enum_choices.clone(),
        _ => Vec::new(),
    }
}

fn select_choice(edit: &mut ConfigEdit, choices: &[String], index: usize) {
    if let Some(choice) = choices.get(index) {
        edit.draft.clone_from(choice);
        edit.cursor = edit.draft.len();
        edit.list.select(index);
    }
}

pub fn choice_label(field: &ConfigField, choice: &str) -> String {
    field
        .value_labels
        .get(choice)
        .cloned()
        .unwrap_or_else(|| choice.to_owned())
}

pub fn is_multiline(field: &ConfigField) -> bool {
    matches!(field.kind.as_str(), "list" | "complex")
}

pub(crate) fn commit(app: &mut App, client: &Arc<Client>, tx: &mpsc::Sender<config::Loaded>) {
    let Some(mut edit) = app.config_screen.edit.take() else {
        return;
    };
    match parse_value(&edit.draft, &edit.field.kind) {
        Ok(value) => {
            let target = app
                .config_screen
                .targets
                .get(edit.target)
                .cloned()
                .unwrap_or_default();
            config::write(app, client, tx, edit.field, "set", value, target)
        }
        Err(text) => {
            edit.error = Some(text);
            app.config_screen.edit = Some(edit);
        }
    }
}

fn raw_text(value: &Value, kind: &str) -> String {
    match kind {
        "bool" => if value.as_bool().unwrap_or(false) {
            "True"
        } else {
            "False"
        }
        .to_owned(),
        "complex" => serde_json::to_string_pretty(value).unwrap_or_default(),
        "list" => value
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default(),
        _ => value
            .as_str()
            .map(str::to_owned)
            // A null scalar (an unknown context window) edits from an empty
            // draft, not from the literal text "null".
            .unwrap_or_else(|| {
                if value.is_null() {
                    String::new()
                } else {
                    value.to_string()
                }
            }),
    }
}

fn parse_value(draft: &str, kind: &str) -> Result<Value, String> {
    match kind {
        "bool" => match draft {
            "True" => Ok(Value::Bool(true)),
            "False" => Ok(Value::Bool(false)),
            _ => Err("Expected True or False".to_owned()),
        },
        "int" => draft
            .trim()
            .parse::<i64>()
            .map(|value| Value::Number(value.into()))
            .map_err(|_| "Expected an integer".to_owned()),
        "float" => serde_json::from_str(draft.trim())
            .ok()
            .filter(Value::is_number)
            .ok_or_else(|| "Expected a number".to_owned()),
        "list" => Ok(Value::Array(
            draft
                .lines()
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(|item| Value::String(item.to_owned()))
                .collect(),
        )),
        "complex" => serde_json::from_str(draft).map_err(|_| "Expected valid JSON".to_owned()),
        _ => Ok(Value::String(draft.to_owned())),
    }
}
