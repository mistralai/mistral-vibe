//! Sessionless `vibe config` commands.

use std::io::{self, Write};
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::server::{
    method, signal, Client, ClientCapabilities, ClientInfo, InitializeParams, Launch,
};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, clap::Subcommand)]
pub enum ConfigCommand {
    /// Print the JSON schema of config.toml, each builtin tool's settings included.
    Schema,
}

pub async fn run(command: &ConfigCommand, launch: Launch) -> ExitCode {
    match run_command(command, launch).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("vibe config: {error:#}");
            ExitCode::from(1)
        }
    }
}

async fn run_command(command: &ConfigCommand, launch: Launch) -> Result<()> {
    let mut shutdown = signal::install()?;
    let (client, child, _notifications, _crash) = Client::spawn(launch).await?;
    client.set_deny_callbacks(true);
    let result = tokio::select! {
        biased;
        _ = shutdown.wait() => Err(anyhow::anyhow!("interrupted")),
        result = execute(&client, command) => result,
    };
    client.close_stdin().await;
    child.wait_with_grace().await;
    result
}

async fn execute(client: &Client, command: &ConfigCommand) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe_config".into(),
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
        ConfigCommand::Schema => {
            let response = request(client, method::CONFIG_SCHEMA, json!({})).await?;
            let schema = schema_document(&response)?;
            let mut stdout = io::stdout().lock();
            serde_json::to_writer_pretty(&mut stdout, &schema)?;
            writeln!(stdout)?;
            stdout.flush()?;
            Ok(())
        }
    }
}

/// The schema itself, stamped with the version the server gave it.
fn schema_document(response: &Value) -> Result<Value> {
    let mut schema = response
        .get("schema")
        .cloned()
        .context("config/schema response missing schema")?;
    if let (Some(object), Some(version)) = (
        schema.as_object_mut(),
        response.get("configSchemaVersion").cloned(),
    ) {
        object.insert(
            "$comment".into(),
            json!(format!("version {}", version.as_str().unwrap_or_default())),
        );
    }
    Ok(schema)
}

async fn request(client: &Client, method: &str, params: Value) -> Result<Value> {
    tokio::time::timeout(REQUEST_TIMEOUT, client.request(method, params))
        .await
        .with_context(|| format!("{method} timed out"))?
        .with_context(|| format!("{method} failed"))
}

#[cfg(test)]
mod tests;
