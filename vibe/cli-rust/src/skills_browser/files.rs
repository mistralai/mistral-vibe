//! What the keys do on the shown skill, on the list or its page: switch it, add, remove or delete it, open its file, or ask the agent for a new one.

use std::path::PathBuf;
use std::sync::Arc;

use super::labels::{self, NEW_SKILL};
use super::list::{highlighted, highlighted_pin, switch, Row};
use super::model::{Choice, LATEST};
use super::pins::{importable, page_pin, start_scope};
use super::request;
use super::{changes, close, Event, State};
use crate::app::{App, Severity};
use crate::commands::spawn_session;
use crate::hints::{action, key, Hint};
use crate::server::{Client, InstalledSkill, SkillChange};

/// A local skill's deletion, waiting for the user to confirm it; Delete is focused only once the user moves to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Confirm {
    pub name: String,
    pub path: String,
    pub linked: bool,
    pub delete_focused: bool,
}

/// The installed skill the screen is about: the page's, else the highlighted row's.
pub fn shown_pin(state: &State) -> Option<&InstalledSkill> {
    match &state.page {
        Some(page) => page_pin(state, page),
        None => highlighted_pin(state),
    }
}

/// The shared skill the list highlights and the user has not added yet.
fn addable(state: &State) -> Option<String> {
    if state.page.is_some() {
        return None;
    }
    let Some(Row::Catalog(skill_id)) = highlighted(state) else {
        return None;
    };
    let pinned = state
        .installed
        .iter()
        .any(|pin| pin.registry_id() == Some(skill_id));
    let listed = importable(state).any(|entry| entry.skill_id == *skill_id);
    (listed && !pinned).then(|| skill_id.clone())
}

/// Space: turn the shown skill on or off everywhere it is installed, when its name loads it.
pub(super) fn toggle(app: &mut App, client: &Arc<Client>) {
    let pin = shown_pin(&app.skills_browser).filter(|pin| pin.active);
    if let Some(pin) = pin.cloned() {
        changes::toggle(app, client, &pin);
    }
}

/// `a`: add the highlighted shared skill to My Skills, tracking its newest version where it starts.
pub(super) fn add(app: &mut App, client: &Arc<Client>) {
    let state = &app.skills_browser;
    let Some(skill_id) = addable(state) else {
        return;
    };
    let Some(entry) = state.catalog.entry(&skill_id) else {
        return;
    };
    let change = changes::import(
        &skill_id,
        start_scope(state, entry),
        &Choice::Alias(LATEST.to_owned()),
    );
    let label = labels::importing(&entry.name);
    changes::change(app, client, &label, vec![change]);
}

/// `r`: unpin the shown shared skill from every scope at once; adding it back from Shared restores it.
pub(super) fn remove(app: &mut App, client: &Arc<Client>) {
    let Some(pin) = shown_pin(&app.skills_browser).cloned() else {
        return;
    };
    let Some(skill_id) = pin.registry_id() else {
        return;
    };
    if changes::remove_everywhere(app, client, &pin.name, skill_id) {
        app.skills_browser.done = Some(labels::removed(&pin.name));
    }
}

/// `x`: ask to confirm deleting the shown local skill's folder.
pub(super) fn ask_delete(app: &mut App) {
    let state = &mut app.skills_browser;
    let Some(pin) = shown_pin(state) else {
        return;
    };
    let Some(path) = pin.local_file() else {
        return;
    };
    state.confirm = Some(Confirm {
        name: pin.name.clone(),
        path: path.to_owned(),
        linked: pin.linked,
        delete_focused: false,
    });
}

/// Enter on the confirmation: delete when Delete is focused, else just close it; a refused delete keeps it open.
pub(super) fn confirm(app: &mut App, client: &Arc<Client>) {
    let Some(confirm) = app.skills_browser.confirm.take() else {
        return;
    };
    if !confirm.delete_focused {
        return;
    }
    let change = SkillChange::DeleteLocal {
        path: confirm.path.clone(),
    };
    let label = labels::deleting(&confirm.name);
    if !changes::change(app, client, &label, vec![change]) {
        app.skills_browser.confirm = Some(confirm);
    }
}

/// `e`: open the shown local skill's `SKILL.md` in the external editor; the folder watch picks up what is saved.
pub(super) fn edit(app: &mut App) {
    let path = shown_pin(&app.skills_browser).and_then(InstalledSkill::local_file);
    app.external_editor_file = path.map(PathBuf::from);
}

/// A skill's file came back from the editor: read the installed pins again so the list and the open page show what was saved.
pub fn reread(app: &mut App, client: &Arc<Client>) {
    if !app.skills_browser.open {
        return;
    }
    spawn_session(app, client, |client, session_id| async move {
        Event::Reread(request::installed(&client, &session_id).await)
    });
}

/// `n`: close the browser and leave the skill-creator command in the composer, for the user to say what the skill does.
pub(super) fn create(app: &mut App) {
    if !app.chat_input.submitted_text().trim().is_empty() {
        return crate::notices::inline(app, Severity::Warning, labels::DRAFT_KEPT);
    }
    close(app);
    crate::long_paste::load_collapsed(app, NEW_SKILL.to_owned());
    crate::completion_manager::input_changed(app);
}

/// The keys the shown skill takes, in the order the footer lists them: switch, add, edit, remove, delete.
pub fn pin_hints(state: &State) -> Vec<Hint> {
    let mut hints = Vec::new();
    if let Some(pin) = shown_pin(state) {
        if pin.active && !pin.locked {
            hints.push((key::SPACE, switch(pin.enabled)));
        }
    }
    if addable(state).is_some() {
        hints.push(("a", action::ADD));
    }
    let Some(pin) = shown_pin(state) else {
        return hints;
    };
    if pin.local_file().is_some() {
        hints.push(("e", action::EDIT));
        hints.push(("x", action::DELETE));
    }
    if pin.registry_id().is_some() {
        hints.push(("r", action::REMOVE));
    }
    hints
}
