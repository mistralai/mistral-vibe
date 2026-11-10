//! `/skills` round-trips, answered as `CommandEvent::Skills`.

use std::time::Duration;

use serde::de::DeserializeOwned;
use serde_json::{json, Value};

use super::bodies::BodyKey;
use super::labels::{self, BODY_MISSING};
use super::list::Row;
use crate::server::deadline::{within, REQUEST_TIMEOUT};
use crate::server::request_error::describe;
use crate::server::{
    method, Client, InstalledSkill, InstalledSkills, SkillCatalog, SkillChange, SkillDetail,
    SkillUpdate, SkillUpdates, SkillVersion, SkillVersions,
};

/// How long a change may take: longer than a read, since an import fetches the skill from the registry first.
const CHANGE_TIMEOUT: Duration = Duration::from_secs(120);

/// A round-trip whose answer opens a screen.
pub enum Opening {
    /// The list, waiting for its first read.
    List,
    /// The page of `row`, waiting for its versions.
    Page { row: Row, label: String },
}

impl Opening {
    pub(super) fn label(&self) -> &str {
        match self {
            Self::List => labels::LOADING,
            Self::Page { label, .. } => label,
        }
    }
}

/// A `/skills` round-trip's answer.
pub enum Event {
    /// The installed pins `/skills` opens on; with registry skills on, the catalog is read next.
    Opened(Result<Vec<InstalledSkill>, String>),
    Catalog {
        generation: u64,
        catalog: SkillCatalog,
    },
    Page {
        row: Row,
        versions: Result<Vec<SkillVersion>, String>,
    },
    Body {
        generation: u64,
        key: BodyKey,
        body: Result<String, String>,
    },
    Changed {
        outcome: Outcome,
        installed: Result<Vec<InstalledSkill>, String>,
    },
    Updates(Result<Vec<SkillUpdate>, String>),
}

/// What a change got back: the last answer that succeeded, and the failure that stopped it.
#[derive(Debug, Default)]
pub struct Outcome {
    pub answer: Option<Value>,
    pub error: Option<String>,
}

/// One request that gives up after `timeout`; its failure, or the deadline, as text.
async fn ask(
    client: &Client,
    method: &str,
    params: Value,
    timeout: Duration,
) -> Result<Value, String> {
    let answer = within(method, timeout, client.request(method, params))
        .await
        .map_err(|error| error.to_string())?;
    answer.map_err(|error| describe(&error))
}

/// A read that gives up after `REQUEST_TIMEOUT`, so one stuck answer never holds the next fetch back for good.
async fn call<T: DeserializeOwned>(
    client: &Client,
    method: &str,
    params: Value,
) -> Result<T, String> {
    let value = ask(client, method, params, REQUEST_TIMEOUT).await?;
    serde_json::from_value(value).map_err(|error| error.to_string())
}

pub(super) async fn installed(
    client: &Client,
    session_id: &str,
) -> Result<Vec<InstalledSkill>, String> {
    let params = json!({"sessionId": session_id});
    let answer: InstalledSkills = call(client, method::SKILLS_INSTALLED, params).await?;
    Ok(answer.skills)
}

/// The shared skills catalog; a failure reads as not loaded.
pub(super) async fn catalog(client: &Client, session_id: &str) -> SkillCatalog {
    let params = json!({"sessionId": session_id});
    call::<SkillCatalog>(client, method::SKILLS_CATALOG, params)
        .await
        .unwrap_or_default()
}

/// A registry skill's published versions.
pub(super) async fn versions(
    client: &Client,
    session_id: &str,
    skill_id: &str,
) -> Result<Vec<SkillVersion>, String> {
    let params = json!({"sessionId": session_id, "skillId": skill_id});
    let answer: SkillVersions = call(client, method::SKILLS_VERSIONS, params).await?;
    Ok(answer.versions)
}

/// A registry skill's body at `version`, the newest when `None`; an answer without one is a failure.
pub(super) async fn body(
    client: &Client,
    session_id: &str,
    skill_id: &str,
    version: Option<i64>,
) -> Result<String, String> {
    let params = json!({"sessionId": session_id, "skillId": skill_id, "version": version});
    let detail: SkillDetail = call(client, method::SKILLS_DETAIL, params).await?;
    detail.into_text().ok_or_else(|| BODY_MISSING.to_owned())
}

/// Send `changes` in order until one fails or outlasts `CHANGE_TIMEOUT`, then re-read the installed pins whatever they answered.
pub(super) async fn change(
    client: &Client,
    session_id: &str,
    changes: &[SkillChange],
) -> (Outcome, Result<Vec<InstalledSkill>, String>) {
    let outcome = send(client, session_id, changes, CHANGE_TIMEOUT).await;
    (outcome, installed(client, session_id).await)
}

/// Send `changes` in order until one fails or outlasts `timeout`.
async fn send(
    client: &Client,
    session_id: &str,
    changes: &[SkillChange],
    timeout: Duration,
) -> Outcome {
    let mut outcome = Outcome::default();
    for change in changes {
        let mut params = serde_json::to_value(change).unwrap_or_default();
        params["sessionId"] = json!(session_id);
        match ask(client, change.method(), params, timeout).await {
            Ok(answer) => outcome.answer = Some(answer),
            Err(error) => {
                outcome.error = Some(error);
                break;
            }
        }
    }
    outcome
}

pub(super) async fn updates(client: &Client, session_id: &str) -> Result<Vec<SkillUpdate>, String> {
    let params = json!({"sessionId": session_id});
    let answer: SkillUpdates = call(client, method::SKILLS_UPDATES, params).await?;
    Ok(answer.updates)
}

#[cfg(test)]
mod tests;
