//! Version changes between runs, stamped in `cache.toml`.

use serde_json::json;

use crate::app::App;
use crate::utils::cache_store;

const SECTION: &str = "telemetry";
const LAST_RUN_VERSION: &str = "last_run_version";

/// Send `vibe.version.updated` when this run's version differs from the last run's; a first run only stamps it.
pub fn record_version_update(app: &App) {
    let Some(from_version) = stamp_version() else {
        return;
    };
    let tx = app.telemetry_tx.clone();
    crate::work::spawn(async move {
        // This install's own manager: the update cache may hold another install's.
        let install_source = if crate::utils::is_replaying() {
            None
        } else {
            Some(
                crate::update_notifier::gateway::install_source()
                    .await
                    .as_str(),
            )
        };
        super::enqueue(
            tx.as_ref(),
            super::event::VERSION_UPDATED,
            super::bmap(json!({
                "from_version": from_version,
                "to_version": env!("CARGO_PKG_VERSION"),
                "install_source": install_source,
            })),
        );
    });
}

/// Stamp this run's version; the last run's when it differs.
fn stamp_version() -> Option<String> {
    let current = env!("CARGO_PKG_VERSION");
    let last = cache_store::read_string(SECTION, LAST_RUN_VERSION);
    if last.as_deref() == Some(current) {
        return None;
    }
    cache_store::write_string(SECTION, LAST_RUN_VERSION, current);
    last
}
