//! Sessionless `vibe mcp` configuration commands.

use std::io::{self, Write};
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::external_url;
use crate::mcp::add_args::AddArgs;
use crate::server::{
    method, notification, signal, Client, ClientCapabilities, ClientInfo, InitializeParams, Launch,
};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

#[derive(Debug, clap::Subcommand)]
pub enum McpCommand {
    /// Add an OAuth MCP server to the user configuration.
    Add(AddArgs),
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
    let mut shutdown = signal::install()?;
    let (client, child, mut notifications, _crash) = Client::spawn(launch).await?;
    client.set_deny_callbacks(true);
    let operation = execute(&client, command);
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
    tokio::time::timeout(REQUEST_TIMEOUT, client.request(method, params))
        .await
        .with_context(|| format!("{method} timed out"))?
}

async fn execute(client: &Client, command: &McpCommand) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe_mcp".into(),
            entrypoint: Some("cli".into()),
            version: env!("CARGO_PKG_VERSION").into(),
        },
        capabilities: ClientCapabilities::default(),
    };
    request(client, method::INITIALIZE, serde_json::to_value(init)?).await?;
    client.notify(method::INITIALIZED, json!({})).await?;
    match command {
        McpCommand::Add(args) => add(client, args).await,
        McpCommand::Remove { name } => {
            let result = request(client, method::MCP_REMOVE, json!({"name": name})).await?;
            if result.get("removed").and_then(Value::as_bool) == Some(true) {
                println!("Removed MCP server `{name}`.");
            } else {
                println!("MCP server `{name}` is not configured in the user config.");
            }
            Ok(())
        }
    }
}

async fn add(client: &Client, args: &AddArgs) -> Result<()> {
    let result = request(
        client,
        method::MCP_ADD,
        json!({
            "url": args.url,
            "name": args.name,
            "scopes": args.scopes,
            "transport": args.transport,
            "allowInsecureHttp": args.allow_insecure_http,
        }),
    )
    .await?;
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
    if !args.login {
        println!("Run `/mcp login {name}` to authenticate.");
        return Ok(());
    }
    let login = tokio::time::timeout(
        LOGIN_TIMEOUT,
        client.request(method::MCP_LOGIN, json!({"name": name})),
    )
    .await
    .context("OAuth login timed out")
    .and_then(|result| result);
    if let Err(error) = login {
        bail!("OAuth login failed: {error:#}\nRun `/mcp login {name}` to authenticate.");
    }
    println!("OAuth login completed.");
    Ok(())
}
