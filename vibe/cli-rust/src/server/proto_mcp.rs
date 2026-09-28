//! MCP server and connector wire types (Python `vibe/app_server/models.py`).

use std::collections::BTreeMap;

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MCPSourceKind {
    Server,
    Connector,
}

impl MCPSourceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Server => "server",
            Self::Connector => "connector",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MCPSourceStatus {
    Disabled,
    Connected,
    Enabled,
    NeedsAuth,
    NeedsSetup,
    Unavailable,
}

impl MCPSourceStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Disabled => "disabled",
            Self::Connected => "connected",
            Self::Enabled => "enabled",
            Self::NeedsAuth => "needs_auth",
            Self::NeedsSetup => "needs_setup",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct MCPToolSummary {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
}

fn enabled_by_default() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
pub struct MCPSourceSummary {
    pub name: String,
    pub kind: MCPSourceKind,
    pub transport: String,
    pub status: MCPSourceStatus,
    #[serde(default)]
    pub tools: Vec<MCPToolSummary>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MCPState {
    #[serde(default)]
    pub sources: Vec<MCPSourceSummary>,
    #[serde(default)]
    pub discovery_errors: BTreeMap<String, String>,
    #[serde(default)]
    pub connector_error: Option<String>,
}

impl MCPState {
    /// Enabled servers still waiting for an OAuth login, sorted by alias.
    pub fn needs_auth(&self) -> Vec<&str> {
        let mut aliases: Vec<&str> = self
            .sources
            .iter()
            .filter(|source| {
                source.kind == MCPSourceKind::Server && source.status == MCPSourceStatus::NeedsAuth
            })
            .map(|source| source.name.as_str())
            .collect();
        aliases.sort_unstable();
        aliases
    }

    /// Server statuses keyed by alias, as `/mcp status` lists them.
    pub fn statuses(&self) -> BTreeMap<&str, &'static str> {
        self.sources
            .iter()
            .filter(|source| source.kind == MCPSourceKind::Server)
            .map(|source| (source.name.as_str(), source.status.as_str()))
            .collect()
    }
}
