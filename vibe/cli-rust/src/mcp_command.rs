//! Sessionless `vibe mcp` configuration commands.

use std::io::{self, Write};
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::external_url;
pub mod add_args;

use crate::server::{
    method, notification, signal, Client, ClientCapabilities, ClientInfo, InitializeParams, Launch,
    RpcError,
};
use add_args::McpAddArgs;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

#[derive(Debug, clap::Subcommand)]
pub enum McpCommand {
    /// Add an MCP server to the user configuration.
    Add(Box<McpAddArgs>),
    /// Remove an MCP server from the user configuration.
    Remove { name: String },
}

pub async fn run(command: &McpCommand, launch: Launch) -> ExitCode {
    match run_command(command, launch).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("vibe mcp: {error:#}");
            ExitCode::from(1)
        }
    }
}

async fn run_command(command: &McpCommand, launch: Launch) -> Result<()> {
    let params = match command {
        McpCommand::Add(args) => args.params()?,
        McpCommand::Remove { name } => json!({"name": name}),
    };
    let mut shutdown = signal::install()?;
    let (client, child, mut notifications, _crash) = Client::spawn(launch).await?;
    client.set_deny_callbacks(true);
    let operation = execute(&client, command, params);
    tokio::pin!(operation);
    let result = loop {
        tokio::select! {
            biased;
            _ = shutdown.wait() => break Err(anyhow::anyhow!("interrupted")),
            Some(event) = notifications.recv() => {
                if event.method == notification::MCP_AUTH_URL {
                    if let Some(url) = event.params.get("url").and_then(Value::as_str) {
                        println!("Open this URL in your browser:\n\n  {url}");
                        io::stdout().flush()?;
                        external_url::open(url);
                    }
                }
            }
            result = &mut operation => break result,
        }
    };
    client.close_stdin().await;
    child.wait_with_grace().await;
    result
}

async fn request(client: &Client, method: &str, params: Value) -> Result<Value> {
    tokio::time::timeout(REQUEST_TIMEOUT, client.request_err(method, params))
        .await
        .with_context(|| format!("{method} timed out"))?
        .map_err(|error| request_error(method, &error))
}

fn request_error(method: &str, error: &RpcError) -> anyhow::Error {
    let mut message = format!("{method} failed: [{}] {}", error.code(), error.message);
    if error.code() != "invalid_params" {
        return anyhow::anyhow!(message);
    }
    let Some(issues) = error
        .data
        .as_ref()
        .and_then(|data| data["issues"].as_array())
    else {
        return anyhow::anyhow!(message);
    };
    for issue in issues.iter().take(10) {
        let Some(detail) = issue["message"].as_str() else {
            continue;
        };
        let location = issue["path"]
            .as_array()
            .map(|path| {
                path.iter()
                    .filter_map(|part| match part {
                        Value::String(text) => Some(text.clone()),
                        Value::Number(index) => Some(index.to_string()),
                        _ => None,
                    })
                    .collect::<Vec<_>>()
                    .join(".")
            })
            .filter(|path| !path.is_empty())
            .unwrap_or_else(|| "<root>".into());
        message.push_str(&format!("\n{location}: {detail}"));
    }
    anyhow::anyhow!(message)
}

async fn execute(client: &Client, command: &McpCommand, params: Value) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe_mcp".into(),
            entrypoint: Some("cli".into()),
            version: env!("CARGO_PKG_VERSION").into(),
            title: None,
            terminal_emulator: crate::terminal_detect::detect().into(),
        },
        capabilities: ClientCapabilities::default(),
    };
    request(client, method::INITIALIZE, serde_json::to_value(init)?).await?;
    client.notify(method::INITIALIZED, json!({})).await?;
    match command {
        McpCommand::Add(args) => add(client, args, params).await,
        McpCommand::Remove { name } => {
            let result = request(client, method::MCP_REMOVE, params).await?;
            if result.get("removed").and_then(Value::as_bool) == Some(true) {
                println!("Removed MCP server `{name}`.");
            } else {
                println!("MCP server `{name}` is not configured in the user config.");
            }
            Ok(())
        }
    }
}

async fn add(client: &Client, args: &McpAddArgs, params: Value) -> Result<()> {
    let result = request(client, method::MCP_ADD, params).await?;
    let name = result
        .get("name")
        .and_then(Value::as_str)
        .context("mcp_catalog/add response missing server name")?;
    if result.get("created").and_then(Value::as_bool) == Some(true) {
        println!("Added MCP server `{name}`.");
    } else {
        println!("MCP server `{name}` is already configured.");
    }
    io::stdout().flush()?;
    if !args.is_oauth() {
        return Ok(());
    }
    if args.no_login {
        println!(
            "OAuth login skipped. Run `/mcp login {name}` if the server later requires authentication."
        );
        return Ok(());
    }
    let response = tokio::time::timeout(
        LOGIN_TIMEOUT,
        client.request(method::MCP_LOGIN, json!({"name": name})),
    )
    .await
    .context("OAuth login timed out")
    .and_then(|result| result)
    .map_err(|error| {
        anyhow::anyhow!("OAuth login failed: {error:#}\nRun `/mcp login {name}` to authenticate.")
    })?;
    if response.get("authenticated").and_then(Value::as_bool) == Some(false) {
        println!("MCP server `{name}` does not require OAuth authentication.");
    } else {
        println!("OAuth login completed.");
    }
    Ok(())
}
