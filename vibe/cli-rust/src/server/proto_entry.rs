//! The `history/entry*` fields reducers branch on; every one defaults (ADR 0014).

use serde::Deserialize;
use serde_json::Value;

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EntryParams {
    pub entry: EntryHead,
    /// Set on `history/entryUpdated`, which carries a patch instead of the entry.
    pub entry_id: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EntryHead {
    pub id: Option<String>,
    #[serde(rename = "type")]
    pub entry_type: Option<String>,
    pub role: Option<String>,
    pub kind: Option<String>,
    pub generation_status: Option<String>,
    #[serde(deserialize_with = "crate::utils::json::null_default")]
    pub detail: EntryDetail,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EntryDetail {
    pub kind: Option<String>,
    pub title: Option<String>,
    pub hook_name: Option<String>,
    #[serde(deserialize_with = "crate::utils::json::null_default")]
    pub display: EntryDisplay,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EntryDisplay {
    pub status_text: Option<String>,
}

impl EntryParams {
    pub fn read(params: &Value) -> Self {
        crate::utils::json::read(params)
    }
}

impl EntryHead {
    pub fn read(params: &Value) -> Self {
        EntryParams::read(params).entry
    }

    pub fn entry_type(&self) -> Option<&str> {
        self.entry_type.as_deref()
    }

    /// The role of a message entry.
    pub fn message_role(&self) -> Option<&str> {
        self.role
            .as_deref()
            .filter(|_| self.entry_type() == Some("message"))
    }

    pub fn detail_kind(&self) -> Option<&str> {
        self.detail.kind.as_deref()
    }
}
