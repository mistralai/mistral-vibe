//! Trust-store round-trips, answered on the main thread as `CommandEvent::TrustedFolders`.

use std::sync::Arc;

use serde::de::DeserializeOwned;
use serde_json::Value;

use super::Event;
use crate::app::App;
use crate::server::request_error::describe;
use crate::server::{Client, WorkspaceTrustFolders};

pub type Answer<T = WorkspaceTrustFolders> = Result<T, String>;

/// Send `method` off the main thread and deliver its parsed answer through `wrap`; false without an event channel.
pub(super) fn spawn<T: DeserializeOwned + Send + 'static>(
    app: &App,
    client: &Arc<Client>,
    method: &'static str,
    params: Value,
    wrap: impl FnOnce(Answer<T>) -> Event + Send + 'static,
) -> bool {
    let Some(tx) = app.command_tx.clone() else {
        return false;
    };
    let client = client.clone();
    crate::work::spawn(async move {
        let answer = match client.request(method, params).await {
            Ok(value) => serde_json::from_value(value).map_err(|error| error.to_string()),
            Err(error) => Err(describe(&error)),
        };
        crate::input::deliver(Some(tx), wrap(answer)).await;
    });
    true
}
