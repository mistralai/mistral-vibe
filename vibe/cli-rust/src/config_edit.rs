//! Typed editor behavior for `/config` fields.

use std::sync::Arc;

use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;
use tokio::sync::mpsc;

use crate::app::App;
use crate::config::{self, ConfigField};

pub const MAX_VISIBLE_CHOICES: usize = 14;

pub struct ConfigEdit {
    pub field: ConfigField,
    pub draft: String,
    pub choice: usize,
    pub target: usize,
}

pub fn open(app: &mut App, mut field: ConfigField) {
    apply_dynamic_choices(app, &mut field);
    let choices = choices(&field);
    let draft = raw_text(&field.raw_value, &field.kind);
    let choice = choices
        .iter()
        .position(|choice| choice == &draft)
        .unwrap_or(0);
    app.config_screen.edit = Some(ConfigEdit {
        field,
        draft,
        choice,
        target: 0,
    });
}

fn apply_dynamic_choices(app: &App, field: &mut ConfigField) {
    if field.name == "active_model" {
        field.kind = "enum".to_owned();
        field.enum_choices = std::iter::once(String::new())
            .chain(
                app.model_picker
                    .models
                    .iter()
                    .map(|model| model.alias.clone()),
            )
            .collect();
        field.value_labels.insert(
            String::new(),
            format!(
                "default (currently {})",
                app.model_picker.default_display_name
            ),
        );
        for model in &app.model_picker.models {
            field
                .value_labels
                .insert(model.alias.clone(), model.display_name.clone());
        }
    }
    if field.name == "theme" {
        field.kind = "enum".to_owned();
        field.enum_choices = crate::theme_picker::options()
            .into_iter()
            .map(str::to_owned)
            .collect();
    }
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
    match key.code {
        KeyCode::Enter if is_multiline(&edit.field) && choices.is_empty() => edit.draft.push('\n'),
        KeyCode::Tab if !app.config_screen.targets.is_empty() => {
            edit.target = (edit.target + 1) % app.config_screen.targets.len()
        }
        KeyCode::Up | KeyCode::Char('k') if !choices.is_empty() => {
            edit.choice = edit.choice.saturating_sub(1)
        }
        KeyCode::Down | KeyCode::Char('j') if !choices.is_empty() => {
            edit.choice = (edit.choice + 1).min(choices.len() - 1)
        }
        KeyCode::Backspace if choices.is_empty() => {
            edit.draft.pop();
        }
        KeyCode::Char(c)
            if choices.is_empty()
                && !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
        {
            edit.draft.push(c)
        }
        _ => {}
    }
    if let Some(choice) = choices.get(edit.choice) {
        edit.draft = choice.clone();
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
    edit.choice = edit
        .choice
        .saturating_add_signed(delta)
        .min(choices.len() - 1);
    edit.draft = choices[edit.choice].clone();
}

pub fn click(app: &mut App, column: u16, row: u16) -> bool {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return false;
    };
    let choices = choices(&edit.field);
    if choices.is_empty() {
        return false;
    }
    let area = app.config_screen.area;
    let width = area.width.saturating_sub(8).clamp(24, 80);
    let height = (choices.len().min(MAX_VISIBLE_CHOICES) as u16 + 10).max(8);
    let x = area.x + (area.width.saturating_sub(width)) / 2;
    let y = area.y + (area.height.saturating_sub(height)) / 2;
    let body_x = x + 3;
    let main_width = if edit.field.layers.is_empty() {
        width - 6
    } else {
        width.saturating_sub(43)
    };
    let choice_y = y + 2 + u16::from(!edit.field.description.is_empty());
    if column < body_x || column >= body_x + main_width || row < choice_y {
        return false;
    }
    let index = choice_offset(edit.choice, choices.len()) + (row - choice_y) as usize;
    if index >= choices.len() {
        return false;
    }
    edit.choice = index;
    edit.draft = choices[index].clone();
    true
}

pub fn paste(app: &mut App, text: String) {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return;
    };
    if choices(&edit.field).is_empty() {
        edit.draft.push_str(&text);
    }
}

pub fn choices(field: &ConfigField) -> Vec<String> {
    match field.kind.as_str() {
        "bool" => vec!["True".to_owned(), "False".to_owned()],
        "enum" => field.enum_choices.clone(),
        _ => Vec::new(),
    }
}

pub fn choice_offset(choice: usize, count: usize) -> usize {
    choice
        .saturating_sub(MAX_VISIBLE_CHOICES - 1)
        .min(count.saturating_sub(MAX_VISIBLE_CHOICES))
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
    let Some(edit) = app.config_screen.edit.take() else {
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
            app.overlays.notice = Some(crate::app::Notice {
                text,
                severity: crate::app::ToastSeverity::Information,
                until: Some(std::time::Instant::now() + std::time::Duration::from_secs(3)),
            });
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
        "complex" => serde_json::to_string(value).unwrap_or_default(),
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
            .unwrap_or_else(|| value.to_string()),
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
