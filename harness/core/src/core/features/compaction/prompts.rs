use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;

/// Compaction text Core adds to summary requests. Core's defaults live in
/// `default_prompts.json`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prompts {
    /// The user message a cut-off retry adds after the standalone request.
    pub cut_off_retry: String,
    /// The result an in-context summary request sends for each tool call of
    /// the reply it ends with. The real results stay out of the request and
    /// go back after the summary when the history is rebuilt.
    pub tool_result_placeholder: String,
    /// The line above the reduced view of items a rebuilt history could not
    /// fit in full. It labels the view and gives no instruction.
    pub reduced_view_header: String,
    /// The line a rebuilt history holds in place of items too large for even
    /// the most reduced view.
    pub reduced_view_omitted: String,
    /// The note after a summary cut to fit: one cut off at the output limit,
    /// or one too long for the rebuilt history.
    pub summary_cut_note: String,
}

impl Prompts {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        let texts = [
            &self.cut_off_retry,
            &self.tool_result_placeholder,
            &self.reduced_view_header,
            &self.reduced_view_omitted,
            &self.summary_cut_note,
        ];
        if texts.iter().any(|text| text.trim().is_empty()) {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.compaction",
                "compaction prompts must not be empty",
            ));
        }
        Ok(())
    }
}
