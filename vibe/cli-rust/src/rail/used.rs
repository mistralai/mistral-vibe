//! The rail's last use, by Ctrl+X or a chip click: its `Ctrl+X to navigate` hint stays hidden for a week after.

use crate::app::App;
use crate::utils::cache_store;

const SECTION: &str = "status_rail";
const KEY: &str = "last_used";
const HINT_HIDDEN_FOR_SECS: i64 = 7 * 24 * 60 * 60;

/// Read the last use saved by any earlier session; once, at process start.
pub fn load_use(app: &mut App) {
    app.rail.used_at = cache_store::read_int(SECTION, KEY);
    app.rail.saved_at = app.rail.used_at;
}

pub(super) fn mark_used(app: &mut App) {
    app.rail.used_at = Some(now());
}

/// Save a use the cache does not hold yet; only the event loop calls it, so tests never write the user's cache.
pub fn save_use(app: &mut App) {
    let Some(used_at) = app.rail.used_at.filter(|at| app.rail.saved_at != Some(*at)) else {
        return;
    };
    app.rail.saved_at = Some(used_at);
    cache_store::write_int(SECTION, KEY, used_at);
}

/// Whether the rail was used within the last week.
pub fn hint_hidden(app: &App) -> bool {
    app.rail
        .used_at
        .is_some_and(|at| now() - at < HINT_HIDDEN_FOR_SECS)
}

fn now() -> i64 {
    (crate::utils::now_ms() / 1000) as i64
}
