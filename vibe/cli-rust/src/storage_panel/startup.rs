//! The startup low-disk notice, once per low-disk episode.

use std::sync::Arc;

use super::{report, Event};
use crate::app::App;
use crate::server::{method, Client, StorageRefreshParams, StorageUsage};
use crate::utils::{bytes, cache_store};

const SECTION: &str = "storage_low_disk_notice";
const KEY: &str = "shown";

/// What the startup read decided, the warn-once cache already updated off the UI thread.
pub enum Notice {
    Show(String),
    Cleared,
}

/// Read the disk once the session is ready, and warm the numbers `/storage` shows:
/// a background refresh the server skips while they are fresh, and the panel's own
/// refresh joins while it runs. An older server stays silent.
pub fn read(app: &App, client: &Arc<Client>) {
    if app.events.is_none() || !app.storage.enabled {
        return;
    }
    let params = StorageRefreshParams {
        max_age_ms: Some(super::MAX_AGE_MS),
        ..Default::default()
    };
    super::refresh(app, client, params);
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let response = client
            .request(method::STORAGE_USAGE_READ, serde_json::json!({}))
            .await;
        let usage: StorageUsage = response
            .map(|value| crate::utils::json::read(&value))
            .unwrap_or_default();
        Event::Startup(decide(&usage))
    });
}

/// Show once while the disk stays low; a startup above the threshold re-arms it.
fn decide(usage: &StorageUsage) -> Option<Notice> {
    let disk = &usage.disk;
    if disk.total_bytes == 0 {
        return None;
    }
    let shown = cache_store::read_int(SECTION, KEY).unwrap_or(0) != 0;
    if disk.is_low == shown {
        return None;
    }
    cache_store::write_int(SECTION, KEY, i64::from(disk.is_low));
    Some(match disk.is_low {
        true => Notice::Show(notice(disk.free_bytes, disk.total_bytes)),
        false => Notice::Cleared,
    })
}

pub(super) fn apply(app: &mut App, notice: Option<Notice>) {
    match notice {
        Some(Notice::Show(text)) => {
            crate::notices::warn(app, &text);
            report::low_disk(app, "shown");
        }
        Some(Notice::Cleared) => report::low_disk(app, "cleared"),
        None => {}
    }
}

pub fn notice(free: u64, total: u64) -> String {
    format!(
        "Low disk space: {} free of {}. Run /storage to see what Vibe uses.",
        bytes::decimal(free),
        bytes::decimal(total)
    )
}
