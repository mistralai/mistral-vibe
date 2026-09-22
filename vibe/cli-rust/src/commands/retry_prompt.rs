//! The `/retry` continuation prompt (Python `build_retry_prompt`).

/// The tag wrapping the retry instruction so the server treats it as internal.
const VIBE_WARNING_TAG: &str = "vibe_warning";

/// Build the retry prompt (Python `build_retry_prompt`): a `vibe_warning`-tagged
/// instruction to continue where the model stopped, plus optional user guidance.
pub fn build_retry_prompt(additional_instructions: &str) -> String {
    let mut message = "The previous model stream ended before reaching its end. \
        Continue the response exactly where it stopped without repeating text \
        already produced. If no response text was produced, answer the pending \
        user request normally."
        .to_owned();
    let instructions = additional_instructions.trim();
    if !instructions.is_empty() {
        message
            .push_str("\n\nFollow these additional instructions from the user while continuing:\n");
        message.push_str(instructions);
    }
    format!("<{VIBE_WARNING_TAG}>{message}</{VIBE_WARNING_TAG}>")
}
