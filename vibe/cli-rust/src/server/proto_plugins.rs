//! Plugin catalogue wire types (Python `PluginCatalogState` in `vibe/app_server/models.py`).

use serde::Deserialize;

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCatalogComponent {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub name: String,
    /// Route status, set only once a tool route drifted; absent means live.
    #[serde(default)]
    pub status: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCatalogEntry {
    pub name: String,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub source_format: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub scope: Option<String>,
    #[serde(default)]
    pub content_sha256: Option<String>,
    /// Where the plugin is installed now; `None` once it was uninstalled since the pin.
    #[serde(default)]
    pub installed_root: Option<String>,
    #[serde(default)]
    pub components: Vec<PluginCatalogComponent>,
    #[serde(default)]
    pub drifted: u32,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCatalogDropped {
    #[serde(default)]
    pub file: String,
    #[serde(default)]
    pub message: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCatalogState {
    #[serde(default)]
    pub plugins: Vec<PluginCatalogEntry>,
    #[serde(default)]
    pub dropped: Vec<PluginCatalogDropped>,
}
