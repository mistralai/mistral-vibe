//! Lookups over the installed pins and the catalog.

use std::collections::BTreeMap;

use caseless::default_case_fold_str as casefold;

use super::model::Choice;
use super::page::Page;
use super::State;
use crate::server::{InstalledSkill, SkillCatalogEntry, SkillScope};

/// The pin a row shows for `name`: the one the agent resolves it to, else its first when none is, as when another pin loads the same stored version under another name.
pub fn listed<'a>(state: &'a State, name: &str) -> Option<&'a InstalledSkill> {
    loaded_or_first(state.installed.iter().filter(move |pin| pin.name == name))
}

/// The pin among `pins` the agent loads, else the first one: the server marks the loaded pin `active` and lists pins in the order names resolve, so the client never ranks them.
fn loaded_or_first<'a>(
    pins: impl Iterator<Item = &'a InstalledSkill> + Clone,
) -> Option<&'a InstalledSkill> {
    pins.clone()
        .find(|pin| pin.active)
        .or_else(|| pins.clone().next())
}

/// The pins of the shared skill `skill_id` under `name`; another shared skill of the same name keeps its own.
pub fn registry_pins<'a: 'k, 'k>(
    state: &'a State,
    name: &'k str,
    skill_id: &'k str,
) -> impl Iterator<Item = &'a InstalledSkill> + Clone + 'k {
    let named =
        move |pin: &&InstalledSkill| pin.name == name && pin.registry_id() == Some(skill_id);
    state.installed.iter().filter(named)
}

/// The pin of the shared skill `skill_id` under `name` in `scope`.
pub fn registry_pin<'a>(
    state: &'a State,
    name: &str,
    skill_id: &str,
    scope: SkillScope,
) -> Option<&'a InstalledSkill> {
    registry_pins(state, name, skill_id).find(|pin| pin.in_scope(scope))
}

/// A shared skill's page's own pin in `scope`, never a same-named skill's.
pub fn scope_pin<'a>(
    state: &'a State,
    page: &Page,
    scope: SkillScope,
) -> Option<&'a InstalledSkill> {
    registry_pin(state, page.subject.name(), page.subject.skill_id()?, scope)
}

/// A registry pin of the shared skill `skill_id`: the one the agent loads, else the first the server lists.
pub fn of_skill<'a>(state: &'a State, skill_id: &str) -> Option<&'a InstalledSkill> {
    loaded_or_first(
        state
            .installed
            .iter()
            .filter(move |pin| pin.registry_id() == Some(skill_id)),
    )
}

/// Whether the page's skill is the one its name loads: the Enabled switch acts on a name, so only that skill's page offers it.
pub fn in_use(state: &State, page: &Page) -> bool {
    let name = page.subject.name();
    match page.subject.skill_id() {
        Some(skill_id) => registry_pins(state, name, skill_id).any(|pin| pin.active),
        None => listed(state, name).is_some_and(|pin| pin.active),
    }
}

/// The pin a page shows: the shared skill's pin the agent loads, else the first the server lists; else whatever the name resolves to.
pub fn page_pin<'a>(state: &'a State, page: &Page) -> Option<&'a InstalledSkill> {
    let subject = &page.subject;
    if !subject.installed() {
        return None;
    }
    match subject.skill_id() {
        Some(skill_id) => loaded_or_first(registry_pins(state, subject.name(), skill_id)),
        None => listed(state, subject.name()),
    }
}

/// What `scope` pins on a page: the installed pin's choice, or the one waiting for Install.
pub fn scope_choice(state: &State, page: &Page, scope: SkillScope) -> Option<Choice> {
    match page.subject.pending() {
        Some(choices) => choices.get(&scope).cloned(),
        None => scope_pin(state, page, scope).and_then(Choice::of),
    }
}

/// The choice that decides `scope`'s version: its own, else the one of the pin the page shows; before Install, the other scope's pending one.
pub fn effective_choice(state: &State, page: &Page, scope: Option<SkillScope>) -> Option<Choice> {
    let own = scope.and_then(|scope| scope_choice(state, page, scope));
    own.or_else(|| match page.subject.pending() {
        Some(choices) => pending_choice(choices, scope),
        None => page_pin(state, page).and_then(Choice::of),
    })
}

/// A pending install's choice other than `scope`'s; with no scope, the project's first, the scope a later pin of the name would load from.
fn pending_choice(
    choices: &BTreeMap<SkillScope, Choice>,
    scope: Option<SkillScope>,
) -> Option<Choice> {
    choices
        .iter()
        .rev()
        .find(|(pending, _)| Some(**pending) != scope)
        .map(|(_, choice)| choice.clone())
}

/// The scopes a page lists: Global, and Project when there is one or the skill is pinned there.
pub fn page_scopes<'a>(state: &'a State, page: &'a Page) -> impl Iterator<Item = SkillScope> + 'a {
    SkillScope::ALL.into_iter().filter(move |scope| {
        *scope == SkillScope::Global
            || state.catalog.project_available()
            || scope_pin(state, page, *scope).is_some()
    })
}

/// Whether `scope` holds the page's only pin, so leaving it would leave the skill nowhere.
pub fn sole_scope(state: &State, page: &Page, scope: SkillScope) -> bool {
    let chosen = |scope: SkillScope| scope_choice(state, page, scope).is_some();
    chosen(scope)
        && SkillScope::ALL
            .iter()
            .all(|other| *other == scope || !chosen(*other))
}

/// A number-pinned registry skill whose catalog has a newer version; alias pins track instead.
pub fn update(state: &State, pin: &InstalledSkill) -> Option<i64> {
    let registry = pin.registry_ref()?;
    if registry.tracked_alias().is_some() {
        return None;
    }
    let latest = state.catalog.update(&pin.name)?;
    (latest > registry.version).then_some(latest)
}

/// Catalog entries listed under Shared: a project pin hides one, and so does a global pin without a project, unless every pin of it is shadowed.
pub fn importable(state: &State) -> impl Iterator<Item = &SkillCatalogEntry> + '_ {
    let catalog = &state.catalog;
    let blocking =
        |pin: &&InstalledSkill| pin.in_scope(SkillScope::Project) || !catalog.project_available();
    catalog.entries().iter().filter(move |entry| {
        all_shadowed(state, entry)
            || !state.installed.iter().filter(blocking).any(|pin| {
                pin.name == entry.name || pin.registry_id() == Some(entry.skill_id.as_str())
            })
    })
}

/// Where a shared skill not added yet starts: Global, unless another shared skill of its name is pinned there and the project can take it, since a scope pins one skill per name.
pub fn start_scope(state: &State, entry: &SkillCatalogEntry) -> SkillScope {
    let taken = other_pinned(state, &entry.name, &entry.skill_id, SkillScope::Global);
    match taken && state.catalog.project_available() {
        true => SkillScope::Project,
        false => SkillScope::Global,
    }
}

/// Whether another shared skill of the page's name is pinned in `scope`, which then cannot pin the page's skill.
pub fn taken(state: &State, page: &Page, scope: SkillScope) -> bool {
    let Some(skill_id) = page.subject.skill_id() else {
        return false;
    };
    scope_choice(state, page, scope).is_none()
        && other_pinned(state, page.subject.name(), skill_id, scope)
}

/// Whether a shared skill other than `skill_id` is pinned under `name` in `scope`.
fn other_pinned(state: &State, name: &str, skill_id: &str, scope: SkillScope) -> bool {
    state.installed.iter().any(|pin| {
        pin.name == name
            && pin.in_scope(scope)
            && pin.registry_id().is_some_and(|id| id != skill_id)
    })
}

/// Every pin of the shared skill is hidden behind another skill of its name, so its Shared row is the way to its page.
fn all_shadowed(state: &State, entry: &SkillCatalogEntry) -> bool {
    let shared = |pin: &&InstalledSkill| pin.registry_id() == Some(entry.skill_id.as_str());
    let mut pins = state.installed.iter().filter(shared).peekable();
    pins.peek().is_some() && pins.all(|pin| listed(state, &pin.name) != Some(pin))
}

/// A case-insensitive substring of the name or the description, against an already folded query.
pub fn matches(query: &str, name: &str, description: &str) -> bool {
    query.is_empty() || casefold(name).contains(query) || casefold(description).contains(query)
}
