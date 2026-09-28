//! `/mcp` subcommands (Python `mcp_commands.py` and `VibeApp._mcp_*`).

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use crate::server::method;
use crate::server::Client;
use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;

use crate::app::App;
use crate::commands::CommandEvent;
use crate::mcp::add_args::{add_help, parse_add_args};

/// Run a `/mcp` subcommand; false means the browser should open instead.
pub fn run(app: &mut App, client: &Arc<Client>, raw_args: &str) -> bool {
    let (name, args) = match raw_args.split_once(char::is_whitespace) {
        Some((name, args)) => (name, args.trim()),
        None => (raw_args, ""),
    };
    match name {
        "add" => add(app, client, args),
        "status" => status(app, client, args),
        "login" => login(app, client, args),
        "logout" => logout(app, client, args),
        _ => return false,
    }
    true
}

fn status(app: &mut App, client: &Arc<Client>, args: &str) {
    if !args.is_empty() {
        reply(app, CommandEvent::Error("Usage: /mcp status".to_owned()));
        return;
    }
    let Some((session_id, tx, pending)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let event = match client
            .request(method::MCP_READ, json!({"sessionId": session_id}))
            .await
        {
            Ok(value) => CommandEvent::Result(status_text(&value)),
            Err(error) => CommandEvent::Error(format!("Failed to read MCP servers: {error}")),
        };
        send(&tx, &pending, event, false);
    });
}

fn status_text(value: &Value) -> String {
    let state = super::state_at(value, "/mcp");
    let statuses = state.statuses();
    if statuses.is_empty() {
        return "No MCP servers configured.".to_owned();
    }
    let mut lines = vec!["### MCP auth status".to_owned(), String::new()];
    lines.extend(
        statuses
            .into_iter()
            .map(|(alias, status)| format!("- `{alias}`: `{status}`")),
    );
    lines.join("\n")
}

fn login(app: &mut App, client: &Arc<Client>, alias: &str) {
    if alias.is_empty() {
        reply(
            app,
            CommandEvent::Error("Usage: /mcp login <alias>".to_owned()),
        );
        return;
    }
    let Some((session_id, tx, pending)) = dispatch(app) else {
        return;
    };
    let alias = alias.to_owned();
    let client = client.clone();
    tokio::spawn(async move {
        send(
            &tx,
            &pending,
            login_event(&client, &session_id, &alias).await,
            false,
        );
    });
}

/// The `mcp/login` round-trip; the auth URL arrives as an `mcp/authUrl` notification.
async fn login_event(client: &Arc<Client>, session_id: &str, alias: &str) -> CommandEvent {
    match client
        .request(
            method::MCP_LOGIN,
            json!({"sessionId": session_id, "name": alias}),
        )
        .await
    {
        Ok(_) => CommandEvent::Result(format!("MCP server `{alias}` authenticated.")),
        Err(error) => CommandEvent::Error(error.to_string()),
    }
}

fn logout(app: &mut App, client: &Arc<Client>, alias: &str) {
    if alias.is_empty() {
        reply(
            app,
            CommandEvent::Error("Usage: /mcp logout <alias>".to_owned()),
        );
        return;
    }
    let Some((session_id, tx, pending)) = dispatch(app) else {
        return;
    };
    let alias = alias.to_owned();
    let client = client.clone();
    tokio::spawn(async move {
        let event = match client
            .request(
                method::MCP_LOGOUT,
                json!({"sessionId": session_id, "name": alias}),
            )
            .await
        {
            Ok(_) => CommandEvent::Result(format!("MCP server `{alias}` logged out.")),
            Err(error) => CommandEvent::Error(error.to_string()),
        };
        send(&tx, &pending, event, false);
    });
}

fn add(app: &mut App, client: &Arc<Client>, raw_args: &str) {
    if matches!(raw_args, "--help" | "-h") {
        reply(app, CommandEvent::Result(add_help()));
        return;
    }
    let args = match parse_add_args(raw_args) {
        Ok(args) => args,
        Err(error) => {
            reply(app, CommandEvent::Error(error));
            return;
        }
    };
    let Some((session_id, tx, pending)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let result = client
            .request(
                method::MCP_ADD,
                json!({
                    "sessionId": session_id,
                    "url": args.url,
                    "name": args.name,
                    "scopes": args.scopes,
                    "transport": args.transport,
                    "allowInsecureHttp": args.allow_insecure_http,
                }),
            )
            .await;
        let added = match result {
            Ok(value) => value,
            Err(error) => {
                send(&tx, &pending, CommandEvent::Error(error.to_string()), false);
                return;
            }
        };
        let name = added
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let created = added.get("created").and_then(Value::as_bool) == Some(true);
        let head = if created {
            format!("Added OAuth MCP server `{name}`.")
        } else {
            format!("OAuth MCP server `{name}` is already configured.")
        };
        let tail = if args.login {
            "Starting OAuth login...".to_owned()
        } else {
            format!("Run `/mcp login {name}` to authenticate, or `/mcp status` to inspect it.")
        };
        send(
            &tx,
            &pending,
            CommandEvent::Result(format!("{head}\n{tail}")),
            args.login,
        );
        if args.login {
            let event = login_event(&client, &session_id, &name).await;
            send(&tx, &pending, event, false);
        }
    });
}

/// Claim a slot for one background subcommand; the app stays busy until it lands.
fn dispatch(app: &mut App) -> Option<(String, Sender<CommandEvent>, Arc<AtomicU32>)> {
    let session_id = app.session.session_id.clone()?;
    let tx = app.command_tx.clone()?;
    let pending = app.commit_started();
    Some((session_id, tx, pending))
}

/// Answer a subcommand locally, without a server round-trip.
fn reply(app: &mut App, event: CommandEvent) {
    let Some(tx) = app.command_tx.clone() else {
        return;
    };
    app.commit_started();
    let _ = tx.try_send(event);
}

/// Send one command event; `extra` claims another slot for a follow-up message.
fn send(tx: &Sender<CommandEvent>, pending: &Arc<AtomicU32>, event: CommandEvent, extra: bool) {
    if extra {
        pending.fetch_add(1, Ordering::Relaxed);
    }
    let _ = tx.try_send(event);
}
