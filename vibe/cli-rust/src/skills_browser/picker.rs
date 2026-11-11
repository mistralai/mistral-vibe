//! A scope's version picker: aliases, versions, unpinning, a preview.

use std::sync::Arc;

use super::bodies::BodyKey;
use super::changes;
use super::labels::{self, ALIASES, VERSIONS};
use super::list::navigate;
use super::model::{self, Choice, Install, Subject, LATEST};
use super::page::{self, Page};
use super::pins::{effective_choice, scope_choice, sole_scope};
use super::State;
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::option_list::{OptionList, SingleAction};
use crate::server::{Client, SkillChange, SkillScope};

/// The version picker of one scope.
pub struct Picker {
    pub scope: SkillScope,
    pub rows: Vec<PickerRow>,
    pub list: OptionList<SingleAction>,
}

/// A row of the picker.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PickerRow {
    /// A bold group name: Aliases or Versions.
    Heading(&'static str),
    Pick(Choice),
    Blank,
    /// Leaves the scope; inert when that would leave the skill nowhere.
    Unpin {
        inert: bool,
    },
}

impl PickerRow {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Pick(_) | Self::Unpin { inert: false })
    }
}

/// `latest`, the named aliases by name, every version newest first, then leaving the scope.
pub fn rows(state: &State, page: &Page, scope: SkillScope) -> Vec<PickerRow> {
    let mut aliases: Vec<&String> = page
        .versions
        .iter()
        .flat_map(|version| &version.aliases)
        .filter(|alias| *alias != LATEST)
        .collect();
    aliases.sort();
    aliases.dedup();
    let mut versions: Vec<i64> = page.versions.iter().map(|v| v.version).collect();
    versions.sort_unstable_by(|a, b| b.cmp(a));
    let alias = |name: &str| PickerRow::Pick(Choice::Alias(name.to_owned()));
    let mut rows = vec![PickerRow::Heading(ALIASES), alias(LATEST)];
    rows.extend(aliases.into_iter().map(|name| alias(name)));
    rows.extend([PickerRow::Blank, PickerRow::Heading(VERSIONS)]);
    rows.extend(
        versions
            .into_iter()
            .map(|v| PickerRow::Pick(Choice::Version(v))),
    );
    if scope_choice(state, page, scope).is_some() {
        let inert = sole_scope(state, page, scope);
        rows.extend([PickerRow::Blank, PickerRow::Unpin { inert }]);
    }
    rows
}

/// Open `scope`'s picker on the choice in effect, else on the other scope's, else on `latest`.
pub(super) fn open(app: &mut App, scope: SkillScope) {
    let state = &app.skills_browser;
    let Some(page) = state.page.as_ref() else {
        return;
    };
    let rows = rows(state, page, scope);
    let current =
        effective_choice(state, page, Some(scope)).unwrap_or(Choice::Alias(LATEST.to_owned()));
    let on_current = rows
        .iter()
        .position(|row| *row == PickerRow::Pick(current.clone()));
    let first = rows.iter().position(PickerRow::selectable);
    let list = OptionList::new(on_current.or(first).unwrap_or(0));
    if let Some(page) = app.skills_browser.page.as_mut() {
        page.picker = Some(Picker { scope, rows, list });
    }
}

/// New pins under the picker: its rows again, the highlight on the row it was on.
pub(super) fn refresh(picker: &mut Picker, rows: Vec<PickerRow>) {
    let kept = picker.rows.get(picker.list.selected()).cloned();
    picker.rows = rows;
    let index = kept.and_then(|kept| picker.rows.iter().position(|row| *row == kept));
    let first = picker.rows.iter().position(PickerRow::selectable);
    picker.list.highlight(index.or(first).unwrap_or(0));
}

/// Enter or a click on a row.
pub(super) fn activate(app: &mut App, client: &Arc<Client>, index: usize) {
    let Some(picker) = app
        .skills_browser
        .page
        .as_ref()
        .and_then(|p| p.picker.as_ref())
    else {
        return;
    };
    let scope = picker.scope;
    match picker.rows.get(index).cloned() {
        Some(PickerRow::Pick(choice)) => choose(app, client, scope, choice),
        Some(PickerRow::Unpin { inert: false }) => unpin(app, client, scope),
        _ => {}
    }
}

/// A choice for `scope`: kept until Install, or written at once when installed.
fn choose(app: &mut App, client: &Arc<Client>, scope: SkillScope, choice: Choice) {
    let Some(page) = app.skills_browser.page.as_mut() else {
        return;
    };
    match &mut page.subject {
        Subject::Registry {
            install: Install::Pending(choices),
            ..
        } => {
            choices.insert(scope, choice);
            page::back(app);
        }
        Subject::Registry { skill_id, .. } => {
            let change = changes::import(skill_id, scope, &choice);
            if changes::change(app, client, &labels::choosing(&choice), vec![change]) {
                page::back(app);
            }
        }
        Subject::Plain { .. } => {}
    }
}

/// Leave `scope`: drop it from the install, or unpin the skill from it.
fn unpin(app: &mut App, client: &Arc<Client>, scope: SkillScope) {
    let Some(page) = app.skills_browser.page.as_mut() else {
        return;
    };
    if let Subject::Registry {
        install: Install::Pending(choices),
        ..
    } = &mut page.subject
    {
        choices.remove(&scope);
        return page::back(app);
    }
    let name = page.subject.name().to_owned();
    let label = labels::unpinning(&name);
    let change = SkillChange::Remove { name, scope };
    if changes::change(app, client, &label, vec![change]) {
        page::back(app);
    }
}

/// The body under the picker: the highlighted version's, the one an alias points at, or on Unpin the one the scope pins.
pub fn previewed(state: &State, page: &Page) -> Option<BodyKey> {
    let picker = page.picker.as_ref()?;
    let choice = match picker.rows.get(picker.list.selected())? {
        PickerRow::Pick(choice) => choice.clone(),
        PickerRow::Unpin { .. } => scope_choice(state, page, picker.scope)?,
        PickerRow::Heading(_) | PickerRow::Blank => return None,
    };
    let version = model::version_of(&page.versions, &choice)?;
    let skill_id = page.subject.skill_id()?.to_owned();
    Some((skill_id, Some(version)))
}

pub(super) fn help(picker: &Picker) -> Vec<Hint> {
    let mut hints = navigate(picker.rows.iter().any(PickerRow::selectable));
    let verb = match picker.rows.get(picker.list.selected()) {
        Some(PickerRow::Pick(_)) => Some(hints::SELECT),
        Some(PickerRow::Unpin { inert: false }) => Some((key::ENTER, action::UNPIN)),
        _ => None,
    };
    hints.extend(verb);
    hints.push(hints::BACK);
    hints
}
