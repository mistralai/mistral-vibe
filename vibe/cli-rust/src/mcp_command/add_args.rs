//! Shell MCP add arguments, including the legacy URL-first syntax.

use anyhow::{bail, Result};
use serde::Serialize;
use serde_json::{json, Map, Value};

#[derive(Debug, clap::Args, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct McpAddArgs {
    /// Server name with --url or --transport stdio; otherwise a hosted server URL.
    #[arg(value_name = "NAME_OR_URL")]
    #[serde(skip)]
    pub target: String,
    /// Remote server URL (use a positional server name).
    #[arg(long, conflicts_with = "name")]
    #[serde(skip)]
    pub url: Option<String>,
    /// Server alias for the legacy URL-first syntax.
    #[arg(long)]
    #[serde(skip)]
    pub name: Option<String>,
    /// Server transport.
    #[arg(long, default_value = "streamable-http", value_parser = ["http", "streamable-http", "stdio"], requires_if("stdio", "command"))]
    pub transport: String,
    /// OAuth scope. Repeat for multiple scopes.
    #[arg(long = "scope", conflicts_with_all = ["headers", "api_key_env", "api_key_header", "api_key_format"])]
    pub scopes: Vec<String>,
    /// Save an OAuth server without starting browser login.
    #[arg(long, conflicts_with_all = ["headers", "api_key_env", "api_key_header", "api_key_format"])]
    #[serde(skip)]
    pub no_login: bool,
    /// Allow plaintext HTTP to non-localhost hosts; credentials are unencrypted.
    #[arg(long)]
    pub allow_insecure_http: bool,
    /// Static header NAME=VALUE. Stored in plaintext; use --api-key-env for secrets.
    #[arg(long = "header", value_name = "NAME=VALUE")]
    #[serde(skip)]
    pub headers: Vec<String>,
    /// Environment variable containing the API key.
    #[arg(long, visible_alias = "bearer-token-env-var", value_name = "VAR")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key_env: Option<String>,
    /// API key header (default: Authorization).
    #[arg(long, requires = "api_key_env", value_name = "HEADER")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key_header: Option<String>,
    /// API key header format containing {token} (default: Bearer {token}).
    #[arg(long, requires = "api_key_env", value_name = "FORMAT")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key_format: Option<String>,
    /// Executable for a stdio server.
    #[arg(long, conflicts_with_all = ["name", "no_login", "scopes"])]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    /// Argument for the stdio executable. Repeat for multiple arguments.
    #[arg(long = "arg", value_name = "VALUE")]
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<String>,
    /// Environment variable for the stdio process. Repeat for multiple variables.
    #[arg(long, value_name = "NAME=VALUE")]
    #[serde(skip)]
    pub env: Vec<String>,
    /// Server startup timeout in seconds.
    #[arg(long, value_name = "SECONDS", value_parser = parse_timeout)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub startup_timeout_sec: Option<f64>,
    /// Tool execution timeout in seconds.
    #[arg(long, value_name = "SECONDS", value_parser = parse_timeout)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_timeout_sec: Option<f64>,
}

impl McpAddArgs {
    pub fn params(&self) -> Result<Value> {
        let mut params = serde_json::to_value(self)?;
        if self.url.is_some() || self.transport == "stdio" {
            params["name"] = json!(self.target);
            params["url"] = json!(self.url);
        } else if !self.target.contains("://") {
            bail!("--url is required for http and streamable-http transports.");
        } else {
            params["name"] = json!(self.name);
            params["url"] = json!(self.target);
        }
        let headers: Map<_, _> = assignments(&self.headers, "--header")?
            .into_iter()
            .map(|(name, value)| (name.to_owned(), json!(value.trim())))
            .collect();
        if !headers.is_empty() {
            params["headers"] = headers.into();
        }
        let env: Map<_, _> = assignments(&self.env, "--env")?
            .into_iter()
            .map(|(name, value)| (name.to_owned(), json!(value)))
            .collect();
        if !env.is_empty() {
            params["env"] = env.into();
        }
        Ok(params)
    }

    pub fn is_oauth(&self) -> bool {
        self.transport != "stdio"
            && self.headers.is_empty()
            && self.api_key_env.is_none()
            && self.api_key_header.is_none()
            && self.api_key_format.is_none()
    }
}

fn assignments<'a>(values: &'a [String], flag: &str) -> Result<Vec<(&'a str, &'a str)>> {
    let mut result: Vec<(&str, &str)> = Vec::new();
    for value in values {
        let Some((name, content)) = value.split_once('=') else {
            bail!("{flag} values must use NAME=VALUE.");
        };
        let name = name.trim();
        if result.iter().any(|(seen, _)| *seen == name) {
            bail!("Duplicate {flag} name '{name}'.");
        }
        result.push((name, content));
    }
    Ok(result)
}

fn parse_timeout(value: &str) -> Result<f64, String> {
    let timeout = value.parse::<f64>().map_err(|error| error.to_string())?;
    if !timeout.is_finite() {
        return Err("Timeout must be a finite number of seconds".into());
    }
    Ok(timeout)
}
