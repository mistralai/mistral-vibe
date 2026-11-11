//! One `/skills` change at a time, then the view on what is left.

use std::sync::Arc;

use super::labels::{self, lock_reason};
use super::list::{self, Row};
use super::model::{Choice, Install, Subject};
use super::pins::{listed, registry_pin, registry_pins};
use super::request::{self, Outcome};
use super::{loading, page, Event, State};
use crate::app::{App, Severity};
use crate::commands::spawn_session;
use crate::event_handler;
use crate::server::{Client, InstalledSkill, SkillChange, SkillScope};

/// Send `changes` as one change, in order; false when it was refused or could not start.
pub(super) fn change(
    app: &mut App,
    client: &Arc<Client>,
    label: &str,
    changes: Vec<SkillChange>,
) -> bool {
    if app.skills_browser.busy.is_some() {
        crate::notices::inline(app, Severity::Warning, labels::BUSY);
        return false;
    }
    let started = spawn_session(app, client, move |client, session_id| async move {
        let (outcome, installed) = request::change(&client, &session_id, &changes).await;
        Event::Changed { outcome, installed }
    });
    if started {
        app.skills_browser.busy = Some(label.to_owned());
        loading(app, label);
    }
    started
}

/// Turn `pin`'s skill on or off everywhere it is installed, unless a pattern or plugin decides it.
pub(super) fn toggle(app: &mut App, client: &Arc<Client>, pin: &InstalledSkill) {
    if pin.locked {
        return crate::notices::inline(app, Severity::Warning, &lock_reason(pin));
    }
    let label = labels::switching(pin.enabled, &pin.name);
    let enabled = !pin.enabled;
    let name = pin.name.clone();
    change(
        app,
        client,
        &label,
        vec![SkillChange::SetEnabled { name, enabled }],
    );
}

/// Remove the shared skill `skill_id` under `name`: unpin it from every scope it is pinned in, never a same-named skill's pin.
pub(super) fn remove_everywhere(app: &mut App, client: &Arc<Client>, name: &str, skill_id: &str) {
    let state = &app.skills_browser;
    let changes: Vec<SkillChange> = SkillScope::ALL
        .into_iter()
        .filter(|scope| registry_pin(state, name, skill_id, *scope).is_some())
        .map(|scope| SkillChange::Remove {
            name: name.to_owned(),
            scope,
        })
        .collect();
    if !changes.is_empty() {
        change(app, client, &labels::removing(name), changes);
    }
}

/// Import into every scope chosen for the install.
pub(super) fn install(app: &mut App, client: &Arc<Client>) {
    let Some(page) = app.skills_browser.page.as_ref() else {
        return;
    };
    let (Some(skill_id), Some(choices)) = (page.subject.skill_id(), page.subject.pending()) else {
        return;
    };
    let changes: Vec<SkillChange> = choices
        .iter()
        .map(|(scope, choice)| import(skill_id, *scope, choice))
        .collect();
    let label = labels::importing(page.subject.name());
    change(app, client, &label, changes);
}

/// Pin `choice` in `scope`, replacing any pin already there.
pub(super) fn import(skill_id: &str, scope: SkillScope, choice: &Choice) -> SkillChange {
    let (version, alias) = match choice {
        Choice::Version(version) => (Some(*version), None),
        Choice::Alias(alias) => (None, Some(alias.clone())),
    };
    SkillChange::Import {
        skill_id: skill_id.to_owned(),
        version,
        alias,
        scope,
    }
}

/// A change answered: apply the last runtime it got back and report its failure, named by the label its spinner showed; an open browser also shows the re-read pins, or why they could not be read.
pub(super) fn changed(
    app: &mut App,
    outcome: Outcome,
    installed: Result<Vec<InstalledSkill>, String>,
) {
    let label = app.skills_browser.busy.take().unwrap_or_default();
    if let Some(answer) = &outcome.answer {
        event_handler::apply_response_runtime(app, answer);
    }
    let state = &mut app.skills_browser;
    let mut reread = None;
    if state.open {
        match installed {
            Ok(pins) => state.installed = pins,
            Err(error) => reread = Some(error),
        }
        follow(state);
    }
    if let Some(error) = outcome.error {
        crate::notices::inline(app, Severity::Error, &labels::action_failed(&label, &error));
    } else if let Some(error) = reread {
        crate::notices::inline(app, Severity::Error, &labels::read_failed(&error));
    }
}

/// Keep a page while its skill is installed, turning an install's page into the installed one; else back to the list.
fn follow(state: &mut State) {
    list::refresh(state);
    let Some(page) = state.page.as_ref() else {
        return;
    };
    let name = page.subject.name().to_owned();
    let installed = match &page.subject {
        Subject::Registry { skill_id, .. } => {
            registry_pins(state, &name, skill_id).next().is_some()
        }
        Subject::Plain { .. } => listed(state, &name).is_some(),
    };
    let pinned_row = listed(state, &name).is_some_and(|pin| pin.registry_ref().is_some());
    let Some(page) = state.page.as_mut() else {
        return;
    };
    if !installed && page.subject.installed() {
        let from = page.from.clone();
        state.page = None;
        return list::focus(state, &from);
    }
    if let Subject::Registry { install, .. } = &mut page.subject {
        if installed && matches!(install, Install::Pending(_)) {
            *install = Install::Done;
            if pinned_row {
                page.from = Row::Installed(name);
            }
        }
    }
    page::refresh(state);
}
