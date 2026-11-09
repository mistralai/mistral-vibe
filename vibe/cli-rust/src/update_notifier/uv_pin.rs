//! The uv requirement pin: an exact `==` clause the install cannot upgrade past.

use super::pep440::parse_pep440_version;
use super::uv_oracle::{this_install_receipt, PROJECT_NAME};

/// The exact pin this install's recorded requirement holds it to, when it
/// provably blocks `latest_version`. uv re-resolves `uv tool upgrade` within
/// the requirement recorded at install time, so an exact clause naming another
/// version makes any upgrade offer a guaranteed no-op. Read from the receipt
/// on every call, never cached: the user can unpin between runs.
pub async fn blocking_pin(latest_version: &str) -> Option<String> {
    let (_tool_dir, receipt) = this_install_receipt().await?;
    blocking_pin_from_receipt(&receipt, latest_version)
}

/// `blocking_pin` over a receipt's text.
pub fn blocking_pin_from_receipt(receipt: &str, latest_version: &str) -> Option<String> {
    let document: toml_edit::DocumentMut = receipt.parse().ok()?;
    let requirements = document
        .get("tool")
        .and_then(|tool| tool.get("requirements"))
        .and_then(|requirements| requirements.as_array())?;
    for requirement in requirements {
        let Some(table) = requirement.as_inline_table() else {
            continue;
        };
        if table.get("name").and_then(|name| name.as_str()) != Some(PROJECT_NAME) {
            continue;
        }
        // One requirement per tool: no specifier is an unpinned registry
        // install, and a git requirement never reaches the oracle anyway.
        let specifier = table.get("specifier").and_then(|spec| spec.as_str())?;
        return blocking_pin_in_specifier(specifier, latest_version);
    }
    None
}

/// The first exact clause that does not name the latest version. Upper
/// bounds (`<`, `<=`, `~=`) can also hold an install back, but whether they
/// block depends on the bound, so they stay live rather than guessed.
fn blocking_pin_in_specifier(specifier: &str, latest_version: &str) -> Option<String> {
    let latest = parse_pep440_version(latest_version)?;
    for clause in specifier.split(',') {
        let clause = clause.trim();
        let Some(bound) = clause
            .strip_prefix("===")
            .or_else(|| clause.strip_prefix("=="))
            .map(str::trim)
        else {
            continue;
        };
        let Some(bound) = parse_pep440_version(bound) else {
            continue;
        };
        if bound != latest {
            return Some(clause.to_owned());
        }
    }
    None
}
