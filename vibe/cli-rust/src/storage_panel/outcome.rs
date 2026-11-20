//! What a server-side delete or cleanup answer becomes: the kept row and the panel's numbers.

use super::actions::plural;
use crate::app::App;
use crate::server::StorageSnapshot;

/// `Kept 2 items: in use (target, tmp), already gone (old-spike).`: each kept `(name, why)` grouped
/// by reason, in the order the server listed them; `None` when nothing was kept.
pub fn kept_row(kept: &[(String, &str)], noun: (&str, &str)) -> Option<String> {
    if kept.is_empty() {
        return None;
    }
    let mut groups: Vec<(&str, Vec<&str>)> = Vec::new();
    for (name, why) in kept {
        match groups.iter_mut().find(|(r, _)| r == why) {
            Some((_, names)) => names.push(name),
            None => groups.push((why, vec![name])),
        }
    }
    let groups: Vec<String> = groups
        .into_iter()
        .map(|(why, names)| format!("{why} ({})", names.join(", ")))
        .collect();
    let count = plural(kept.len(), noun.0, noun.1);
    Some(format!("Kept {count}: {}.", groups.join(", ")))
}

/// Show an answer at once on the snapshot the action started from (`measured_at`), until the
/// refresh the server started replaces it; a newer snapshot, measured after it, is kept as it is.
pub fn apply_answer(app: &mut App, measured_at: u64, apply: impl FnOnce(&mut StorageSnapshot)) {
    let mut usage = app.storage.usage.clone();
    let Some(snapshot) = usage
        .snapshot
        .as_mut()
        .filter(|s| s.measured_at <= measured_at)
    else {
        return;
    };
    apply(snapshot);
    super::replace(app, usage);
}
