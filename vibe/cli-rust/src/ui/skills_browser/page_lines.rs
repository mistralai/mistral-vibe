//! A skill's page and its version picker as styled lines.

use ratatui::style::{Modifier, Style};

use super::body_lines::blank;
use crate::server::{InstalledSkill, SkillScope};
use crate::skills_browser::labels::{
    self, ADD, DISABLED, ENABLED, FILE, LATEST_LABEL, SCOPE, SOURCE, STATUS,
};
use crate::skills_browser::model::{self, Choice, LATEST};
use crate::skills_browser::page::{selectable, Page, Property};
use crate::skills_browser::picker::{Picker, PickerRow};
use crate::skills_browser::pins::{page_pin, scope_choice};
use crate::skills_browser::State;
use crate::ui::visual_lines::{merge, text, wrap_folded, wrap_indented, Sc, VisualLine};
use crate::ui::{list_cursor, theme};
use crate::utils::paths::collapse_home;

/// The label column of a page row.
const LABEL_WIDTH: usize = 9;

/// A page's description and rows, or its picker's rows.
pub(super) fn rows(state: &State, page: &Page, width: usize) -> Vec<VisualLine> {
    match &page.picker {
        Some(open) => picker_lines(state, page, open),
        None => page_lines(state, page, width),
    }
}

/// What the skill does, then its facts in a label column, then the rows that act on it under the same labels.
fn page_lines(state: &State, page: &Page, width: usize) -> Vec<VisualLine> {
    let mut out = Vec::new();
    if !page.description.is_empty() {
        out.extend(described(&page.description, width));
        out.push(blank());
    }
    for (label, value) in facts(state, page) {
        out.extend(fact(label, value, width));
    }
    if !page.rows.is_empty() {
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

/// The page's facts: status, source, scope and file of an installed skill, or how a shared one not added yet is shared.
fn facts(state: &State, page: &Page) -> Vec<(&'static str, Vec<Sc>)> {
    let (base, dim) = list_cursor::styles(false);
    let Some(pin) = page_pin(state, page) else {
        return not_added(state, page, base);
    };
    let mut out = vec![(STATUS, status(pin))];
    if pin.locked {
        let reason = labels::lock_reason(pin);
        out.push(("", text(&reason, dim.fg(theme::text_warning()))));
    }
    let [scope, source, _] = labels::cells(pin);
    out.push((SOURCE, text(&source, base)));
    if page.subject.skill_id().is_none() && !scope.is_empty() {
        out.push((SCOPE, text(&scope, base)));
    }
    if let Some(path) = pin.path.as_deref() {
        out.push((FILE, text(&collapse_home(path), base)));
    }
    out
}

/// A shared skill not in My Skills yet: how it is shared and its newest version.
fn not_added(state: &State, page: &Page, base: Style) -> Vec<(&'static str, Vec<Sc>)> {
    let entry = page
        .subject
        .skill_id()
        .and_then(|id| state.catalog.entry(id));
    let Some(entry) = entry else {
        return Vec::new();
    };
    vec![
        (
            SOURCE,
            text(
                &format!("{} · {}", labels::SHARED, labels::sharing(entry)),
                base,
            ),
        ),
        (LATEST_LABEL, text(&labels::latest(entry), base)),
    ]
}

/// `  Label    value`, the value wrapped under itself.
fn fact(label: &str, value: Vec<Sc>, width: usize) -> Vec<VisualLine> {
    let (_, dim) = list_cursor::styles(false);
    let indent = 2 + LABEL_WIDTH;
    let room = width.saturating_sub(indent).max(1);
    let mut out = Vec::new();
    for (index, row) in wrap_folded(&value, room).into_iter().enumerate() {
        let head = if index == 0 { label } else { "" };
        let mut chars = text(&format!("  {head:<width$}", width = LABEL_WIDTH), dim);
        chars.extend(row);
        out.push(option(None, false, &chars));
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

/// `● Enabled`: on in the current color, off dim, a locked one in the warning color.
fn status(pin: &InstalledSkill) -> Vec<Sc> {
    let (base, _) = list_cursor::styles(false);
    let color = match (pin.enabled, pin.locked) {
        (_, true) => theme::text_warning(),
        (true, false) => list_cursor::current_color(),
        (false, false) => theme::muted(),
    };
    let word = state_word(pin.enabled);
    text(&format!("{} {word}", glyph(pin.enabled)), base.fg(color))
}

fn glyph(on: bool) -> &'static str {
    if on {
        "●"
    } else {
        "○"
    }
}

fn state_word(on: bool) -> &'static str {
    if on {
        ENABLED
    } else {
        DISABLED
    }
}

/// The description in the foreground, so it reads before the body under it.
fn described(description: &str, width: usize) -> Vec<VisualLine> {
    let (base, _) = list_cursor::styles(false);
    description
        .lines()
        .flat_map(|line| wrap_indented(&format!("  {line}"), base, width))
        .map(|chars| option(None, false, &chars))
        .collect()
}

/// `  Label    value  tail`, the label and tail dim.
fn property_chars(
    state: &State,
    page: &Page,
    property: Property,
    base: Style,
    dim: Style,
) -> Vec<Sc> {
    let label = match property {
        Property::Scope(scope) => labels::scope_label(scope),
        Property::Install => ADD,
    };
    if property == Property::Install {
        let call = list_cursor::accent(base, theme::text_primary()).add_modifier(Modifier::BOLD);
        return text(&format!("  + {ADD}"), call);
    }
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
        Property::Install => {}
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
