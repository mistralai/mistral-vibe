use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::error::CoreError;
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

pub(crate) fn evaluate_result(
    policy: &Policy,
    tool_name: &str,
    call_id: &str,
    result: ToolResult,
) -> Result<Option<EvaluatedResult>, CoreError> {
    let limits = policy.character_limits();
    let value = parse_top_level_json_string(value_for_serialization(&result));
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
            serde_json::to_string(value).is_ok_and(|compact| compact == text.text)
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

fn replace_result(original: ToolResult, message: String) -> ToolResult {
    let content = vec![ContentBlock::text(message.clone())];
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

fn build_saved_output_message(
    file_path: &str,
    serialized: &str,
    limits: CharacterLimits,
    type_definition: &str,
) -> String {
    [
        format_system_tag(&format!(
            "The tool output is large. To preserve your context window, it was saved to the file system under {file_path}.\nOutput size: {} characters\nMax size before saving: {} characters\nThe truncated output below contains only the first {} characters of the tool response.",
            utf16_len(serialized),
            limits.max_output,
            limits.model_visible_output
        )),
        format_truncated_output(serialized, limits.model_visible_output),
        format!("<output-type>{type_definition}</output-type>"),
        format_system_tag(&format!(
            "Remember: The full content was saved to {file_path}.\nRead the JSON's inferred type definition from the <output-type> tag first, then inspect the saved JSON with `jq` before searching them with `grep` instead of `read_file`."
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
            "The {tool_name} tool output was too large ({} characters, filesystem threshold is {}), but saving it to {file_path} failed.\nThe output also exceeds the hard inline safety limit of {HARD_INLINE_CHARACTER_LIMIT} characters. The truncated output below contains only the first {HARD_INLINE_CHARACTER_LIMIT} characters of the tool response.\nError: {}",
            utf16_len(serialized),
            limits.max_output,
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
            "The {tool_name} tool output is unreasonably large ({} characters). To protect the context window, the hard inline safety limit is {HARD_INLINE_CHARACTER_LIMIT} characters.\nThe truncated output below contains only the first {HARD_INLINE_CHARACTER_LIMIT} characters of the tool response.",
            utf16_len(serialized),
        )),
        format_truncated_output(serialized, HARD_INLINE_CHARACTER_LIMIT),
        format!("<output-type>{type_definition}</output-type>"),
        format_system_tag(
            "Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result.",
        ),
    ]
    .join("\n")
}

fn format_truncated_output(serialized: &str, limit: usize) -> String {
    format!(
        "<truncated-output>\n{}…\n</truncated-output>",
        take_utf16_prefix(serialized, limit)
    )
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
        assert!(text.text.contains("<truncated-output>\n{\n  …"));
        assert!(text.text.contains("export interface Output"));
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
