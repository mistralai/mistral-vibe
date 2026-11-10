//! A skill's page and its version picker as styled lines.

use ratatui::style::{Modifier, Style};

use super::body_lines::{blank, paragraph};
use super::list_lines::remove_again;
use crate::hints::key;
use crate::server::SkillScope;
use crate::skills_browser::labels::{self, ADD, ENABLED, REMOVE};
use crate::skills_browser::model::{self, Choice, LATEST};
use crate::skills_browser::page::{selectable, Page, Property};
use crate::skills_browser::picker::{Picker, PickerRow};
use crate::skills_browser::pins::{page_pin, scope_choice};
use crate::skills_browser::State;
use crate::ui::list_cursor;
use crate::ui::visual_lines::{merge, text, Sc, VisualLine};

/// The label column of a page row.
const LABEL_WIDTH: usize = 9;

/// A page's description and rows, or its picker's rows.
pub(super) fn rows(state: &State, page: &Page, width: usize) -> Vec<VisualLine> {
    match &page.picker {
        Some(open) => picker_lines(state, page, open),
        None => page_lines(state, page, width),
    }
}

fn page_lines(state: &State, page: &Page, width: usize) -> Vec<VisualLine> {
    let mut out = Vec::new();
    if !page.description.is_empty() {
        out.extend(paragraph(&page.description, width));
        out.push(blank());
    }
    let selected = page.list.selected();
    let takes = selectable(state, page);
    for (index, property) in page.rows.iter().enumerate() {
        let highlighted = index == selected;
        let (base, dim) = list_cursor::styles(highlighted);
        let chars = property_chars(state, page, *property, base, dim);
        out.push(option(takes[index].then_some(index), highlighted, &chars));
    }
    out
}

fn picker_lines(state: &State, page: &Page, open: &Picker) -> Vec<VisualLine> {
    let selected = open.list.selected();
    let mut out = Vec::new();
    for (index, row) in open.rows.iter().enumerate() {
        let highlighted = index == selected && row.selectable();
        let (base, dim) = list_cursor::styles(highlighted);
        let chars = match row {
            PickerRow::Heading(name) => text(name, base.add_modifier(Modifier::BOLD)),
            PickerRow::Pick(choice) => pick_chars(state, page, open.scope, choice, base, dim),
            PickerRow::Blank => Vec::new(),
            PickerRow::Unpin { inert } => unpin_chars(page, open.scope, *inert, base, dim),
        };
        out.push(option(
            row.selectable().then_some(index),
            highlighted,
            &chars,
        ));
    }
    out
}

/// `  Label    value  tail`, the label and tail dim; an armed Remove asks for Enter again.
fn property_chars(
    state: &State,
    page: &Page,
    property: Property,
    base: Style,
    dim: Style,
) -> Vec<Sc> {
    let label = match property {
        Property::Scope(scope) => labels::scope_label(scope),
        Property::Enabled => ENABLED,
        Property::Remove => REMOVE,
        Property::Install => ADD,
    };
    let mut chars = text(&format!("  {label:<width$}", width = LABEL_WIDTH), dim);
    match property {
        Property::Scope(scope) => {
            let chosen = scope_choice(state, page, scope).is_some();
            let value = labels::scope_value(state, page, scope);
            chars.extend(text(&value, if chosen { base } else { dim }));
            if let Some(tail) = labels::scope_tail(state, page, scope) {
                chars.extend(text(&format!("  {tail}"), dim));
            }
        }
        Property::Enabled => {
            let on = page_pin(state, page).is_some_and(|pin| pin.enabled);
            chars.extend(text(labels::yes_no(on), base));
        }
        Property::Remove if state.removing.as_deref() == Some(page.subject.name()) => {
            chars.extend(remove_again(key::ENTER, base));
        }
        Property::Remove | Property::Install => {}
    }
    chars
}

/// An alias with the version it points at, or a version; `›` marks what the scope pins.
fn pick_chars(
    state: &State,
    page: &Page,
    scope: SkillScope,
    choice: &Choice,
    base: Style,
    dim: Style,
) -> Vec<Sc> {
    let current = scope_choice(state, page, scope).as_ref() == Some(choice);
    let mut chars = text(
        list_cursor::marker(current),
        list_cursor::marker_style(base, current),
    );
    match choice {
        Choice::Alias(alias) => {
            chars.extend(text(alias, base));
            if let Some(version) = model::resolve(&page.versions, alias) {
                chars.extend(text(&format!(" → v{version}"), dim));
            }
            if alias == LATEST {
                let italic = dim.add_modifier(Modifier::ITALIC);
                chars.extend(text(&format!("  {}", labels::ALWAYS_NEWEST), italic));
            }
        }
        Choice::Version(version) => {
            chars.extend(text(&format!("v{version}"), base));
            if model::resolve(&page.versions, LATEST) == Some(*version) {
                chars.extend(text(&format!("  {}", labels::NEWEST), dim));
            }
        }
    }
    chars
}

/// `Unpin from Global`, or `Don't pin here` before an install; dim with the reason when it is the last scope.
fn unpin_chars(page: &Page, scope: SkillScope, inert: bool, base: Style, dim: Style) -> Vec<Sc> {
    let style = if inert { dim } else { base };
    let mut chars = text(list_cursor::marker(false), style);
    chars.extend(text(&labels::unpin(page, scope), style));
    if inert {
        chars.extend(text(&format!("  {}", labels::unpin_refused(page)), dim));
    }
    chars
}

fn option(option: Option<usize>, highlighted: bool, chars: &[Sc]) -> VisualLine {
    VisualLine {
        option,
        highlighted,
        spans: merge(chars),
    }
}
