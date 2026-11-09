//! `/model` picker state: a leading Default
//! (unpinned) row plus one row per configured model, current row marked, no live
//! preview. A `/` fuzzy filter narrows the rows. A pick chains straight into
//! `/thinking`, which writes both at once.

use std::cmp::Reverse;
use std::sync::Arc;

use crossterm::event::{KeyEvent, MouseButton, MouseEvent, MouseEventKind};
use serde_json::Value;

use crate::app::{App, ModelOption};
use crate::config_write::Scope;
use crate::event_handler;
use crate::option_list::Click;
use crate::search_field::{self, Outcome};
use crate::selection::RegionId;
use crate::server::Client;
use crate::thinking_picker::{self, ModelPick, THINKING_LEVELS};
use crate::utils::fuzzy;

/// Alias persisted for the Default (unpinned) row.
pub const UNPINNED_ACTIVE_MODEL: &str = "";

/// Label of the Default row, and its search text.
const DEFAULT_LABEL: &str = "Default";
/// Row shown when the filter matches nothing.
pub const NO_MATCHES_LABEL: &str = "No matching models";

/// One visible row of the picker.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Row {
    /// The unpinned Default entry.
    Default,
    /// A model of `ModelPicker::models`.
    Model(usize),
    /// The filter matched nothing; cannot take the highlight.
    Note,
}

impl Row {
    fn selectable(&self) -> bool {
        !matches!(self, Self::Note)
    }
}

/// A `/config` field write's server answer, applied on the main thread.
pub enum Event {
    /// Runtime returned by `config/write`.
    Written(Value),
    /// The write failed; the message names the field and the server's reason.
    Failed(String),
}

/// Apply the server's answer.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Written(runtime) => event_handler::apply_runtime_value(app, &runtime),
        Event::Failed(message) => crate::notices::failed(app, &message),
    }
}

/// Hand the highlighted model to the thinking picker, opened at once on its
/// levels; nothing is written until that picker commits or is dismissed. A
/// model that cannot be resolved has no levels to offer and is written alone.
pub fn select(app: &mut App, client: &Arc<Client>, scope: Scope) {
    if !rows(app)
        .get(app.model_picker.list.selected())
        .is_some_and(Row::selectable)
    {
        return;
    }
    let alias = selected_alias(app);
    app.model_picker.open = false;
    app.model_picker.search = search_field::Search::default();
    let target = match alias.as_str() {
        UNPINNED_ACTIVE_MODEL => app.model_picker.default_alias.clone(),
        _ => alias.clone(),
    };
    let option = app.model_picker.models.iter().find(|m| m.alias == target);
    let Some((thinking, levels)) = option.map(|m| (m.thinking.clone(), m.thinking_levels.clone()))
    else {
        let pick = ModelPick {
            alias,
            target: String::new(),
            scope,
        };
        thinking_picker::commit_model(app, client, &pick);
        return;
    };
    let pick = ModelPick {
        alias,
        target,
        scope,
    };
    thinking_picker::open_for_model(app, pick, thinking, levels);
}

/// Open the picker highlighted on the current choice.
pub fn open(app: &mut App) {
    app.model_picker.search = search_field::Search::default();
    app.model_picker.list = crate::option_list::OptionList::new(current_index(app));
    app.model_picker.open = true;
}

/// Pre-selected row: the current choice's row, else `0` (Default).
fn current_index(app: &App) -> usize {
    rows(app)
        .iter()
        .position(|row| is_current_row(app, *row))
        .unwrap_or(0)
}

/// Whether `row` is the current choice: Default when unpinned, else the pinned model.
fn is_current_row(app: &App, row: Row) -> bool {
    match row {
        Row::Default => !app.model_picker.is_pinned,
        Row::Model(i) => app.model_picker.is_pinned && model_index(app) == Some(i),
        Row::Note => false,
    }
}

/// Index into `models` of the currently active alias, if any.
fn model_index(app: &App) -> Option<usize> {
    app.model_picker
        .models
        .iter()
        .position(|m| m.alias == app.model_picker.current_model)
}

/// The visible rows: with an empty query the Default row then every model in
/// config order; with a query the matches, best fuzzy score first, or a single
/// note row when nothing matches.
pub fn rows(app: &App) -> Vec<Row> {
    let query = app.model_picker.search.query.trim();
    if query.is_empty() {
        let mut rows = vec![Row::Default];
        rows.extend((0..app.model_picker.models.len()).map(Row::Model));
        return rows;
    }
    let mut scored: Vec<(i64, Row)> = Vec::new();
    if let Some(score) = fuzzy::score(query, DEFAULT_LABEL) {
        scored.push((score, Row::Default));
    }
    scored.extend(
        app.model_picker
            .models
            .iter()
            .enumerate()
            .filter_map(|(index, model)| {
                model_score(query, model).map(|score| (score, Row::Model(index)))
            }),
    );
    if scored.is_empty() {
        return vec![Row::Note];
    }
    scored.sort_by_key(|(score, _)| Reverse(*score));
    scored.into_iter().map(|(_, row)| row).collect()
}

/// Best fuzzy score of a model's display name and alias.
fn model_score(query: &str, model: &ModelOption) -> Option<i64> {
    [
        fuzzy::score(query, &model.display_name),
        fuzzy::score(query, &model.alias),
    ]
    .into_iter()
    .flatten()
    .max()
}

/// Row count: the visible rows.
pub fn option_count(app: &App) -> usize {
    rows(app).len()
}

/// Move the highlight up/down by one over the visible rows, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    let rows = rows(app);
    app.model_picker.list.step(down, rows.len(), true, |index| {
        rows.get(index).is_some_and(Row::selectable)
    });
}

/// Whether visible row `i` is the current choice.
pub fn is_current(app: &App, i: usize) -> bool {
    rows(app)
        .get(i)
        .is_some_and(|row| is_current_row(app, *row))
}

/// The alias to persist for the highlighted row (`""` for Default).
pub fn selected_alias(app: &App) -> String {
    match rows(app).get(app.model_picker.list.selected()) {
        Some(Row::Model(i)) => app
            .model_picker
            .models
            .get(*i)
            .map(|m| m.alias.clone())
            .unwrap_or_default(),
        _ => UNPINNED_ACTIVE_MODEL.to_owned(),
    }
}

/// Close the picker without persisting (Esc / cancel).
pub fn cancel(app: &mut App) {
    app.model_picker.open = false;
    app.model_picker.search = search_field::Search::default();
}

/// Route a key through the search field; false when the list handles it.
pub fn handle_search_key(app: &mut App, key: &KeyEvent) -> bool {
    match search_field::handle_key(&mut app.model_picker.search, key) {
        Outcome::Pass => false,
        Outcome::Consumed => true,
        Outcome::Filtered => {
            app.model_picker.list.reset();
            true
        }
    }
}

/// Paste into the search field; ignored when it is not focused.
pub fn paste_search(app: &mut App, text: &str) {
    if !search_field::paste(&mut app.model_picker.search, text) {
        return;
    }
    app.model_picker.list.reset();
}

/// A click on the search row focuses the field.
pub fn focus_search(app: &mut App) {
    app.model_picker.search.focused = true;
    app.model_picker.list.reset_viewport();
}

/// A press on the search field focuses it; a press on a row leaves the field;
/// a click on the highlighted row confirms, like Enter.
pub fn mouse(app: &mut App, event: MouseEvent, client: &Arc<Client>) {
    let at = (event.column, event.row);
    let pressed = event.kind == MouseEventKind::Down(MouseButton::Left);
    let on_search = app.model_picker.search.area.contains(at.into());
    let click = crate::mouse::option_gesture(app, event, RegionId::BottomApp, |app| {
        &mut app.model_picker.list
    });
    if on_search && pressed {
        focus_search(app);
        return;
    }
    if pressed && app.model_picker.list.index_at(at).is_some() {
        app.model_picker.search.focused = false;
    }
    if matches!(click, Some(Click::Confirm(_))) {
        select(app, client, Scope::Saved);
    }
}

/// Refresh the model snapshot from a `runtime/read`/`runtime/updated` value
/// (shape `{runtime: {config: {...}}}`), so the picker reflects live config.
pub fn apply_runtime(app: &mut App, response: &Value) {
    let Some(config) = crate::server::RuntimeSnapshot::config(response) else {
        return;
    };
    let models: Vec<ModelOption> = config.models.into_iter().filter_map(model_option).collect();
    let alias = config.default_model_alias;
    app.model_picker.default_display_name = models
        .iter()
        .find(|model| model.alias == alias)
        .map_or_else(|| alias.clone(), |model| model.display_name.clone());
    app.model_picker.models = models;
    app.model_picker.current_model = config.active_model.map(|m| m.alias).unwrap_or_default();
    app.model_picker.is_pinned = config.active_model_pinned;
    app.model_picker.default_alias = alias;
    if app.model_picker.open {
        let len = rows(app).len();
        if app.model_picker.list.selected() >= len {
            app.model_picker.list.select(len.saturating_sub(1));
        }
    }
}

fn model_option(model: crate::server::ModelView) -> Option<ModelOption> {
    if model.alias.is_empty() {
        return None;
    }
    // Older servers omit the per-model set; fall back to the canonical five (ADR 0014).
    let thinking_levels = match model.thinking_levels.is_empty() {
        true => THINKING_LEVELS.iter().map(|it| it.to_string()).collect(),
        false => model.thinking_levels,
    };
    Some(ModelOption {
        display_name: model.display_name.unwrap_or_else(|| model.alias.clone()),
        alias: model.alias,
        thinking: model.thinking.unwrap_or_default(),
        thinking_levels,
    })
}
