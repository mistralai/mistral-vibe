//! The `/retry` prompt.

/// The tag wrapping the retry instruction so the server treats it as internal.
const VIBE_WARNING_TAG: &str = "vibe_warning";

/// The `vibe_warning`-tagged instruction to redo the discarded response, plus optional user guidance.
pub fn build_retry_prompt(additional_instructions: &str) -> String {
    let mut message = "Your previous response was interrupted before it finished, and its \
        unfinished output was discarded. Continue working on the pending user request from \
        where the work stands, and write your reply in full."
        .to_owned();
    let instructions = additional_instructions.trim();
    if !instructions.is_empty() {
        message.push_str("\n\nFollow these additional instructions from the user:\n");
        message.push_str(instructions);
    }
    format!("<{VIBE_WARNING_TAG}>{message}</{VIBE_WARNING_TAG}>")
}
