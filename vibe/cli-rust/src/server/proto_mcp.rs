//! MCP server and connector wire types (Python `vibe/app_server/models.py`).

use std::collections::BTreeMap;

use caseless::default_case_fold_str as casefold;
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
#[serde(rename_all = "camelCase")]
pub struct MCPSourceSummary {
    pub name: String,
    /// Human label; older servers omit it, so `label()` falls back to `name`.
    #[serde(default)]
    pub display_name: String,
    pub kind: MCPSourceKind,
    pub transport: String,
    pub status: MCPSourceStatus,
    #[serde(default)]
    pub tools: Vec<MCPToolSummary>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub plugin_name: Option<String>,
}

impl MCPSourceSummary {
    pub fn label(&self) -> &str {
        if self.display_name.is_empty() {
            &self.name
        } else {
            &self.display_name
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MCPState {
    #[serde(default, deserialize_with = "deserialize_sources")]
    pub sources: Vec<MCPSourceSummary>,
    #[serde(default)]
    pub discovery_errors: BTreeMap<String, String>,
    #[serde(default)]
    pub connector_error: Option<String>,
    #[serde(default)]
    pub manage_connectors_url: Option<String>,
}

/// Skip a malformed source instead of failing the whole state (ADR 0014).
fn deserialize_sources<'de, D>(deserializer: D) -> Result<Vec<MCPSourceSummary>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let values = Vec::<serde_json::Value>::deserialize(deserializer)?;
    Ok(values
        .into_iter()
        .filter_map(|value| serde_json::from_value(value).ok())
        .collect())
}

impl MCPState {
    /// The source `/mcp <query>` names: by alias, else by its shown display name.
    pub fn resolve_source(&self, query: &str) -> Option<&MCPSourceSummary> {
        let needle = casefold(query);
        self.sources
            .iter()
            .find(|source| source.name == query)
            .or_else(|| {
                self.sources
                    .iter()
                    .find(|source| casefold(source.label()) == needle)
            })
    }

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
