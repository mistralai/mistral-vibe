//! The footer's monthly usage gauge: read with the account at ready, re-read after turns and model switches.

use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;

use crate::app::App;
use crate::clock;
use crate::post_ready::{self, AccountReads};
use crate::server::Client;
use crate::ui::footer_gauge::Usage;

/// Turns closer together than this share one account read.
const REFRESH_EVERY: Duration = Duration::from_secs(60);
/// A failed read for a new model is asked again after this long.
const RETRY_AFTER: Duration = Duration::from_secs(5);

/// An account re-read's answer, for the model it was read under.
pub struct AccountRead {
    pub model: String,
    pub reads: Option<AccountReads>,
}

/// The active model without its thinking level, which keys the account reads:
/// the model picks the credential, its thinking level does not.
pub fn account_model(app: &App) -> &str {
    let model = app.session.startup_config.active_model.as_str();
    model
        .strip_suffix(']')
        .and_then(|model| model.rsplit_once('['))
        .map_or(model, |(name, _)| name)
}

/// The usage the footer gauges, while the account read describes the active model's credential.
pub fn footer_usage(app: &App) -> Option<Usage> {
    let reads = app.whoami.hit(account_model(app))?;
    usage(&reads.account)
}

/// Whether the active model's account is a Vibe key's chat plan, the one billing gauges.
fn vibe_key(app: &App) -> bool {
    app.whoami
        .hit(account_model(app))
        .and_then(|reads| reads.account.pointer("/plan/kind"))
        .and_then(Value::as_str)
        == Some("chat")
}

/// The monthly Vibe Code usage in an `account/read`; `None` without a budget.
pub fn usage(account: &Value) -> Option<Usage> {
    let budget = account.get("budget")?;
    Some(Usage {
        percent: budget.get("usagePercentage")?.as_f64()?,
        payg: budget.get("paygEnabled").and_then(Value::as_bool) == Some(true),
    })
}

/// Re-read a Vibe key's account after a turn, at most once a minute unless `force`d (a rate limit).
pub fn refresh(app: &mut App, client: &Arc<Client>, force: bool) {
    let recent = app
        .whoami
        .read_at()
        .is_some_and(|at| clock::real_now().duration_since(at) < REFRESH_EVERY);
    if vibe_key(app) && (force || !recent) {
        read(app, client);
    }
}

/// Re-read the account once the active model differs from the one it was read for (`/model`, a resume).
pub fn follow_model(app: &mut App, client: &Arc<Client>) {
    let model = account_model(app);
    if model.is_empty() || !app.whoami.has_asked() || app.whoami.asked_for(model) {
        return;
    }
    let retrying = app
        .whoami
        .failed_at()
        .is_some_and(|at| clock::real_now().duration_since(at) < RETRY_AFTER);
    if retrying {
        return;
    }
    read(app, client);
}

fn read(app: &mut App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let model = account_model(app).to_owned();
    app.whoami.mark_read(clock::real_now(), model.clone());
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let reads = post_ready::read(&client, &session_id).await;
        AccountRead { model, reads }
    });
}

/// Keep a successful re-read; a failed one, or one whose billing call failed, leaves the last known usage.
/// A failed read for a model not read yet is asked again shortly by `follow_model`.
pub fn apply(app: &mut App, read: AccountRead) {
    let Some(mut reads) = read.reads else {
        app.whoami.failed(&read.model, clock::real_now());
        return;
    };
    let previous = app
        .whoami
        .hit(&read.model)
        .and_then(|cached| cached.account.get("budget"))
        .filter(|budget| !budget.is_null())
        .cloned();
    if let (Some(budget), Some(account)) = (previous, reads.account.as_object_mut()) {
        if account.get("budget").is_none_or(Value::is_null) {
            account.insert("budget".into(), budget);
        }
    }
    app.whoami.store(read.model, reads);
}
