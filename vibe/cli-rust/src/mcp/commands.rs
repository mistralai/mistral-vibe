//! `/mcp` subcommands.

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
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    crate::work::spawn(async move {
        let event = match client
            .request_err(method::MCP_READ, json!({"sessionId": session_id}))
            .await
        {
            Ok(value) => status_event(&value),
            Err(error) => {
                CommandEvent::Error(format!("Failed to read MCP servers: {}", error.message))
            }
        };
        let _ = tx.try_send(event);
    });
}

fn status_event(value: &Value) -> CommandEvent {
    let state = super::state_at(value, "/mcp").unwrap_or_default();
    let statuses = state.statuses();
    if statuses.is_empty() {
        return CommandEvent::Status("No MCP servers configured.".to_owned());
    }
    let mut lines = vec!["## MCP auth status".to_owned(), String::new()];
    lines.extend(
        statuses
            .into_iter()
            .map(|(alias, status)| format!("- `{alias}`: `{status}`")),
    );
    CommandEvent::Result(lines.join("\n"))
}

fn login(app: &mut App, client: &Arc<Client>, alias: &str) {
    if alias.is_empty() {
        reply(
            app,
            CommandEvent::Error("Usage: /mcp login <alias>".to_owned()),
        );
        return;
    }
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let alias = alias.to_owned();
    let client = client.clone();
    crate::work::spawn(async move {
        let _ = tx.try_send(login_event(&client, &session_id, &alias).await);
    });
}

/// The `mcp/login` round-trip; the auth URL arrives as an `mcp/authUrl` notification.
async fn login_event(client: &Arc<Client>, session_id: &str, alias: &str) -> CommandEvent {
    match client
        .request_err(
            method::MCP_LOGIN,
            json!({"sessionId": session_id, "name": alias}),
        )
        .await
    {
        Ok(_) => CommandEvent::Status(format!("MCP server `{alias}` authenticated.")),
        Err(error) => CommandEvent::Error(error.message),
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
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let alias = alias.to_owned();
    let client = client.clone();
    crate::work::spawn(async move {
        let event = match client
            .request_err(
                method::MCP_LOGOUT,
                json!({"sessionId": session_id, "name": alias}),
            )
            .await
        {
            Ok(_) => CommandEvent::Status(format!("MCP server `{alias}` logged out.")),
            Err(error) => CommandEvent::Error(error.message),
        };
        let _ = tx.try_send(event);
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
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    crate::work::spawn(async move {
        let result = client
            .request_err(
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
                let _ = tx.try_send(CommandEvent::Error(error.message));
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
        let _ = tx.try_send(CommandEvent::Status(format!("{head}\n{tail}")));
        if args.login {
            let event = login_event(&client, &session_id, &name).await;
            let _ = tx.try_send(event);
        }
    });
}

/// The session and command channel one background subcommand answers on.
fn dispatch(app: &mut App) -> Option<(String, Sender<CommandEvent>)> {
    let session_id = app.session.session_id.clone()?;
    let tx = app.command_tx.clone()?;
    Some((session_id, tx))
}

/// Answer a subcommand locally, without a server round-trip.
fn reply(app: &mut App, event: CommandEvent) {
    let Some(tx) = app.command_tx.clone() else {
        return;
    };
    let _ = tx.try_send(event);
}
