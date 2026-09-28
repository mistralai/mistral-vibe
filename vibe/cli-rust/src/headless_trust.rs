//! Untrusted-workspace warning for headless mode, mirroring Python `_warn_if_workspace_untrusted`.

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::server::{method, Client};

pub async fn warn_if_untrusted(client: &Client, cwd: Option<&str>) -> Result<()> {
    let resp = client
        .request(method::WORKSPACE_TRUST_STATUS, json!({ "cwd": cwd }))
        .await
        .context("workspace/trust/status")?;
    if let Some(warning) = warning_from(&resp) {
        eprintln!("{warning}");
    }
    Ok(())
}

/// Python keeps first-seen order when merging detected + repo-detected files.
pub fn warning_from(resp: &Value) -> Option<String> {
    if resp.get("status").and_then(Value::as_str) != Some("untrusted") {
        return None;
    }
    let details = resp.get("details")?;
    let cwd = details.get("cwd").and_then(Value::as_str)?;
    let mut seen = std::collections::HashSet::new();
    let files: Vec<&str> = ["detectedFiles", "repoDetectedFiles"]
        .iter()
        .filter_map(|key| details.get(key))
        .filter_map(Value::as_array)
        .flatten()
        .filter_map(Value::as_str)
        .filter(|f| seen.insert(*f))
        .collect();
    if files.is_empty() {
        return None;
    }
    Some(format!(
        "Warning: {cwd} is not trusted; project configuration ({}) will be ignored. Re-run with --trust to trust this folder temporarily.",
        files.join(", ")
    ))
}
