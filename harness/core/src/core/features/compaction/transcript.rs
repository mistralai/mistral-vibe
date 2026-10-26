//! One-line renderings of history items, and the reduced view built from
//! them.
//!
//! The standalone summary request renders the history it covers in full. A
//! rebuilt history that cannot put back every item after the summary renders
//! the dropped items clipped, or as one line each, whichever first fits the
//! room left, and names them as omitted when neither does. It never renders
//! them in full: that is about the size of putting them back as they were,
//! which did not fit. The view gives no
//! instruction: the model decides what to redo. Everything here is pure: no
//! I/O and no session state, so the same items always render the same text.

use serde_json::Value;

use crate::core::features::large_output::saved_output_path;
use crate::core::model_input_projection::{IMAGE_PLACEHOLDER, is_image_content};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{
    AssistantPart, AssistantSemanticPart, Message, ToolArguments, ToolOutcome,
};

/// How much of each item a rendering keeps.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Detail {
    /// The whole item, images as `[Image]`: the standalone transcript.
    Full,
    /// The first and last `CLIPPED_CHARS / 2` characters of the item's full
    /// text, with the count it omits: the reduced view's first level.
    Clipped,
    /// The item's kind and size only: the reduced view's second level.
    Shape,
}

/// The characters a clipped rendering keeps of a long item, half from its
/// start and half from its end.
pub(crate) const CLIPPED_CHARS: usize = 100;

/// One history item, borrowed from the stored messages.
#[derive(Clone, Copy, Debug)]
pub(crate) enum Item<'a> {
    ToolCall {
        name: &'a str,
        arguments: &'a ToolArguments,
    },
    ToolResult {
        outcome: ToolOutcome,
        content: &'a [ContentBlock],
    },
    UserMessage {
        content: &'a [ContentBlock],
    },
    /// Assistant text: the standalone transcript only, since a rebuilt
    /// history puts the reply back whole.
    AssistantText {
        content: &'a [ContentBlock],
    },
    /// A system message imported into the history: the standalone transcript
    /// only.
    SystemMessage {
        content: &'a [ContentBlock],
    },
}

/// Renders one item at one level of detail. The same item and level always
/// give the same text.
pub(crate) fn render(item: &Item, detail: Detail) -> String {
    match item {
        Item::ToolCall { name, arguments } => render_tool_call(name, arguments, detail),
        Item::ToolResult { outcome, content } => render_tool_result(*outcome, content, detail),
        Item::UserMessage { content } => render_message("UserMessage", content, detail),
        Item::AssistantText { content } => render_message("AssistantMessage", content, detail),
        Item::SystemMessage { content } => render_message("SystemMessage", content, detail),
    }
}

/// The items of one stored message, in order. Reasoning is private to the
/// turn that produced it and is left out, as is blank assistant text.
pub(super) fn message_items(message: &Message) -> Vec<Item<'_>> {
    match message {
        Message::System { content } => vec![Item::SystemMessage { content }],
        Message::User { content } => vec![Item::UserMessage { content }],
        Message::Tool {
            outcome, content, ..
        } => vec![Item::ToolResult {
            outcome: *outcome,
            content,
        }],
        Message::Assistant { content } => content
            .iter()
            .filter_map(|part| match part {
                AssistantPart::Content(ContentBlock::Text(text)) if text.text.trim().is_empty() => {
                    None
                }
                AssistantPart::Content(block) => Some(Item::AssistantText {
                    content: std::slice::from_ref(block),
                }),
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                    name,
                    arguments,
                    ..
                }) => Some(Item::ToolCall { name, arguments }),
                AssistantPart::Semantic(AssistantSemanticPart::Reasoning { .. }) => None,
            })
            .collect(),
    }
}

/// The reduced view of the items a rebuilt history could not put back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum ReducedView {
    /// Every item clipped, under the header.
    Clipped { text: String },
    /// Every item as its shape, under the header: the clipped view did not fit.
    Shape { text: String },
    /// The omitted line alone: not even the shapes fit.
    Omitted { text: String },
}

impl ReducedView {
    pub(crate) fn text(&self) -> &str {
        match self {
            Self::Clipped { text } | Self::Shape { text } | Self::Omitted { text } => text,
        }
    }

    /// The view's level as the `compaction_inputs_reduced` warning names it.
    pub(crate) fn level(&self) -> &'static str {
        match self {
            Self::Clipped { .. } => "clipped",
            Self::Shape { .. } => "shape",
            Self::Omitted { .. } => OMITTED_LEVEL,
        }
    }
}

/// The level the omitted line stands for in the `compaction_inputs_reduced`
/// warning.
pub(crate) const OMITTED_LEVEL: &str = "omitted";

/// The header and the omitted line, from Core's compaction prompt texts.
#[derive(Clone, Copy, Debug)]
pub(crate) struct ViewTexts<'a> {
    pub header: &'a str,
    pub omitted: &'a str,
}

/// The first of the clipped and the shape view, header included, that fits
/// `room` by `estimate`; the omitted line when neither does.
pub(crate) fn reduce(
    items: &[Item],
    room: u64,
    estimate: impl Fn(&str) -> u64,
    texts: &ViewTexts,
) -> ReducedView {
    let rendered = |detail| view(texts.header, items.iter().map(|item| render(item, detail)));
    let clipped = rendered(Detail::Clipped);
    if estimate(&clipped) <= room {
        return ReducedView::Clipped { text: clipped };
    }
    let shape = rendered(Detail::Shape);
    if estimate(&shape) <= room {
        return ReducedView::Shape { text: shape };
    }
    ReducedView::Omitted {
        text: texts.omitted.to_string(),
    }
}

fn view(header: &str, lines: impl Iterator<Item = String>) -> String {
    std::iter::once(header.to_string())
        .chain(lines)
        .collect::<Vec<_>>()
        .join("\n")
}

/// `name(key=value, …)` in full, the whole call clipped, or
/// `name(2 args, 1,318 characters)` as a shape.
fn render_tool_call(name: &str, arguments: &ToolArguments, detail: Detail) -> String {
    let full = match arguments {
        ToolArguments::Json {
            value: Value::Object(fields),
            ..
        } => {
            let fields = fields
                .iter()
                .map(|(key, value)| format!("{key}={value}"))
                .collect::<Vec<_>>()
                .join(", ");
            format!("{name}({fields})")
        }
        ToolArguments::Json { raw, .. } | ToolArguments::InvalidJson { raw, .. } => {
            format!("{name}({raw})")
        }
    };
    match detail {
        Detail::Full => full,
        Detail::Clipped => clip(&full),
        Detail::Shape => {
            let size = characters(char_count(&full));
            match arguments {
                ToolArguments::Json {
                    value: Value::Object(fields),
                    ..
                } => {
                    let count = fields.len();
                    let noun = if count == 1 { "arg" } else { "args" };
                    format!("{name}({count} {noun}, {size})")
                }
                _ => format!("{name}({size})"),
            }
        }
    }
}

/// `<OK>` or `<ERROR>` and the content, clipped, or its size. A result saved
/// to a file keeps the file's path at every level, so the model can read it
/// again.
fn render_tool_result(outcome: ToolOutcome, content: &[ContentBlock], detail: Detail) -> String {
    let status = match outcome {
        ToolOutcome::Success => "<OK>",
        ToolOutcome::Failure => "<ERROR>",
    };
    let text = render_blocks(content);
    let saved = saved_output_path(&text);
    match detail {
        Detail::Full => format!("{status} {text}"),
        Detail::Clipped => {
            let clipped = clip(&text);
            match saved {
                Some(path) if clipped != text => format!("{status} {clipped} (saved to {path})"),
                _ => format!("{status} {clipped}"),
            }
        }
        Detail::Shape => {
            let size = characters(char_count(&text));
            match saved {
                Some(path) => format!("{status} result, {size}, saved to {path}"),
                None => format!("{status} result, {size}"),
            }
        }
    }
}

fn render_message(label: &str, content: &[ContentBlock], detail: Detail) -> String {
    match detail {
        Detail::Full => format!("{label}: {}", render_blocks(content)),
        Detail::Clipped => format!("{label}: {}", clip(&render_blocks(content))),
        Detail::Shape => label.to_string(),
    }
}

/// The first and last `CLIPPED_CHARS / 2` characters of `text` around a note
/// naming how many it omits; text no longer than `CLIPPED_CHARS` unchanged.
/// Counts and cuts characters, never bytes, so a cut never splits one.
fn clip(text: &str) -> String {
    let count = char_count(text);
    if count <= CLIPPED_CHARS {
        return text.to_string();
    }
    let head = CLIPPED_CHARS / 2;
    let tail = CLIPPED_CHARS - head;
    let head_end = text
        .char_indices()
        .nth(head)
        .map_or(text.len(), |(index, _)| index);
    let tail_start = text
        .char_indices()
        .nth(count - tail)
        .map_or(text.len(), |(index, _)| index);
    format!(
        "{}[CONTENT OMITTED FOR SIZE: {} MORE CHARACTERS]{}",
        &text[..head_end],
        count - CLIPPED_CHARS,
        &text[tail_start..]
    )
}

fn char_count(text: &str) -> usize {
    text.chars().count()
}

/// `1 character`, `1,318 characters`.
fn characters(count: usize) -> String {
    let digits = count.to_string();
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(digit);
    }
    let noun = if count == 1 {
        "character"
    } else {
        "characters"
    };
    format!("{grouped} {noun}")
}

pub(super) fn render_blocks(content: &[ContentBlock]) -> String {
    content
        .iter()
        .map(render_block)
        .collect::<Vec<_>>()
        .join("\n")
}

fn render_block(block: &ContentBlock) -> String {
    match block {
        ContentBlock::Text(content) => content.text.clone(),
        block if is_image_content(block) => IMAGE_PLACEHOLDER.to_string(),
        ContentBlock::Audio(_) => "[audio]".to_string(),
        block => render_resource(block),
    }
}

fn render_resource(block: &ContentBlock) -> String {
    let value = serde_json::to_value(block).unwrap_or(Value::Null);
    let resource = value.get("resource").unwrap_or(&value);
    let uri = resource
        .get("uri")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    match resource.get("text").and_then(Value::as_str) {
        Some(text) => format!("[resource {uri}]\n{text}"),
        None => format!("[resource {uri}]"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::prompt::PromptSettings;
    use serde_json::json;

    fn json_arguments(value: Value) -> ToolArguments {
        ToolArguments::Json {
            raw: value.to_string(),
            value,
        }
    }

    fn texts(prompts: &crate::core::features::compaction::Prompts) -> ViewTexts<'_> {
        ViewTexts {
            header: &prompts.reduced_view_header,
            omitted: &prompts.reduced_view_omitted,
        }
    }

    /// Core's usual estimate: four bytes a token, rounded up.
    fn estimate(text: &str) -> u64 {
        text.len().div_ceil(4) as u64
    }

    /// The write of section 3 of the design: 1,318 characters at level 0.
    fn write_file_arguments() -> ToolArguments {
        let content = "blabla file content ".repeat(64);
        let content = &content[..1_274];
        json_arguments(json!({"path": "./blabla.json", "content": content}))
    }

    #[test]
    fn a_tool_call_renders_at_each_level() {
        let arguments = write_file_arguments();
        let item = Item::ToolCall {
            name: "write_file",
            arguments: &arguments,
        };

        let full = render(&item, Detail::Full);
        assert_eq!(char_count(&full), 1_318);
        assert!(full.starts_with(r#"write_file(path="./blabla.json", content="blabla file"#));
        assert_eq!(
            render(&item, Detail::Clipped),
            r#"write_file(path="./blabla.json", content="blabla f[CONTENT OMITTED FOR SIZE: 1218 MORE CHARACTERS] file content blabla file content blabla file co")"#
        );
        assert_eq!(
            render(&item, Detail::Shape),
            "write_file(2 args, 1,318 characters)"
        );
    }

    #[test]
    fn a_call_without_an_argument_object_renders_its_raw_arguments() {
        let invalid = ToolArguments::InvalidJson {
            raw: "{\"path\": ".to_string(),
            error: "EOF".to_string(),
        };
        let item = Item::ToolCall {
            name: "read_file",
            arguments: &invalid,
        };
        assert_eq!(render(&item, Detail::Full), "read_file({\"path\": )");
        assert_eq!(render(&item, Detail::Shape), "read_file(20 characters)");

        let one = json_arguments(json!({"command": "ls"}));
        let item = Item::ToolCall {
            name: "bash",
            arguments: &one,
        };
        assert_eq!(render(&item, Detail::Full), r#"bash(command="ls")"#);
        assert_eq!(render(&item, Detail::Shape), "bash(1 arg, 18 characters)");
    }

    #[test]
    fn a_tool_result_renders_its_outcome_at_each_level() {
        let content = vec![ContentBlock::text("x".repeat(100_507))];
        let ok = Item::ToolResult {
            outcome: ToolOutcome::Success,
            content: &content,
        };
        let error = Item::ToolResult {
            outcome: ToolOutcome::Failure,
            content: &content,
        };

        assert_eq!(
            render(&ok, Detail::Full),
            format!("<OK> {}", "x".repeat(100_507))
        );
        assert_eq!(
            render(&ok, Detail::Clipped),
            format!(
                "<OK> {}[CONTENT OMITTED FOR SIZE: 100407 MORE CHARACTERS]{}",
                "x".repeat(50),
                "x".repeat(50)
            )
        );
        assert_eq!(
            render(&ok, Detail::Shape),
            "<OK> result, 100,507 characters"
        );
        assert_eq!(
            render(&error, Detail::Shape),
            "<ERROR> result, 100,507 characters"
        );
    }

    #[test]
    fn messages_render_their_label_and_text_at_each_level() {
        let long = format!("{}{}", "a".repeat(60), "b".repeat(60));
        let content = vec![ContentBlock::text(long)];
        let user = Item::UserMessage { content: &content };
        let assistant = Item::AssistantText { content: &content };
        let system = Item::SystemMessage { content: &content };

        assert_eq!(
            render(&user, Detail::Full),
            format!("UserMessage: {}{}", "a".repeat(60), "b".repeat(60))
        );
        assert_eq!(
            render(&user, Detail::Clipped),
            format!(
                "UserMessage: {}[CONTENT OMITTED FOR SIZE: 20 MORE CHARACTERS]{}",
                "a".repeat(50),
                "b".repeat(50)
            )
        );
        assert_eq!(render(&user, Detail::Shape), "UserMessage");
        assert_eq!(render(&assistant, Detail::Shape), "AssistantMessage");
        assert_eq!(render(&system, Detail::Shape), "SystemMessage");
        assert!(render(&assistant, Detail::Full).starts_with("AssistantMessage: aaa"));
    }

    #[test]
    fn short_text_is_never_clipped() {
        let exact = "c".repeat(CLIPPED_CHARS);
        assert_eq!(clip(&exact), exact);
        assert_eq!(
            clip(&"c".repeat(CLIPPED_CHARS + 1)),
            format!(
                "{}[CONTENT OMITTED FOR SIZE: 1 MORE CHARACTERS]{}",
                "c".repeat(50),
                "c".repeat(50)
            )
        );
    }

    #[test]
    fn a_clip_cuts_characters_on_utf8_boundaries() {
        let text = format!("{}{}{}", "é".repeat(60), "middle", "日".repeat(60));

        let clipped = clip(&text);

        assert_eq!(
            clipped,
            format!(
                "{}[CONTENT OMITTED FOR SIZE: 26 MORE CHARACTERS]{}",
                "é".repeat(50),
                "日".repeat(50)
            )
        );
    }

    #[test]
    fn images_render_as_a_marker() {
        let content = vec![
            ContentBlock::text("look at this"),
            ContentBlock::image("aGVsbG8=", "image/png"),
        ];
        let user = Item::UserMessage { content: &content };
        let result = Item::ToolResult {
            outcome: ToolOutcome::Success,
            content: &content,
        };

        assert_eq!(
            render(&user, Detail::Full),
            "UserMessage: look at this\n[Image]"
        );
        assert_eq!(render(&result, Detail::Full), "<OK> look at this\n[Image]");
    }

    #[test]
    fn a_capped_result_keeps_its_notice_and_recovery_note_when_clipped() {
        let capped = format!(
            "<system>The bash tool output is unreasonably large (412388 characters).</system>\n{}\n<system>Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result.</system>",
            "log line\n".repeat(1_000)
        );
        let content = vec![ContentBlock::text(capped)];
        let item = Item::ToolResult {
            outcome: ToolOutcome::Success,
            content: &content,
        };

        let clipped = render(&item, Detail::Clipped);

        assert!(clipped.starts_with("<OK> <system>The bash tool output is unreasonably large["));
        assert!(clipped.ends_with(" or ask for a summarized/filtered result.</system>"));
    }

    #[test]
    fn a_saved_result_keeps_its_path_at_every_level() {
        let saved = format!(
            "<system>The tool output is large. To preserve your context window, it was saved to the file system under .vibe/out/call-1.json.</system>\n{}\n<system>Remember: The full content was saved to .vibe/out/call-1.json.\nRead the JSON's inferred type definition first.</system>",
            "row\n".repeat(500)
        );
        let content = vec![ContentBlock::text(saved)];
        let item = Item::ToolResult {
            outcome: ToolOutcome::Success,
            content: &content,
        };

        assert!(render(&item, Detail::Full).contains(".vibe/out/call-1.json"));
        assert!(render(&item, Detail::Clipped).ends_with(" (saved to .vibe/out/call-1.json)"));
        assert!(render(&item, Detail::Shape).ends_with(", saved to .vibe/out/call-1.json"));
    }

    /// Section 3's worked example: eight capped log results after the
    /// reply's eight bash calls.
    fn worked_example() -> (Vec<ToolArguments>, Vec<Vec<ContentBlock>>) {
        let arguments = (0..8)
            .map(|_| json_arguments(json!({"command": "kubectl logs deploy/api --since=2h"})))
            .collect();
        let notice =
            "<system>The bash tool output is unreasonably large (412388 characters).</system>\n";
        let recovery = "\n<system>Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result.</system>";
        let excerpt = "l".repeat(100_507 - notice.len() - recovery.len());
        let results = (0..8)
            .map(|_| vec![ContentBlock::text(format!("{notice}{excerpt}{recovery}"))])
            .collect();
        (arguments, results)
    }

    fn worked_items<'a>(
        arguments: &'a [ToolArguments],
        results: &'a [Vec<ContentBlock>],
    ) -> Vec<Item<'a>> {
        arguments
            .iter()
            .map(|arguments| Item::ToolCall {
                name: "bash",
                arguments,
            })
            .chain(results.iter().map(|content| Item::ToolResult {
                outcome: ToolOutcome::Success,
                content,
            }))
            .collect()
    }

    #[test]
    fn the_loop_picks_the_first_reduced_level_that_fits() {
        let prompts = PromptSettings::default().compaction;
        let texts = texts(&prompts);
        let (arguments, results) = worked_example();
        let items = worked_items(&arguments, &results);
        let size = |detail| {
            estimate(&view(
                texts.header,
                items.iter().map(|item| render(item, detail)),
            ))
        };
        let (full, clipped, shape) = (
            size(Detail::Full),
            size(Detail::Clipped),
            size(Detail::Shape),
        );
        assert!(
            full > 200_000 && clipped < 600 && shape < 200,
            "{full} {clipped} {shape}"
        );

        for (room, expected) in [
            (full, "clipped"),
            (clipped, "clipped"),
            (clipped - 1, "shape"),
            (shape, "shape"),
            (shape - 1, OMITTED_LEVEL),
        ] {
            let reduced = reduce(&items, room, estimate, &texts);
            assert_eq!(reduced.level(), expected, "room {room}");
            match &reduced {
                ReducedView::Clipped { text } | ReducedView::Shape { text } => {
                    assert!(text.starts_with(texts.header));
                    assert!(estimate(text) <= room);
                }
                ReducedView::Omitted { text } => assert_eq!(text, texts.omitted),
            }
        }

        // The rooms of section 3 of the design: the reply's 8 results are
        // clipped even in 210k, where they would fit in full, and in the 188k
        // left once the exchange is dropped, as one line each in 300 tokens,
        // and not at all in 100.
        for (room, level) in [
            (210_000, "clipped"),
            (188_000, "clipped"),
            (300, "shape"),
            (100, OMITTED_LEVEL),
        ] {
            assert_eq!(
                reduce(&items, room, estimate, &texts).level(),
                level,
                "room {room}"
            );
        }
    }

    #[test]
    fn the_shape_view_is_one_line_per_item_under_the_header() {
        let prompts = PromptSettings::default().compaction;
        let texts = texts(&prompts);
        let arguments = json_arguments(json!({"path": "./x.json"}));
        let content = vec![ContentBlock::text("also check tests")];
        let result = vec![ContentBlock::text("x".repeat(5_000))];
        let items = [
            Item::ToolCall {
                name: "read_file",
                arguments: &arguments,
            },
            Item::ToolResult {
                outcome: ToolOutcome::Failure,
                content: &result,
            },
            Item::UserMessage { content: &content },
        ];
        let shape = view(
            texts.header,
            items.iter().map(|item| render(item, Detail::Shape)),
        );

        let reduced = reduce(&items, estimate(&shape), estimate, &texts);

        assert_eq!(
            reduced,
            ReducedView::Shape {
                text: format!(
                    "{}\nread_file(1 arg, 26 characters)\n<ERROR> result, 5,000 characters\nUserMessage",
                    texts.header
                ),
            }
        );
        assert_eq!(
            reduced.text(),
            reduce(&items, estimate(&shape), estimate, &texts).text(),
            "same input, same text"
        );
    }

    #[test]
    fn thousands_are_grouped() {
        assert_eq!(characters(0), "0 characters");
        assert_eq!(characters(1), "1 character");
        assert_eq!(characters(999), "999 characters");
        assert_eq!(characters(1_000), "1,000 characters");
        assert_eq!(characters(1_234_567), "1,234,567 characters");
    }
}
