//! Post-ready startup work: the account plan and the greeting (Python
//! `_complete_post_ready_startup`).

use std::sync::Arc;

use serde_json::{json, Value};

use crate::app::App;
use crate::commands::CommandEvent;
use crate::server::{method, Client};
use crate::utils::greeting_cache;

/// The `identity` and `account` objects behind the banner and `/whoami`.
#[derive(Clone, Default)]
pub struct AccountReads {
    pub identity: Value,
    pub account: Value,
}

/// Read identity and account together; `None` when either call fails, so a
/// failed read is never cached as an answer.
pub async fn read(client: &Arc<Client>, session_id: &str) -> Option<AccountReads> {
    let params = json!({"sessionId": session_id});
    let (identity, account) = tokio::join!(
        client.request(method::IDENTITY_READ, params.clone()),
        client.request(method::ACCOUNT_READ, params),
    );
    Some(AccountReads {
        identity: field(identity.ok()?, "identity"),
        account: field(account.ok()?, "account"),
    })
}

fn field(response: Value, key: &str) -> Value {
    response.get(key).cloned().unwrap_or(Value::Null)
}

/// The plan label the banner appends to its version line.
pub fn plan_title(account: &Value) -> Option<String> {
    account
        .pointer("/plan/title")
        .and_then(Value::as_str)
        .map(str::to_owned)
}

/// Fetch the account and identity once the session is ready, then hand the
/// banner its plan title and greeting. Holds the app busy until it lands, so a
/// settled frame never misses them.
pub fn fetch(app: &mut App, client: &Arc<Client>, show_greeting: bool) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let reads = read(&client, &session_id).await;
        let greeting = reads
            .as_ref()
            .filter(|_| show_greeting)
            .and_then(|reads| greeting(&reads.identity))
            .filter(|_| greeting_cache::should_show());
        if greeting.is_some() {
            greeting_cache::mark_shown();
        }
        let _ = tx.try_send(CommandEvent::PostReady { reads, greeting });
    });
}

/// Python `GreetingMessage`, shown only when identity exposes a first name.
fn greeting(identity: &Value) -> Option<String> {
    let username = identity
        .get("firstName")
        .and_then(Value::as_str)
        .filter(|username| !username.is_empty())?;
    Some(format!("Hello {username}, how can I help you?"))
}
