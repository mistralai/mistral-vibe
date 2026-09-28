//! Remote-project picker projections from the app server.

use serde::Deserialize;

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Repository {
    pub repo_url: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub project_id: String,
    pub name: String,
    #[serde(default)]
    pub repositories: Vec<Repository>,
    #[serde(default)]
    pub is_read_only: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemoteProjectLink {
    pub repo_url: String,
    pub project_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PickerContext {
    pub repo_url: String,
    pub repo_name: String,
    pub saved_link: Option<RemoteProjectLink>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GitInfo {
    pub branch: Option<String>,
    pub default_branch: Option<String>,
}

impl GitInfo {
    pub fn suggested_default_branch(&self) -> &str {
        self.default_branch
            .as_deref()
            .filter(|s| !s.is_empty())
            .or(self.branch.as_deref().filter(|s| !s.is_empty()))
            .unwrap_or("main")
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PickerState {
    pub projects: Vec<Project>,
    pub next_cursor: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct PickerView {
    pub context: PickerContext,
    pub state: PickerState,
    pub git: GitInfo,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenResponse {
    pub picker_id: String,
    pub view: PickerView,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LoadMoreResponse {
    pub view: PickerView,
    pub focus_option_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ProjectResponse {
    pub view: PickerView,
    pub project: Project,
}
