//! Effect (tool call) wire types: Python `PublicEffectEntry` and its results.

use serde::Deserialize;
use serde_json::Value;

use super::effect_output::*;
use crate::utils::clean::clean_output;

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EffectEntry {
    #[serde(default)]
    pub generation_status: Option<String>,
    #[serde(default)]
    pub detail: Option<EffectSide>,
    #[serde(default)]
    pub state: Option<EffectState>,
}

impl EffectEntry {
    /// Settled `state.display`, else the in-progress `detail.display` (Python order).
    pub fn display(&self) -> Option<&EffectDisplay> {
        self.state
            .as_ref()
            .and_then(|s| s.display.as_ref())
            .or_else(|| self.detail.as_ref().and_then(|d| d.display.as_ref()))
    }

    pub fn success(&self) -> bool {
        self.display().and_then(|d| d.success).unwrap_or(true)
    }

    pub fn status(&self) -> Option<&str> {
        self.state
            .as_ref()
            .and_then(|state| state.status.as_deref())
    }

    /// Header verb, message, and suffix, following the Textual effect state mapping.
    pub fn summary(&self) -> (&str, &str, &str) {
        let detail = self.detail.as_ref().and_then(|side| side.display.as_ref());
        if self.status() == Some("failed") {
            if let Some(display) = detail {
                if let Some(message) = display.settled_message.as_deref() {
                    return (
                        display.settled_verb.as_deref().unwrap_or(&display.verb),
                        message,
                        &display.suffix,
                    );
                }
            }
        }

        let display = self.display().or(detail);
        let Some(display) = display else {
            return ("", "", "");
        };
        if matches!(self.status(), Some("cancelled" | "skipped")) {
            return ("", &display.message, &display.suffix);
        }
        (&display.verb, &display.message, &display.suffix)
    }

    pub fn is_muted(&self) -> bool {
        matches!(self.status(), Some("cancelled" | "skipped"))
    }

    pub fn is_subagent(&self) -> bool {
        self.kind() == Some("subagent")
            || self
                .detail
                .as_ref()
                .and_then(|detail| detail.tool_name.as_deref())
                .is_some_and(|name| name.starts_with("subagent."))
    }

    /// Result body lines shown on expand, following Python `_render_result_collapsible`.
    pub fn body(&self) -> Vec<String> {
        match self.status() {
            Some("failed") => format!("Error: {}", clean_output(self.error_message()))
                .lines()
                .map(str::to_owned)
                .collect(),
            Some("cancelled" | "skipped") => vec![format!("Skipped: {}", self.reason())],
            Some("completed") if self.output().is_some() && self.is_subagent() => {
                python_json_lines(self.output())
            }
            Some("completed") if self.output().is_some() => {
                format_effect_output(self.kind(), self.output())
            }
            // A hook that replaces a tool result leaves no structured output; the
            // model-facing text lives in outputText.
            Some("completed") => {
                let fallback = clean_output(self.output_text());
                match fallback.trim() {
                    "" => Vec::new(),
                    text => text.lines().map(str::to_owned).collect(),
                }
            }
            _ => Vec::new(),
        }
    }

    /// Python `EFFECT_WIDGETS[kind].result.COLLAPSIBLE`: diff- and answer-shaped
    /// results are always rendered in full, with no disclosure header.
    pub fn is_collapsible(&self) -> bool {
        !matches!(
            self.kind(),
            Some("file_edit" | "file_write" | "user_question")
        )
    }

    /// Python `_effect_is_terminal`: the state carries a settled verdict.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self.status(),
            Some("completed" | "failed" | "cancelled" | "skipped")
        )
    }

    /// Python `has_body`: whether the folded result section has anything to
    /// unfold; a running call mounts no result section yet.
    pub fn has_body(&self) -> bool {
        if !self.is_terminal() {
            return false;
        }
        if self.status() != Some("completed") {
            return true;
        }
        self.output().is_some()
            || !clean_output(self.output_text()).trim().is_empty()
            || !self.warnings().is_empty()
    }

    /// The streamed `state.outputText`, shown live under a running call.
    pub fn output_text(&self) -> &str {
        self.state
            .as_ref()
            .map(|state| state.output_text.as_str())
            .unwrap_or("")
    }

    /// Python `display.warnings`: the settled result display's warnings.
    pub fn warnings(&self) -> &[String] {
        self.state
            .as_ref()
            .and_then(|state| state.display.as_ref())
            .map_or(&[], |display| display.warnings.as_slice())
    }

    /// The warnings a result widget paints above its body: Python's Read and
    /// Edit widgets render them with the output, Grep's ahead of its null guard.
    pub fn result_warnings(&self) -> &[String] {
        if !matches!(self.kind(), Some("file_read" | "file_edit" | "file_search")) {
            return &[];
        }
        if self.kind() != Some("file_search") && self.output().is_none() {
            return &[];
        }
        self.warnings()
    }

    /// The call display's `statusText`, which steers the loading label.
    pub fn status_text(&self) -> &str {
        self.detail
            .as_ref()
            .and_then(|side| side.display.as_ref())
            .map_or("", |display| display.status_text.as_str())
    }

    /// The settled `edit` output, parsed as Python's `FileEditEffectOutput`.
    pub fn file_edit_output(&self) -> Option<FileEditEffectOutput> {
        if self.kind() != Some("file_edit") {
            return None;
        }
        serde_json::from_value(self.output()?.clone()).ok()
    }

    /// The settled todo rows, or none when the effect is not a todo.
    pub fn todo_rows(&self) -> Option<Vec<TodoRow<'_>>> {
        if self.kind() != Some("todo") {
            return None;
        }
        self.output().map(todo_rows)
    }

    /// `(label, url)` per web-search source, for link hit testing on the body.
    pub fn source_links(&self) -> Vec<(String, String)> {
        if self.kind() != Some("web_search") {
            return Vec::new();
        }
        self.output()
            .and_then(|output| output.get("sources"))
            .and_then(Value::as_array)
            .map(|sources| sources.iter().filter_map(source_label_url).collect())
            .unwrap_or_default()
    }

    /// The effect's call category (Python `ToolEffectKind`).
    pub fn kind(&self) -> Option<&str> {
        self.detail
            .as_ref()
            .and_then(|detail| detail.kind.as_deref())
    }

    /// The written path, whose extension picks the body's highlight language.
    pub fn file_write_path(&self) -> Option<&str> {
        if self.kind() != Some("file_write") {
            return None;
        }
        self.output()?.get("filePath")?.as_str()
    }

    /// The read path, whose extension picks the body's highlight language.
    pub fn file_read_path(&self) -> Option<&str> {
        if self.kind() != Some("file_read") {
            return None;
        }
        self.output()?.get("filePath")?.as_str()
    }

    fn output(&self) -> Option<&Value> {
        self.state
            .as_ref()
            .and_then(|state| state.output.as_deref())
    }

    fn error_message(&self) -> &str {
        self.state
            .as_ref()
            .and_then(|state| state.error.as_ref())
            .map_or("", |error| error.message.as_str())
    }

    fn reason(&self) -> &str {
        self.state
            .as_ref()
            .and_then(|state| state.reason.as_deref())
            .unwrap_or("")
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EffectSide {
    #[serde(default)]
    pub display: Option<EffectDisplay>,
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub tool_name: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EffectState {
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub display: Option<EffectDisplay>,
    #[serde(default)]
    pub output: Option<Box<Value>>,
    /// The stream a running call appends its incremental output to.
    #[serde(default)]
    pub output_text: String,
    #[serde(default)]
    pub error: Option<EffectError>,
    #[serde(default)]
    pub reason: Option<String>,
}

/// Python `PublicError`: the message behind a failed effect's `Error:` body.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EffectError {
    #[serde(default)]
    pub message: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EffectDisplay {
    #[serde(default)]
    pub verb: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub settled_verb: Option<String>,
    #[serde(default)]
    pub settled_message: Option<String>,
    #[serde(default)]
    pub success: Option<bool>,
    #[serde(default)]
    pub suffix: String,
    #[serde(default)]
    pub warnings: Vec<String>,
    #[serde(default)]
    pub status_text: String,
}
