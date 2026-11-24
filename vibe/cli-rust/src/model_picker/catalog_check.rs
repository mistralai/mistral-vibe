//! Opening `/model` asks the server to refetch a catalog that locks a model: the user may have just upgraded.

use std::sync::Arc;

use serde_json::json;

use crate::app::App;
use crate::server::{method, Client};

/// Shown after the title while the check runs.
pub const CHECKING_LABEL: &str = "Checking for new models…";

/// Start the check when the last runtime listed a locked model. Its answer only clears the
/// checking line: the unlocked models arrive with `runtime/updated`.
pub fn start(app: &mut App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    if !app.model_picker.has_locked_models || app.events.is_none() {
        return;
    }
    app.model_picker.checks_in_flight += 1;
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({"sessionId": session_id, "refreshModelCatalog": true});
        if let Err(error) = client.request(method::CONFIG_READ, params).await {
            tracing::debug!(%error, "model catalog check failed");
        }
        super::Event::Checked
    });
}
