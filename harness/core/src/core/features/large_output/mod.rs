use std::borrow::Cow;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;
use crate::core::model_input_projection::{
    IMAGE_PLACEHOLDER, is_image_content, project_model_input_content,
};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

use self::type_inference::infer_typescript_type;

mod type_inference;

const CHARACTERS_PER_TOKEN: usize = 4;
const DEFAULT_MAX_OUTPUT_TOKENS: u32 = 10_000;
const HARD_INLINE_TOKEN_LIMIT: u32 = 25_000;
const HARD_INLINE_CHARACTER_LIMIT: usize =
    (HARD_INLINE_TOKEN_LIMIT as usize).saturating_mul(CHARACTERS_PER_TOKEN);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Policy {
    Disabled,
    Filesystem {
        #[serde(default = "default_max_output_tokens")]
        max_output_tokens: u32,
        #[serde(default = "default_max_output_tokens")]
        model_visible_output_tokens: u32,
    },
}

const fn default_max_output_tokens() -> u32 {
    DEFAULT_MAX_OUTPUT_TOKENS
}

impl Policy {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        let Self::Filesystem {
            max_output_tokens,
            model_visible_output_tokens,
        } = self
        else {
            return Ok(());
        };
        if *max_output_tokens == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.large_output.max_output_tokens",
                "large-output max_output_tokens must be greater than zero",
            ));
        }
        if *max_output_tokens > HARD_INLINE_TOKEN_LIMIT {
            return Err(CoreError::invalid_configuration(
                "settings.tools.large_output.max_output_tokens",
                format!(
                    "large-output max_output_tokens must not exceed the hard inline limit of {HARD_INLINE_TOKEN_LIMIT} tokens"
                ),
            ));
        }
        if *model_visible_output_tokens == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.large_output.model_visible_output_tokens",
                "large-output model_visible_output_tokens must be greater than zero",
            ));
        }
        if model_visible_output_tokens > max_output_tokens {
            return Err(CoreError::invalid_configuration(
                "settings.tools.large_output.model_visible_output_tokens",
                "large-output model_visible_output_tokens must not exceed max_output_tokens",
            ));
        }
        Ok(())
    }

    pub(crate) fn character_limits(&self) -> Option<CharacterLimits> {
        match self {
            Self::Disabled => None,
            Self::Filesystem {
                max_output_tokens,
                model_visible_output_tokens,
            } => {
                let max_output_tokens = (*max_output_tokens).min(HARD_INLINE_TOKEN_LIMIT);
                let model_visible_output_tokens = (*model_visible_output_tokens)
                    .min(max_output_tokens)
                    .min(HARD_INLINE_TOKEN_LIMIT);
                Some(CharacterLimits {
                    max_output: tokens_to_characters(max_output_tokens),
                    model_visible_output: tokens_to_characters(model_visible_output_tokens),
                })
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CharacterLimits {
    pub(crate) max_output: usize,
    pub(crate) model_visible_output: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct EvaluatedResult {
    pub(crate) serialized_char_count: usize,
    pub(crate) disposition: ResultDisposition,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ResultDisposition {
    Inline(ToolResult),
    Write(PendingWrite),
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PendingWrite {
    relative_path: String,
    serialized: String,
    original_result: ToolResult,
    tool_name: String,
    limits: CharacterLimits,
    type_definition: String,
}

impl PendingWrite {
    pub(crate) fn restore(
        relative_path: String,
        serialized: String,
        original_result: ToolResult,
        tool_name: String,
        limits: CharacterLimits,
        type_definition: String,
    ) -> Self {
        Self {
            relative_path,
            serialized,
            original_result,
            tool_name,
            limits,
            type_definition,
        }
    }

    pub(crate) fn relative_path(&self) -> &str {
        &self.relative_path
    }

    pub(crate) fn serialized(&self) -> &str {
        &self.serialized
    }

    pub(crate) fn original_result(&self) -> &ToolResult {
        &self.original_result
    }

    pub(crate) fn tool_name(&self) -> &str {
        &self.tool_name
    }

    pub(crate) fn limits(&self) -> CharacterLimits {
        self.limits
    }

    pub(crate) fn type_definition(&self) -> &str {
        &self.type_definition
    }

    pub(crate) fn complete(self, resolved_path: &str) -> ToolResult {
        let receipt = build_saved_output_message(
            resolved_path,
            &self.serialized,
            self.limits,
            &self.type_definition,
        );
        replace_result(self.original_result, receipt)
    }

    pub(crate) fn fail(self, error: &ProtocolError) -> ToolResult {
        if utf16_len(&self.serialized) <= HARD_INLINE_CHARACTER_LIMIT {
            return self.original_result;
        }
        let receipt = build_write_error_message(
            error,
            &self.relative_path,
            &self.serialized,
            &self.tool_name,
            self.limits,
            &self.type_definition,
        );
        replace_result(self.original_result, receipt)
    }
}

/// Measures the text of `result` against the limits. Images never count: they
/// stay in the result whatever its size, and the context budget counts them at
/// their image cost. The measured and saved serialization holds `[Image]` in
/// place of each image, never its data.
pub(crate) fn evaluate_result(
    policy: &Policy,
    tool_name: &str,
    call_id: &str,
    result: ToolResult,
) -> Result<Option<EvaluatedResult>, CoreError> {
    let limits = policy.character_limits();
    let value = parse_top_level_json_string(value_for_serialization(&without_image_data(&result)));
    let serialized = serde_json::to_string_pretty(&value).map_err(|error| {
        CoreError::invariant(format!("large-output serialization failed: {error}"))
    })?;
    let serialized_char_count = utf16_len(&serialized);
    match limits {
        Some(limits) if serialized_char_count <= limits.max_output => Ok(Some(EvaluatedResult {
            serialized_char_count,
            disposition: ResultDisposition::Inline(result),
        })),
        None if serialized_char_count <= HARD_INLINE_CHARACTER_LIMIT => Ok(None),
        None => {
            let type_definition = infer_typescript_type(&value);
            let receipt = build_hard_limit_message(tool_name, &serialized, &type_definition);
            Ok(Some(EvaluatedResult {
                serialized_char_count,
                disposition: ResultDisposition::Inline(replace_result(result, receipt)),
            }))
        }
        Some(limits) => Ok(Some(EvaluatedResult {
            serialized_char_count,
            disposition: ResultDisposition::Write(PendingWrite {
                relative_path: format!(
                    "tool-results/{}-{}.json",
                    encode_path_segment(tool_name),
                    encode_path_segment(call_id)
                ),
                serialized,
                original_result: result,
                tool_name: tool_name.to_string(),
                limits,
                type_definition: infer_typescript_type(&value),
            }),
        })),
    }
}

/// `result` with each image replaced by the text a model without image input
/// gets for it: `[Image]`, followed by the file link of an embedded image.
fn without_image_data(result: &ToolResult) -> Cow<'_, ToolResult> {
    if !result_content(result).iter().any(is_image_content) {
        return Cow::Borrowed(result);
    }
    let mut measured = result.clone();
    let (ToolResult::Success { content, .. } | ToolResult::Failure { content, .. }) = &mut measured;
    for block in content.iter_mut() {
        if let Cow::Owned(placeholder) =
            project_model_input_content(block, ImageDeliveryMode::WithoutImageInput)
        {
            *block = placeholder;
        }
    }
    Cow::Owned(measured)
}

fn result_content(result: &ToolResult) -> &[ContentBlock] {
    let (ToolResult::Success { content, .. } | ToolResult::Failure { content, .. }) = result;
    content
}

fn value_for_serialization(result: &ToolResult) -> Value {
    match result {
        ToolResult::Success {
            content,
            structured_content,
            ..
        } => {
            if let StructuredContent::Present(value) = structured_content
                && compact_structured_payload(content, value)
            {
                return value.clone();
            }
            let mut serialized = Map::new();
            serialized.insert(
                "content".to_string(),
                serde_json::to_value(content).expect("content blocks serialize"),
            );
            if let StructuredContent::Present(structured) = structured_content {
                serialized.insert("structured_content".to_string(), structured.clone());
            }
            Value::Object(serialized)
        }
        ToolResult::Failure { .. } => {
            serde_json::to_value(result).expect("validated tool results serialize")
        }
    }
}

fn compact_structured_payload(content: &[ContentBlock], value: &Value) -> bool {
    match content {
        [] => true,
        [ContentBlock::Text(text)] if text.meta.is_none() && text.annotations.is_none() => {
            let mirrors_value = value.as_str() == Some(text.text.as_str())
                || serde_json::from_str::<Value>(&text.text).is_ok_and(|parsed| parsed == *value);
            // The model reads the text, so it must not be longer than the value measured in its place.
            mirrors_value
                && serde_json::to_string_pretty(&parse_top_level_json_string(value.clone()))
                    .is_ok_and(|measured| utf16_len(&text.text) <= utf16_len(&measured))
        }
        _ => false,
    }
}

fn parse_top_level_json_string(value: Value) -> Value {
    let Value::String(text) = value else {
        return value;
    };
    serde_json::from_str(&text).unwrap_or(Value::String(text))
}

/// Replaces the text of `original` with `message`. Its images follow the
/// message unchanged, since they never count toward the limits.
fn replace_result(original: ToolResult, message: String) -> ToolResult {
    let images: Vec<ContentBlock> = result_content(&original)
        .iter()
        .filter(|block| is_image_content(block))
        .cloned()
        .collect();
    let message = if images.is_empty() {
        message
    } else {
        format!(
            "{message}\n{}",
            format_system_tag(&format!(
                "The images of the tool output follow this notice. The text above shows {IMAGE_PLACEHOLDER} in their place."
            ))
        )
    };
    let content = std::iter::once(ContentBlock::text(message.clone()))
        .chain(images)
        .collect();
    match original {
        ToolResult::Success { .. } => ToolResult::Success {
            content,
            structured_content: StructuredContent::present(Value::Null),
            meta: None,
        },
        ToolResult::Failure { error, .. } => ToolResult::Failure {
            content,
            structured_content: StructuredContent::present(Value::Null),
            meta: None,
            error: ProtocolError {
                code: error.code,
                message,
                retryable: error.retryable,
                details: Value::Null,
            },
        },
    }
}

/// The words before the path in a saved output's closing reminder.
const SAVED_TO: &str = "Remember: The full content was saved to ";

/// The file a saved tool output was written to, read back from the result
/// text Core wrote for it, or `None` for any other text.
pub(crate) fn saved_output_path(text: &str) -> Option<&str> {
    let start = text.rfind(SAVED_TO)? + SAVED_TO.len();
    let length = text[start..].find(".\n")?;
    Some(&text[start..start + length]).filter(|path| !path.is_empty())
}

fn build_saved_output_message(
    file_path: &str,
    serialized: &str,
    limits: CharacterLimits,
    type_definition: &str,
) -> String {
    [
        format_system_tag(&format!(
            "The tool output is large. To preserve your context window, it was saved to the file system under {file_path}.\nOutput size: {} characters\nMax size before saving: {} characters\n{}",
            utf16_len(serialized),
            limits.max_output,
            excerpt_note(limits.model_visible_output)
        )),
        format_truncated_output(serialized, limits.model_visible_output),
        format!("<output-type>{type_definition}</output-type>"),
        format_system_tag(&format!(
            "{SAVED_TO}{file_path}.\nRead the JSON's inferred type definition from the <output-type> tag first. To see an omitted part, extract the field you need with `jq` and search or slice it with `grep`, or page through the file with `read_file` using `offset` and `limit`."
        )),
    ]
    .join("\n")
}

fn build_write_error_message(
    error: &ProtocolError,
    file_path: &str,
    serialized: &str,
    tool_name: &str,
    limits: CharacterLimits,
    type_definition: &str,
) -> String {
    [
        format_system_tag(&format!(
            "The {tool_name} tool output was too large ({} characters, filesystem threshold is {}), but saving it to {file_path} failed.\nThe output also exceeds the hard inline safety limit of {HARD_INLINE_CHARACTER_LIMIT} characters. {}\nError: {}",
            utf16_len(serialized),
            limits.max_output,
            excerpt_note(HARD_INLINE_CHARACTER_LIMIT),
            error.message
        )),
        format_truncated_output(serialized, HARD_INLINE_CHARACTER_LIMIT),
        format!("<output-type>{type_definition}</output-type>"),
        format_system_tag(
            "Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result. The full content was not saved.",
        ),
    ]
    .join("\n")
}

fn build_hard_limit_message(tool_name: &str, serialized: &str, type_definition: &str) -> String {
    [
        format_system_tag(&format!(
            "The {tool_name} tool output is unreasonably large ({} characters). To protect the context window, the hard inline safety limit is {HARD_INLINE_CHARACTER_LIMIT} characters.\n{}",
            utf16_len(serialized),
            excerpt_note(HARD_INLINE_CHARACTER_LIMIT),
        )),
        format_truncated_output(serialized, HARD_INLINE_CHARACTER_LIMIT),
        format!("<output-type>{type_definition}</output-type>"),
        format_system_tag(
            "Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result.",
        ),
    ]
    .join("\n")
}

fn excerpt_note(limit: usize) -> String {
    format!(
        "The excerpt below keeps about {limit} characters of the tool response: the start and end of each long text field, or of the whole response, with the middle omitted."
    )
}

fn format_truncated_output(serialized: &str, limit: usize) -> String {
    format!(
        "<truncated-output>\n{}\n</truncated-output>",
        excerpt(serialized, limit)
    )
}

/// Shortens `serialized` JSON to about `limit` UTF-16 units.
///
/// Long string values are cut in the middle first, all to the same length, so
/// short fields such as status codes, counts, and flags stay readable. When the
/// response is mostly structure rather than text, the serialized text itself
/// keeps its start and end.
fn excerpt(serialized: &str, limit: usize) -> String {
    let length = utf16_len(serialized);
    if length <= limit {
        return serialized.to_string();
    }
    serde_json::from_str::<Value>(serialized)
        .ok()
        .and_then(|value| excerpt_string_values(&value, limit))
        .unwrap_or_else(|| cut_middle(serialized, length, limit))
}

fn excerpt_string_values(value: &Value, limit: usize) -> Option<String> {
    let mut lengths = Vec::new();
    collect_string_lengths(value, &mut lengths);
    let longest = lengths.iter().copied().max()?;
    let render = |cap: usize| {
        serde_json::to_string_pretty(&cap_string_values(value, &lengths, cap, &mut 0))
            .expect("JSON values serialize")
    };
    let mut fitting = render(0);
    if utf16_len(&fitting) > limit {
        return None;
    }
    // Find the longest cap that still fits. Uncut, at `longest`, it does not.
    let (mut low, mut high) = (0, longest);
    while low < high {
        let cap = low + (high - low).div_ceil(2);
        let candidate = render(cap);
        if utf16_len(&candidate) <= limit {
            low = cap;
            fitting = candidate;
        } else {
            high = cap - 1;
        }
    }
    Some(fitting)
}

fn collect_string_lengths(value: &Value, lengths: &mut Vec<usize>) {
    match value {
        Value::String(text) => lengths.push(utf16_len(text)),
        Value::Array(values) => values
            .iter()
            .for_each(|value| collect_string_lengths(value, lengths)),
        Value::Object(fields) => fields
            .values()
            .for_each(|value| collect_string_lengths(value, lengths)),
        Value::Null | Value::Bool(_) | Value::Number(_) => {}
    }
}

/// Copies `value` with every string longer than `cap` cut to `cap`, visiting
/// strings in the order `collect_string_lengths` measured them.
fn cap_string_values(value: &Value, lengths: &[usize], cap: usize, next: &mut usize) -> Value {
    match value {
        Value::String(text) => {
            let length = lengths[*next];
            *next += 1;
            Value::String(cut_middle(text, length, cap))
        }
        Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|value| cap_string_values(value, lengths, cap, next))
                .collect(),
        ),
        Value::Object(fields) => Value::Object(
            fields
                .iter()
                .map(|(key, value)| (key.clone(), cap_string_values(value, lengths, cap, next)))
                .collect(),
        ),
        Value::Null | Value::Bool(_) | Value::Number(_) => value.clone(),
    }
}

/// Keeps the start and end of `text`, which is `length` UTF-16 units long,
/// within `cap` units plus a marker naming how much was omitted. Text that the
/// marker would not shorten is kept whole.
fn cut_middle(text: &str, length: usize, cap: usize) -> String {
    if length <= cap {
        return text.to_string();
    }
    let head = take_utf16_prefix(text, cap.div_ceil(2));
    let tail = take_utf16_suffix(text, cap - utf16_len(head));
    let omitted = length - utf16_len(head) - utf16_len(tail);
    let marker = format!("\n[... {omitted} characters omitted ...]\n");
    if cap + utf16_len(&marker) >= length {
        return text.to_string();
    }
    format!("{head}{marker}{tail}")
}

fn format_system_tag(content: &str) -> String {
    let escaped = content
        .trim()
        .replace("</system>", "&lt;/system&gt;")
        .replace("<system>", "&lt;system&gt;");
    format!("<system>{escaped}</system>")
}

fn utf16_len(value: &str) -> usize {
    value.encode_utf16().count()
}

fn take_utf16_prefix(value: &str, limit: usize) -> &str {
    if utf16_len(value) <= limit {
        return value;
    }
    let mut used = 0;
    let mut end = 0;
    for (index, character) in value.char_indices() {
        let next = character.len_utf16();
        if used + next > limit {
            break;
        }
        used += next;
        end = index + character.len_utf8();
    }
    &value[..end]
}

fn take_utf16_suffix(value: &str, limit: usize) -> &str {
    let mut used = 0;
    let mut start = value.len();
    for (index, character) in value.char_indices().rev() {
        let next = character.len_utf16();
        if used + next > limit {
            break;
        }
        used += next;
        start = index;
    }
    &value[start..]
}

fn encode_path_segment(value: &str) -> String {
    let mut result = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric()
            || matches!(
                byte,
                b'-' | b'_' | b'.' | b'!' | b'~' | b'*' | b'\'' | b'(' | b')'
            )
        {
            result.push(char::from(byte));
        } else {
            use std::fmt::Write as _;
            write!(&mut result, "%{byte:02X}").expect("writing to a String cannot fail");
        }
    }
    result
}

fn tokens_to_characters(tokens: u32) -> usize {
    usize::try_from(tokens)
        .expect("u32 tokens fit in usize")
        .saturating_mul(CHARACTERS_PER_TOKEN)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const SCREENSHOT_SIZES: [usize; 3] = [26_250, 71_604, 300_000];

    fn default_policy() -> Policy {
        serde_json::from_value(json!({"mode": "filesystem"})).unwrap()
    }

    /// The large-output limits of a host with a small threshold: 4,000 tokens,
    /// below the 8,192-token cost of one image.
    fn small_policy() -> Policy {
        Policy::Filesystem {
            max_output_tokens: 4_000,
            model_visible_output_tokens: 2_000,
        }
    }

    /// A tool result with `text` and `images` PNG images of `raw_bytes`, each saved
    /// to a file (an embedded resource) when `file_backed`.
    fn screenshot_result(
        text: &str,
        images: usize,
        raw_bytes: usize,
        file_backed: bool,
    ) -> ToolResult {
        let data = "A".repeat(raw_bytes.div_ceil(3) * 4);
        let image = if file_backed {
            json!({
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/mistralai-vibe-session-s/attachments/a.png",
                    "mimeType": "image/png",
                    "blob": data,
                },
            })
        } else {
            json!({"type": "image", "data": data, "mimeType": "image/png"})
        };
        let mut content = vec![json!({"type": "text", "text": text})];
        content.extend(std::iter::repeat_n(image, images));
        serde_json::from_value(json!({"type": "success", "content": content})).unwrap()
    }

    fn disposition(policy: &Policy, result: ToolResult) -> ResultDisposition {
        evaluate_result(policy, "screenshot", "call", result)
            .unwrap()
            .unwrap()
            .disposition
    }

    ///
    /// *Prepare*: Screenshots of 26 KB, 71 KB and 300 KB, saved to a file or not.
    /// *Do*: Evaluate each result under the default policy.
    /// *Assert*: Each stays inline: an image never counts toward the limits.
    ///
    #[test]
    fn a_screenshot_stays_inline_whatever_its_size() {
        for raw_bytes in SCREENSHOT_SIZES {
            for file_backed in [false, true] {
                // Prepare
                let result = screenshot_result("a screenshot", 1, raw_bytes, file_backed);

                // Do
                let disposition = disposition(&default_policy(), result.clone());

                // Assert
                assert_eq!(
                    disposition,
                    ResultDisposition::Inline(result),
                    "{raw_bytes} bytes, file {file_backed}"
                );
            }
        }
    }

    ///
    /// *Prepare*: A 4,000-token threshold, below the cost of one image, and a short
    /// text with one screenshot, and with three.
    /// *Do*: Evaluate both results.
    /// *Assert*: Both stay inline, images included: an image never triggers a save.
    ///
    #[test]
    fn an_image_never_triggers_a_save() {
        for images in [1, 3] {
            // Prepare
            let result = screenshot_result("a screenshot", images, 26_250, false);

            // Do
            let disposition = disposition(&small_policy(), result.clone());

            // Assert
            assert_eq!(
                disposition,
                ResultDisposition::Inline(result),
                "{images} images"
            );
        }
    }

    ///
    /// *Prepare*: A 4,000-token threshold and a result whose text alone passes it,
    /// next to a screenshot and a screenshot saved to a file.
    /// *Do*: Evaluate the result and complete the write.
    /// *Assert*: The text is saved and the file and excerpt hold `[Image]` in
    /// place of each image, never its data; the images follow the notice, unchanged.
    ///
    #[test]
    fn saved_text_keeps_its_images_inline() {
        // Prepare
        let mut result = screenshot_result(&"x".repeat(20_000), 1, 26_250, false);
        let saved_screenshot = result_content(&screenshot_result("", 1, 26_250, true))[1].clone();
        let ToolResult::Success { content, .. } = &mut result else {
            unreachable!("screenshot results succeed");
        };
        content.push(saved_screenshot);
        let images = result_content(&result)[1..].to_vec();
        let image_data = "A".repeat(1_000);

        // Do
        let evaluated = evaluate_result(&small_policy(), "screenshot", "call", result)
            .unwrap()
            .unwrap();
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("text past the threshold is saved");
        };
        let saved = pending.serialized().to_string();
        let completed = pending.complete("/home/user/tool-results/screenshot-call.json");

        // Assert
        assert!(!saved.contains(&image_data));
        assert!(saved.contains("\"[Image]\""));
        assert!(saved.contains(
            "\"[Image] [resource: file:///tmp/mistralai-vibe-session-s/attachments/a.png]\""
        ));
        assert_eq!(evaluated.serialized_char_count, utf16_len(&saved));
        let content = result_content(&completed);
        let ContentBlock::Text(notice) = &content[0] else {
            panic!("the notice is text");
        };
        assert!(notice.text.contains("<truncated-output>"));
        assert!(
            notice
                .text
                .contains("The images of the tool output follow this notice.")
        );
        assert!(!notice.text.contains(&image_data));
        assert_eq!(&content[1..], images.as_slice());
    }

    #[test]
    fn a_saved_output_names_its_file_and_other_text_names_none() {
        let message = build_saved_output_message(
            ".vibe/tool-outputs/call-1.json",
            &"x".repeat(200),
            CharacterLimits {
                max_output: 100,
                model_visible_output: 50,
            },
            "string",
        );

        assert_eq!(
            saved_output_path(&message),
            Some(".vibe/tool-outputs/call-1.json")
        );
        assert_eq!(saved_output_path("plain tool output"), None);
    }

    #[test]
    fn filesystem_policy_deserializes_core_defaults() {
        let policy = serde_json::from_value::<Policy>(json!({
            "mode": "filesystem",
        }))
        .unwrap();

        assert_eq!(
            policy,
            Policy::Filesystem {
                max_output_tokens: DEFAULT_MAX_OUTPUT_TOKENS,
                model_visible_output_tokens: DEFAULT_MAX_OUTPUT_TOKENS,
            }
        );
    }

    #[test]
    fn filesystem_policy_defaults_only_omitted_limits() {
        let policy = serde_json::from_value::<Policy>(json!({
            "mode": "filesystem",
            "max_output_tokens": 12_000,
        }))
        .unwrap();

        assert_eq!(
            policy,
            Policy::Filesystem {
                max_output_tokens: 12_000,
                model_visible_output_tokens: DEFAULT_MAX_OUTPUT_TOKENS,
            }
        );
    }

    #[test]
    fn filesystem_policy_estimates_four_characters_per_token() {
        assert_eq!(
            Policy::Filesystem {
                max_output_tokens: 10_000,
                model_visible_output_tokens: 5_000,
            }
            .character_limits(),
            Some(CharacterLimits {
                max_output: 40_000,
                model_visible_output: 20_000,
            })
        );
    }

    #[test]
    fn filesystem_policy_rejects_a_threshold_above_the_hard_inline_limit() {
        let error = Policy::Filesystem {
            max_output_tokens: 25_001,
            model_visible_output_tokens: 5_000,
        }
        .validate()
        .unwrap_err();

        assert_eq!(
            error,
            CoreError::InvalidConfiguration {
                field: "settings.tools.large_output.max_output_tokens",
                detail: "large-output max_output_tokens must not exceed the hard inline limit of 25000 tokens".to_string(),
            }
        );
    }

    #[test]
    fn filesystem_policy_never_inlines_a_result_above_the_hard_limit() {
        let result = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(json!({
                "text": "x".repeat(HARD_INLINE_CHARACTER_LIMIT * 2),
            })),
            meta: None,
        };

        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 50_000,
                model_visible_output_tokens: 50_000,
            },
            "large_tool",
            "call",
            result,
        )
        .unwrap()
        .unwrap();

        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("filesystem mode must write results above the hard inline limit");
        };
        assert_eq!(
            pending.limits(),
            CharacterLimits {
                max_output: HARD_INLINE_CHARACTER_LIMIT,
                model_visible_output: HARD_INLINE_CHARACTER_LIMIT,
            }
        );
    }

    #[test]
    fn oversized_result_prepares_a_write_receipt_and_preserves_success() {
        let result = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(json!({"text": "x".repeat(20)})),
            meta: None,
        };
        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "web_search",
            "call-123",
            result,
        )
        .unwrap()
        .unwrap();
        assert_eq!(evaluated.serialized_char_count, 36);
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("large result must prepare a write");
        };
        assert_eq!(
            pending.relative_path(),
            "tool-results/web_search-call-123.json"
        );
        let completed = pending.complete("/home/user/tool-results/web_search-call-123.json");
        let ToolResult::Success {
            content,
            structured_content,
            ..
        } = completed
        else {
            panic!("success status is preserved");
        };
        assert_eq!(structured_content, StructuredContent::present(Value::Null));
        let ContentBlock::Text(text) = &content[0] else {
            panic!("receipt is text");
        };
        assert!(
            text.text
                .contains("/home/user/tool-results/web_search-call-123.json")
        );
        assert!(text.text.contains("<truncated-output>\n{\n  \"text\": "));
        assert!(text.text.contains("export interface Output"));
    }

    ///
    /// *Prepare*: A command result whose stdout is far longer than the excerpt budget.
    /// *Do*: Build the excerpt.
    /// *Assert*: Stdout keeps its start and end around an omission marker, and the short fields stay whole.
    ///
    #[test]
    fn excerpt_cuts_long_text_fields_and_keeps_short_fields() {
        // Prepare
        let stdout = format!(
            "{}{}{}",
            "a".repeat(5_000),
            "#".repeat(5_000),
            "z".repeat(5_000)
        );
        let serialized = serde_json::to_string_pretty(&json!({
            "command": "make test",
            "stdout": stdout,
            "stderr": "",
            "returncode": 2,
            "was_truncated": false,
        }))
        .unwrap();

        // Do
        let excerpt = excerpt(&serialized, 2_000);

        // Assert
        assert!(utf16_len(&excerpt) <= 2_000);
        let value: Value = serde_json::from_str(&excerpt).unwrap();
        assert_eq!(value["command"], "make test");
        assert_eq!(value["returncode"], 2);
        assert_eq!(value["was_truncated"], false);
        let stdout = value["stdout"].as_str().unwrap();
        assert!(stdout.starts_with("aaaa"));
        assert!(stdout.ends_with("zzzz"));
        assert!(stdout.contains(" characters omitted ...]\n"));
        assert!(!stdout.contains('#'));
    }

    ///
    /// *Prepare*: Two long text fields of different lengths and a long multi-byte field.
    /// *Do*: Build the excerpt.
    /// *Assert*: Both long fields are cut to the same kept length, the shorter one is cut no more than the longer, and characters are never split.
    ///
    #[test]
    fn excerpt_cuts_every_long_field_to_one_length() {
        // Prepare
        let serialized = serde_json::to_string_pretty(&json!({
            "first": "x".repeat(4_000),
            "second": "y".repeat(8_000),
            "emoji": "\u{1F600}".repeat(3_000),
        }))
        .unwrap();

        // Do
        let excerpt = excerpt(&serialized, 3_000);

        // Assert
        let value: Value = serde_json::from_str(&excerpt).unwrap();
        let kept = |field: &str| {
            value[field]
                .as_str()
                .unwrap()
                .chars()
                .filter(|character| ['x', 'y'].contains(character))
                .count()
        };
        assert_eq!(kept("first"), kept("second"));
        assert!(value["emoji"].as_str().unwrap().starts_with('\u{1F600}'));
        assert!(value["emoji"].as_str().unwrap().ends_with('\u{1F600}'));
    }

    ///
    /// *Prepare*: A response made of many short values, with no long text field to cut.
    /// *Do*: Build the excerpt.
    /// *Assert*: The serialized text keeps its start and end around an omission marker.
    ///
    #[test]
    fn excerpt_of_structure_keeps_the_start_and_end_of_the_text() {
        // Prepare
        let serialized = serde_json::to_string_pretty(&(0..2_000).collect::<Vec<_>>()).unwrap();

        // Do
        let excerpt = excerpt(&serialized, 200);

        // Assert
        assert!(excerpt.starts_with("[\n  0,\n  1,"));
        assert!(excerpt.ends_with("1999\n]"));
        assert!(excerpt.contains(&format!(
            "\n[... {} characters omitted ...]\n",
            utf16_len(&serialized) - 200
        )));
    }

    #[test]
    fn disabled_policy_keeps_results_within_the_hard_inline_limit_unchanged() {
        let original = ToolResult::Success {
            content: vec![ContentBlock::text("small")],
            structured_content: StructuredContent::Absent,
            meta: None,
        };

        let evaluated = evaluate_result(&Policy::Disabled, "small_tool", "call", original).unwrap();

        assert_eq!(evaluated, None);
    }

    #[test]
    fn disabled_policy_truncates_results_above_the_hard_inline_limit() {
        let result = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(json!({
                "text": "x".repeat(HARD_INLINE_CHARACTER_LIMIT * 2),
            })),
            meta: None,
        };

        let evaluated = evaluate_result(&Policy::Disabled, "large_tool", "call", result)
            .unwrap()
            .unwrap();

        assert!(evaluated.serialized_char_count > HARD_INLINE_CHARACTER_LIMIT);
        let ResultDisposition::Inline(ToolResult::Success { content, .. }) = evaluated.disposition
        else {
            panic!("disabled mode must truncate inline without a write");
        };
        let ContentBlock::Text(text) = &content[0] else {
            panic!("hard-limit receipt is text");
        };
        assert!(text.text.contains("unreasonably large"));
        assert!(text.text.contains(&format!(
            "hard inline safety limit is {HARD_INLINE_CHARACTER_LIMIT}"
        )));
        assert!(text.text.contains("<output-type>export interface Output"));
    }

    #[test]
    fn write_failure_returns_the_original_result_within_the_hard_inline_limit() {
        let original = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(json!({"text": "x".repeat(40)})),
            meta: Some(
                [("source".to_string(), json!("tool"))]
                    .into_iter()
                    .collect(),
            ),
        };
        let pending = match evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "large_tool",
            "call",
            original.clone(),
        )
        .unwrap()
        .unwrap()
        .disposition
        {
            ResultDisposition::Write(pending) => pending,
            ResultDisposition::Inline(_) => panic!("result must exceed the filesystem threshold"),
        };

        let failed = pending.fail(&ProtocolError {
            code: "filesystem_write_failed".to_string(),
            message: "filesystem full".to_string(),
            retryable: false,
            details: Value::Null,
        });

        assert_eq!(failed, original);
    }

    #[test]
    fn write_failure_above_the_hard_limit_preserves_failure_status_and_error_code() {
        let original = ToolResult::Failure {
            content: vec![ContentBlock::text(
                "x".repeat(HARD_INLINE_CHARACTER_LIMIT * 2),
            )],
            structured_content: StructuredContent::Absent,
            meta: None,
            error: ProtocolError {
                code: "tool_failed".to_string(),
                message: "tool failed".to_string(),
                retryable: false,
                details: json!({"stage": "tool"}),
            },
        };
        let pending = match evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "large_tool",
            "call",
            original,
        )
        .unwrap()
        .unwrap()
        .disposition
        {
            ResultDisposition::Write(pending) => pending,
            ResultDisposition::Inline(_) => panic!("failure must be oversized"),
        };
        let failed = pending.fail(&ProtocolError {
            code: "filesystem_write_failed".to_string(),
            message: "filesystem full".to_string(),
            retryable: false,
            details: Value::Null,
        });
        let ToolResult::Failure { content, error, .. } = failed else {
            panic!("failure status is preserved");
        };
        assert_eq!(error.code, "tool_failed");
        assert_eq!(error.details, Value::Null);
        let ContentBlock::Text(text) = &content[0] else {
            panic!("receipt is text");
        };
        assert!(text.text.contains("filesystem full"));
        assert!(text.text.contains(&format!(
            "hard inline safety limit of {HARD_INLINE_CHARACTER_LIMIT}"
        )));
    }

    #[test]
    fn top_level_json_strings_are_serialized_as_their_parsed_value() {
        let result = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(Value::String(
                r#"{"items":[1,2,3]}"#.to_string(),
            )),
            meta: None,
        };
        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 1,
                model_visible_output_tokens: 1,
            },
            "json_tool",
            "call",
            result,
        )
        .unwrap()
        .unwrap();
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("parsed JSON must exceed the configured limit");
        };
        assert_eq!(
            pending.serialized(),
            "{\n  \"items\": [\n    1,\n    2,\n    3\n  ]\n}"
        );
        assert_eq!(
            pending.type_definition(),
            "export interface Output {\nitems: number[]\n}\n"
        );
    }

    #[test]
    fn compact_structured_success_serializes_the_structured_value() {
        let value = json!({"text": "x".repeat(20)});
        let result = ToolResult::Success {
            content: vec![ContentBlock::text(
                serde_json::to_string(&value).expect("structured value serializes"),
            )],
            structured_content: StructuredContent::present(value.clone()),
            meta: None,
        };
        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "run_typescript",
            "call",
            result,
        )
        .unwrap()
        .unwrap();
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("compact structured value must still be eligible");
        };
        assert_eq!(
            pending.serialized(),
            serde_json::to_string_pretty(&value).expect("pretty JSON serializes")
        );
    }

    #[test]
    fn string_structured_value_mirrored_as_text_is_counted_once() {
        let text = "x".repeat(HARD_INLINE_CHARACTER_LIMIT * 6 / 10);
        let result = ToolResult::Success {
            content: vec![ContentBlock::text(text.clone())],
            structured_content: StructuredContent::present(Value::String(text)),
            meta: None,
        };

        let evaluated =
            evaluate_result(&Policy::Disabled, "authored_tool", "call", result).unwrap();

        assert_eq!(evaluated, None);
    }

    #[test]
    fn padded_text_mirroring_a_small_value_is_measured_at_its_own_length() {
        let padding = " ".repeat(HARD_INLINE_CHARACTER_LIMIT);
        for (text, value) in [
            (format!("{{\"ok\":{padding}true}}"), json!({"ok": true})),
            (
                format!("{{{padding}}}"),
                Value::String(format!("{{{padding}}}")),
            ),
        ] {
            let result = ToolResult::Success {
                content: vec![ContentBlock::text(text)],
                structured_content: StructuredContent::present(value),
                meta: None,
            };

            let evaluated =
                evaluate_result(&Policy::Disabled, "authored_tool", "call", result).unwrap();

            assert!(
                evaluated.is_some(),
                "padded text must still hit the hard limit"
            );
        }
    }

    #[test]
    fn structured_value_mirrored_as_differently_spaced_json_is_saved_once() {
        let value = json!({"text": "x".repeat(20), "count": 2});
        let result = ToolResult::Success {
            content: vec![ContentBlock::text(format!(
                r#"{{"text": "{}", "count": 2}}"#,
                "x".repeat(20)
            ))],
            structured_content: StructuredContent::present(value.clone()),
            meta: None,
        };
        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "authored_tool",
            "call",
            result,
        )
        .unwrap()
        .unwrap();
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("structured value must exceed the configured limit");
        };
        assert_eq!(
            pending.serialized(),
            serde_json::to_string_pretty(&value).expect("pretty JSON serializes")
        );
    }

    #[test]
    fn extra_result_content_is_serialized_with_the_completed_payload() {
        let stdout = format!("stdout:\n{}", "x".repeat(40));
        let result = ToolResult::Success {
            content: vec![
                ContentBlock::text(r#"{"ok":true}"#),
                ContentBlock::text(stdout.clone()),
            ],
            structured_content: StructuredContent::present(json!({"ok": true})),
            meta: None,
        };
        let evaluated = evaluate_result(
            &Policy::Filesystem {
                max_output_tokens: 2,
                model_visible_output_tokens: 1,
            },
            "run_typescript",
            "call",
            result,
        )
        .unwrap()
        .unwrap();
        let ResultDisposition::Write(pending) = evaluated.disposition else {
            panic!("stdout must be counted toward the filesystem threshold");
        };
        assert!(pending.serialized().contains("stdout"));
        assert!(pending.serialized().contains(&"x".repeat(40)));
        assert!(pending.serialized().contains("ok"));
        let completed = pending.complete("/home/user/tool-results/run_typescript-call.json");
        let ToolResult::Success { content, .. } = completed else {
            panic!("success status is preserved");
        };
        let ContentBlock::Text(text) = &content[0] else {
            panic!("receipt is text");
        };
        assert!(
            text.text
                .contains("/home/user/tool-results/run_typescript-call.json")
        );
        assert!(!text.text.contains(&"x".repeat(40)));
    }
}
