//! The `/skills` texts: column cells, page values and notes.

use super::model::{self, Choice};
use super::page::Page;
use super::pins::{scope_choice, scope_pin, taken};
use super::State;
use crate::server::{InstalledSkill, SkillCatalogEntry, SkillScope};
use crate::utils::text::capitalize;

pub const TITLE: &str = "Skills";
pub const PLACEHOLDER: &str = "Search skills";
pub const MY_SKILLS: &str = "My Skills";
pub const SHARED: &str = "Shared";
pub const NO_SKILLS: &str = "No skills yet";
pub const NO_MATCH: &str = "No skills found";
pub const ALIASES: &str = "Aliases";
pub const VERSIONS: &str = "Versions";
pub const EMPTY_BODY: &str = "(empty skill body)";
pub const LOADING_BODY: &str = "Loading preview…";
pub const BODY_FAILED: &str = "Could not load the skill body";
pub const BODY_MISSING: &str = "the registry did not return it";
pub const NO_SESSION: &str = "no session to ask";
pub const NOT_PINNED: &str = "not pinned";
pub const SHADOWED: &str = "(shadowed)";
pub const ALWAYS_NEWEST: &str = "always newest";
pub const NEWEST: &str = "(latest)";
pub const ENABLED: &str = "Enabled";
pub const REMOVE: &str = "Remove";
pub const ADD: &str = "Add to My Skills";
/// An armed removal's prompt around the key that confirms it, like `/resume`'s delete.
pub const REMOVE_AGAIN: (&str, &str) = ("Press ", " again to remove");
pub const PLUGIN_LOCK: &str = "Plugin skills are managed via /plugins.";
pub const BUSY: &str = "Wait for the current change to finish.";
pub const LOADING: &str = "Loading skills";

/// The Shared tab's note when it lists nothing.
pub fn shared_note(state: &State) -> &'static str {
    if state.catalog_pending {
        "Loading shared skills"
    } else if !state.catalog.authenticated() {
        "Sign in to Mistral to browse shared skills"
    } else if !state.catalog.loaded() {
        "Could not load the shared skills catalog"
    } else {
        "No shared skills to add"
    }
}

/// `yes` or `no`.
pub fn yes_no(on: bool) -> &'static str {
    if on {
        "yes"
    } else {
        "no"
    }
}

/// The spinner while a switch is in flight.
pub fn switching(enabled: bool, name: &str) -> String {
    let verb = if enabled { "Disabling" } else { "Enabling" };
    format!("{verb} {name}")
}

/// A picker's title: the skill and the scope it pins.
pub fn picker_title(name: &str, scope: SkillScope) -> String {
    format!("{name}  {} version", scope_label(scope))
}

/// The title of a page not in My Skills yet.
pub fn not_installed(name: &str) -> String {
    format!("{name}  not in My Skills")
}

/// The spinner while a page waits for its versions.
pub fn opening(name: &str) -> String {
    format!("Loading {name}")
}

/// The spinner while a skill is removed from every scope.
pub fn removing(name: &str) -> String {
    format!("Removing {name}")
}

/// The spinner while a skill is added to My Skills, one import per scope.
pub fn importing(name: &str) -> String {
    format!("Adding {name}")
}

/// The spinner while a scope takes a new pin.
pub fn choosing(choice: &Choice) -> String {
    match choice {
        Choice::Alias(alias) => format!("Tracking {alias}"),
        Choice::Version(version) => format!("Pinning v{version}"),
    }
}

/// The spinner while one scope is unpinned.
pub fn unpinning(name: &str) -> String {
    format!("Unpinning {name}")
}

/// The notice when a change failed, named as its spinner named it.
pub fn action_failed(what: &str, error: &str) -> String {
    format!("{what} failed: {error}")
}

/// The notice of pinned skills with a newer version.
pub fn updates(names: &[&str]) -> String {
    format!(
        "New version available for skill(s): {}. Run /skills to update.",
        names.join(", ")
    )
}

/// The error when the installed skills could not be read.
pub fn read_failed(error: &str) -> String {
    format!("Failed to read skills: {error}")
}

/// The notice when a page could not read its versions.
pub fn versions_failed(name: &str, error: &str) -> String {
    format!("Failed to read the versions of {name}: {error}")
}

/// Why Space cannot turn a locked skill on or off.
pub fn lock_reason(pin: &InstalledSkill) -> String {
    if pin.source == "plugin" {
        return PLUGIN_LOCK.to_owned();
    }
    format!(
        "'{}' is set by a config pattern; edit enabled_skills or disabled_skills to change it.",
        pin.name
    )
}

/// A pin's Scope, Source and Version cells; Scope only for local and registry pins.
pub fn cells(pin: &InstalledSkill) -> [String; 3] {
    let scoped = matches!(pin.source.as_str(), "local" | "registry");
    let scope = if scoped {
        capitalize(&pin.scope)
    } else {
        String::new()
    };
    [scope, source(&pin.source), pin_version(pin)]
}

fn source(value: &str) -> String {
    match value {
        "builtin" => "Built-in".to_owned(),
        "registry" => SHARED.to_owned(),
        _ => capitalize(value),
    }
}

/// `vN`, or `vN (alias)` for a pin tracking an alias; empty outside the registry.
pub fn pin_version(pin: &InstalledSkill) -> String {
    let Some(registry) = pin.registry_ref() else {
        return String::new();
    };
    match registry.tracked_alias() {
        Some(alias) => format!("v{} ({alias})", registry.version),
        None => format!("v{}", registry.version),
    }
}

/// How a catalog skill is shared: `Private`, `Workspace`, else `Shared`.
pub fn sharing(entry: &SkillCatalogEntry) -> String {
    match entry.sharing_scope.as_str() {
        "private" | "workspace" => capitalize(&entry.sharing_scope),
        _ => "Shared".to_owned(),
    }
}

/// A catalog skill's newest version, `vN`.
pub fn latest(entry: &SkillCatalogEntry) -> String {
    format!("v{}", entry.latest_version)
}

/// `vN  pinned`, or `alias → vN  tracking` with what the alias points at today.
pub fn choice_value(page: &Page, choice: &Choice) -> String {
    match choice {
        Choice::Version(version) => format!("v{version}  pinned"),
        Choice::Alias(alias) => match model::resolve(&page.versions, alias) {
            Some(version) => format!("{alias} → v{version}  tracking"),
            None => format!("{alias}  tracking"),
        },
    }
}

/// A scope row's value: what the scope pins, or will once installed, else that another skill of the name holds it.
pub fn scope_value(state: &State, page: &Page, scope: SkillScope) -> String {
    if taken(state, page, scope) {
        return format!("held by another skill named {}", page.subject.name());
    }
    scope_choice(state, page, scope).map_or_else(
        || NOT_PINNED.to_owned(),
        |choice| choice_value(page, &choice),
    )
}

/// After a scope's value: that the name loads another pin than this one, or what a free scope means.
pub fn scope_tail(state: &State, page: &Page, scope: SkillScope) -> Option<&'static str> {
    if taken(state, page, scope) {
        return None;
    }
    if scope_choice(state, page, scope).is_none() {
        return Some(blurb(scope));
    }
    let pin = scope_pin(state, page, scope);
    pin.filter(|pin| !pin.active).map(|_| SHADOWED)
}

/// What a scope means.
pub fn blurb(scope: SkillScope) -> &'static str {
    match scope {
        SkillScope::Global => "available in every project",
        SkillScope::Project => "committed to this project's .vibe/skills.toml",
    }
}

pub fn scope_label(scope: SkillScope) -> &'static str {
    match scope {
        SkillScope::Global => "Global",
        SkillScope::Project => "Project",
    }
}

/// The picker row that leaves `scope`: an unpin once installed, else dropping it from the install.
pub fn unpin(page: &Page, scope: SkillScope) -> String {
    if page.subject.installed() {
        format!("Unpin from {}", scope_label(scope))
    } else {
        "Don't pin here".to_owned()
    }
}

/// Why the picker's last scope cannot be left.
pub fn unpin_refused(page: &Page) -> &'static str {
    if page.subject.installed() {
        "the only pin; Remove deletes the skill"
    } else {
        "Add to My Skills needs a scope"
    }
}
