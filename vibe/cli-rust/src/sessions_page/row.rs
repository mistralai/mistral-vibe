//! One `session/list` item and one `projects/list` project, read leniently (ADR 0014).

use serde::Deserialize;
use serde_json::Value;

use crate::utils::json::null_default;

/// A listed session (app-server `PublicSession`); every field but the id is optional.
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Session {
    pub id: String,
    #[serde(default, deserialize_with = "null_default")]
    pub title: String,
    #[serde(default, deserialize_with = "null_default")]
    pub preview: String,
    #[serde(default, deserialize_with = "null_default")]
    pub created_at: u64,
    #[serde(default, deserialize_with = "null_default")]
    pub updated_at: u64,
    #[serde(default)]
    pub bumped_at: Option<u64>,
    #[serde(default)]
    pub archived_at: Option<u64>,
    #[serde(default, deserialize_with = "null_default")]
    pub is_unseen: bool,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default, deserialize_with = "status_type")]
    pub status: String,
    #[serde(default)]
    pub started_by_session_id: Option<String>,
    #[serde(default)]
    pub parent_session_id: Option<String>,
    #[serde(default)]
    pub worktree: Option<Worktree>,
    #[serde(default)]
    pub project: Option<ProjectStamp>,
    #[serde(default)]
    pub repository_root: Option<String>,
    /// The checkout holding `cwd`: the repository itself, or a linked worktree.
    #[serde(default)]
    pub checkout_root: Option<String>,
    /// `cwd` is gone from disk, such as a removed worktree.
    #[serde(default, deserialize_with = "null_default")]
    pub cwd_missing: bool,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub reasoning_effort: Option<String>,
    #[serde(default)]
    pub token_usage: Option<Tokens>,
}

/// What a session spent, as `session/list` reports it.
#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Tokens {
    #[serde(default, deserialize_with = "null_default")]
    pub input_tokens: u64,
    #[serde(default, deserialize_with = "null_default")]
    pub output_tokens: u64,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
pub struct Worktree {
    #[serde(default, deserialize_with = "null_default")]
    pub name: String,
    #[serde(default, deserialize_with = "null_default")]
    pub branch: String,
}

/// The project a session was started in (`{kind: "local"|"cloud", id}`).
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
pub struct ProjectStamp {
    #[serde(default, deserialize_with = "null_default")]
    pub kind: String,
    #[serde(default)]
    pub id: Option<String>,
}

/// A local project (`projects/list`).
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub id: String,
    #[serde(default, deserialize_with = "null_default")]
    pub name: String,
    #[serde(default, deserialize_with = "null_default")]
    pub root_paths: Vec<String>,
    #[serde(default)]
    pub cloud: Option<CloudBacking>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CloudBacking {
    #[serde(default)]
    pub project_id: Option<String>,
}

impl Session {
    /// The activity time: `bumpedAt`, which only a new turn moves, else `updatedAt`.
    pub fn activity(&self) -> u64 {
        self.bumped_at.unwrap_or(self.updated_at)
    }

    /// The row's label: its title, else its first message, else a placeholder.
    pub fn label(&self) -> &str {
        let title = self.title.trim();
        if !title.is_empty() {
            return title;
        }
        match crate::utils::text::first_line(&self.preview).trim() {
            "" => "Untitled session",
            preview => preview,
        }
    }

    /// Where in its repository the session works: the linked worktree, then
    /// the subfolder (`feature › vibe`).
    pub fn location(&self) -> Option<String> {
        let mut parts: Vec<String> = Vec::new();
        if let (Some(root), Some(checkout)) = (&self.repository_root, &self.checkout_root) {
            if checkout != root {
                parts.push(name_of(checkout));
            }
        }
        let base = self
            .checkout_root
            .as_ref()
            .or(self.repository_root.as_ref());
        if let (Some(base), Some(cwd)) = (base, &self.cwd) {
            if let Ok(inner) = std::path::Path::new(cwd).strip_prefix(base) {
                parts.extend(
                    inner
                        .components()
                        .map(|part| part.as_os_str().to_string_lossy().into_owned()),
                );
            }
        }
        if self.branch().is_some() && parts.first().map(String::as_str) == self.branch() {
            parts.remove(0);
        }
        (!parts.is_empty()).then(|| parts.join(" › "))
    }

    pub fn branch(&self) -> Option<&str> {
        self.worktree
            .as_ref()
            .map(|worktree| worktree.branch.as_str())
            .filter(|branch| !branch.is_empty())
    }
}

fn name_of(folder: &str) -> String {
    std::path::Path::new(folder).file_name().map_or_else(
        || folder.to_owned(),
        |name| name.to_string_lossy().into_owned(),
    )
}

/// `status.type`, or empty when the status is missing or malformed.
fn status_type<'de, D: serde::Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let value = Value::deserialize(d)?;
    Ok(value
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned())
}

/// The sessions of a `session/list` answer page, skipping malformed items and subagents.
pub fn sessions(value: &Value) -> Vec<Session> {
    crate::utils::json::read::<LenientList<Session>>(&value["items"])
        .0
        .into_iter()
        .filter(|session| session.parent_session_id.is_none())
        .collect()
}

/// The projects of a `projects/list` answer; anything unreadable is no project.
pub fn projects(value: &Value) -> Vec<Project> {
    crate::utils::json::read::<LenientList<Project>>(&value["projects"]).0
}

#[derive(Default)]
struct LenientList<T>(Vec<T>);

impl<'de, T: serde::de::DeserializeOwned> Deserialize<'de> for LenientList<T> {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        crate::utils::json::lenient(d).map(Self)
    }
}
