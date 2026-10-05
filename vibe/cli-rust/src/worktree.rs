//! The session worktree, tracked from the `worktree` transcript effect.

use serde_json::Value;

use crate::app::App;
use crate::server::{
    EffectEntry, HistoryEntry, PublicSession, PublicSessionState, PublicSessionWorktree,
};

/// A managed worktree the app-server moved the session into, reported by the
/// settled `worktree` effect (Python `WorktreeEffect`). `created` is the
/// effect's verdict for the run that created it; a resumed session's
/// restored projection also reports true, and the exit cleanup gate
/// additionally requires `--worktree` to have been requested this run.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorktreeInfo {
    pub name: String,
    pub branch: String,
    pub path: String,
    /// True only when this run created it; reused worktrees are never
    /// auto-cleaned (Python `_run_cli_with_worktree_cleanup`).
    pub created: bool,
}

impl WorktreeInfo {
    /// A transcript history entry's resolved worktree, when it is a settled
    /// `worktree` effect.
    pub fn from_entry(entry: &Value) -> Option<Self> {
        match HistoryEntry::from_value(entry) {
            HistoryEntry::Effect(effect) => Self::from_effect(&effect),
            _ => None,
        }
    }

    /// The latest settled worktree effect in a state's history page.
    pub fn from_state(state: &PublicSessionState) -> Option<Self> {
        state
            .history
            .as_ref()?
            .iter()
            .rev()
            .find_map(Self::from_entry)
    }

    /// The session state's reported worktree (Python `PublicSessionWorktree`).
    /// The additive field is the only source a session that has not run a
    /// turn has: the transcript effect only lands with the deferred first
    /// turn, while the state update rides the session move itself.
    pub fn from_session(session: &PublicSession) -> Option<Self> {
        let worktree = session.worktree.as_ref()?;
        Some(Self {
            name: worktree.name.clone(),
            branch: worktree.branch.clone(),
            path: worktree.path.clone(),
            created: worktree.created,
        })
    }

    /// Parse a `worktree` effect (Python `WorktreeEffect`): `detail.input`
    /// carries the name, branch, and path, and the settled verb says Created
    /// vs Reused. Only a completed effect resolves: while it runs the call
    /// display hardcodes `settledVerb: Created` (`WorktreeProgress`), and a
    /// failed one never moved the session.
    fn from_effect(effect: &EffectEntry) -> Option<Self> {
        if effect.status() != Some("completed") || effect.kind() != Some("worktree") {
            return None;
        }
        let input = effect.input()?;
        // `display()` prefers the settled state, whose verb is Created/Reused;
        // the call display instead carries it as `settledVerb`.
        let display = effect.display()?;
        let created = match display.settled_verb.as_deref().unwrap_or(&display.verb) {
            "Created" => true,
            "Reused" => false,
            _ => return None,
        };
        Some(Self {
            name: input.get("name")?.as_str()?.to_owned(),
            branch: input.get("branch")?.as_str()?.to_owned(),
            path: input.get("path")?.as_str()?.to_owned(),
            created,
        })
    }
}

/// Track the session's worktree from a `history/entryAdded` payload.
pub fn track_entry(app: &mut App, params: &Value) {
    if let Some(info) = params.get("entry").and_then(WorktreeInfo::from_entry) {
        track(app, info);
    }
}

/// Track the session's worktree from a snapshot's history page, falling back
/// to the session state's reported worktree when the page misses the effect.
/// A page miss never drops a tracked worktree (the server keeps the last
/// `HISTORY_LIMIT` entries and the start-of-session effect is the first, so
/// long sessions legitimately lose it) and never moves the cwd backwards:
/// the session's own cwd is the move's authoritative value, applied after the
/// worktree so a root-only worktree path cannot shadow it.
pub fn track_state(app: &mut App, state: &PublicSessionState) {
    let info =
        WorktreeInfo::from_state(state).or_else(|| WorktreeInfo::from_session(&state.session));
    if let Some(info) = info {
        track(app, info);
        // Applied inside the worktree arm only, never on its own: a snapshot
        // from before the move (session still in the checkout, no worktree
        // in the history page) must not drag the footer back.
        if let Some(cwd) = &state.session.cwd {
            app.session.cwd = Some(cwd.clone());
        }
    }
}

/// A `session/updated` JSON patch's `replace` ops as (path, value): the shape
/// every worktree move arrives in, with a missing value read as null.
pub fn replace_ops(ops: &Value) -> impl Iterator<Item = (&str, &Value)> {
    ops.as_array()
        .into_iter()
        .flatten()
        .filter(|op| op.get("op") == Some(&Value::String("replace".into())))
        .filter_map(|op| {
            op.get("path")
                .and_then(Value::as_str)
                .map(|path| (path, op.get("value").unwrap_or(&Value::Null)))
        })
}

/// Apply a `session/updated` patch: the move to the worktree arrives as
/// `/cwd` and `/worktree` replaces on one notification, so a session quit
/// before its first turn still follows the move and knows its worktree.
///
/// The `/cwd` value is the move's authoritative session directory — the
/// worktree root plus the subdirectory the session runs in — while
/// `/worktree.path` is the root alone, so the cwd applies after the worktree
/// tracking rather than being clobbered by it.
pub fn track_patch(app: &mut App, patch: &Value) {
    let mut cwd: Option<String> = None;
    for (path, value) in replace_ops(patch) {
        match path {
            "/cwd" => {
                if let Some(value) = value.as_str() {
                    cwd = Some(value.to_owned());
                }
            }
            "/worktree" => {
                if let Ok(worktree) = serde_json::from_value::<PublicSessionWorktree>(value.clone())
                {
                    track(
                        app,
                        WorktreeInfo {
                            name: worktree.name,
                            branch: worktree.branch,
                            path: worktree.path,
                            created: worktree.created,
                        },
                    );
                }
            }
            _ => {}
        }
    }
    if let Some(cwd) = cwd {
        app.session.cwd = Some(cwd);
    }
}

/// Follow the server-side move: the session now runs in the worktree, so the
/// client's notion of its cwd follows (Python chdirs the whole process; here
/// the server moved the session). The worktree's path is the root, so this is
/// the fallback; a snapshot's session cwd or the announce's `/cwd` value
/// refines it to the subdirectory the session actually runs in.
pub fn track(app: &mut App, info: WorktreeInfo) {
    if app.session.worktree.as_ref() == Some(&info) {
        return;
    }
    tracing::info!(
        worktree = info.name,
        branch = info.branch,
        "session worktree tracked"
    );
    app.session.cwd = Some(info.path.clone());
    app.session.worktree = Some(info);
}
