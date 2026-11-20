//! `read_file` on an image file: the Core-owned output schema accepts the
//! image metadata next to the text output, the model receives the image block,
//! and each exposure describes what an image read returns.

use super::*;

const READ_FILE_TEXT_DESCRIPTION: &str = "Read a text file with optional zero-indexed line offset and limit. When `was_truncated` is true, read on from `next_offset`. An offset past the end of the file is an error. A directory reads as a listing of its entries, one per line with `/` after each subdirectory, at most 500 at a time.";
const READ_FILE_DOCUMENTS: &str =
    "PDF, Word, PowerPoint, and email files return their extracted text.";
const PNG_BASE64: &str = "iVBORw0KGgoAAP8=";

fn with_read_file(read_file: Value) -> HarnessConfig {
    let mut value = serde_json::to_value(config()).unwrap();
    value["settings"]["tools"]["file_system"] = json!({"mode": "enabled", "read_file": read_file});
    serde_json::from_value(value).expect("read_file support deserializes")
}

fn image_output(path: &str) -> Value {
    json!({"path": path, "mime_type": "image/png", "file_size_bytes": 11})
}

/// The content a Runtime returns for an image read: its metadata as JSON text, then the image.
fn image_content(path: &str) -> Value {
    json!([
        {"type": "text", "text": image_output(path).to_string()},
        {"type": "image", "data": PNG_BASE64, "mimeType": "image/png"},
    ])
}

/// The result Core commits for one direct `read_file` call that returns `result`.
fn committed_read_file_result(result: Value) -> Value {
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-read-file-schema", "read the file", "queue"),
    )));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let pending = accepted_value(session.apply(input(
        2,
        tool_call_completion(
            &completion_action_id,
            vec![tool_call(
                "call-read-file-schema",
                "read_file",
                json!({"path": "picture.png"}),
            )],
        ),
    )));
    let action = &pending["transition"]["next"]["directives"][0]["action"];
    let resumed = accepted_value(session.apply(input(
        3,
        json!({
            "type": "tool_succeeded",
            "action_id": action["action_id"],
            "call_id": action["call_id"],
            "result": result,
        }),
    )));
    let committed = &resumed["transition"]["observations"][0];
    assert_eq!(committed["type"], "tool_result_committed");
    committed["result"].clone()
}

/// AC6.
/// *Prepare*: A Session declares native `read_file` images, and its model reads images.
/// *Do*: The model calls `read_file` on a PNG file, and the Runtime returns the image
/// metadata as `structured_content` with the metadata text and the image as `content`.
/// *Assert*: Core commits the result unchanged, and the next model input holds the tool
/// message with the metadata text and the image block.
#[test]
fn ac6_direct_read_file_image_reaches_the_model_as_an_image_block() {
    let turn_id = "turn-read-file-image";
    let mut runtime = SynchronousRuntime::new(with_read_file(json!({"images": "native"})));
    let first_completion = runtime.start_turn(turn_id, "look at the picture");
    let calls = vec![tool_call(
        "call-read-image",
        "read_file",
        json!({"path": "picture.png"}),
    )];
    let read = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [runtime_tool(
                "file_system.read_file",
                json!({"path": "picture.png"}),
            )],
        )
        .only_action();
    let read_result = json!({
        "type": "tool_succeeded",
        "action_id": read["action_id"],
        "call_id": read["call_id"],
        "result": {
            "type": "success",
            "content": image_content("/workspace/picture.png"),
            "structured_content": image_output("/workspace/picture.png"),
        },
    });

    let next_completion = runtime
        .apply(
            read_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&read, &read_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            json!({
                "role": "tool",
                "tool_call_id": "call-read-image",
                "name": "read_file",
                "outcome": "success",
                "content": image_content("/workspace/picture.png"),
            }),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &next_completion, "a picture");
}

/// AC6.
/// *Prepare*: Direct `read_file` results: the unchanged text output, the image metadata
/// with its image, then shapes that mix or drop fields of the two.
/// *Do*: Return each one to a fresh Session as a successful Runtime built-in result.
/// *Assert*: The text and image outputs are committed as successes; the mixed and
/// incomplete shapes become the Core-owned output schema failure.
#[test]
fn ac6_read_file_output_schema_accepts_text_and_image_and_rejects_mixed_shapes() {
    let text_output = json!({
        "path": "picture.png",
        "content": "content",
        "file_size_bytes": 7,
        "returned_bytes": 7,
        "offset": 0,
        "lines_read": 1,
        "was_truncated": false,
    });
    let mut image_with_content = image_output("picture.png");
    image_with_content["content"] = json!("content");
    let mut image_without_mime_type = image_output("picture.png");
    image_without_mime_type
        .as_object_mut()
        .unwrap()
        .remove("mime_type");
    let cases = [
        ("text output", text_output, json!([]), true),
        (
            "image output",
            image_output("picture.png"),
            image_content("picture.png"),
            true,
        ),
        (
            "image output with content",
            image_with_content,
            image_content("picture.png"),
            false,
        ),
        (
            "image output without mime_type",
            image_without_mime_type,
            image_content("picture.png"),
            false,
        ),
    ];

    for (case, structured_content, content, accepted) in cases {
        let committed = committed_read_file_result(json!({
            "type": "success",
            "content": content,
            "structured_content": structured_content,
        }));

        if accepted {
            assert_eq!(committed["type"], "success", "{case}: {committed}");
            assert_eq!(
                committed["structured_content"], structured_content,
                "{case}"
            );
        } else {
            assert_eq!(committed["type"], "failure", "{case}: {committed}");
            assert_eq!(
                committed["error"]["code"], "runtime_builtin_output_schema_mismatch",
                "{case}"
            );
        }
    }
}

/// AC7.
/// *Prepare*: Sessions that declare native `read_file` images, alone and with extracted
/// document text, for a model that reads images.
/// *Do*: Compile their tools and select the direct `read_file` definition and the
/// `tools.file_system.read_file` declaration.
/// *Assert*: The direct description still ends with the image sentence; the declaration a
/// program calls ends with the sentence that an image read returns its media type and size.
#[test]
fn ac7_programmatic_read_file_describes_the_image_media_type_and_size() {
    let descriptions = |config: &HarnessConfig| {
        let tools = crate::core::tools::resolved::ResolvedTools::compile(config)
            .expect("the configuration compiles");
        let direct = tools
            .top_level_tools()
            .iter()
            .find(|tool| tool.name == "read_file")
            .expect("Core exposes read_file directly")
            .description
            .clone();
        let programmatic = tools
            .program_descriptors()
            .iter()
            .find(|tool| {
                tool.programmatic_name.namespace == "file_system"
                    && tool.programmatic_name.name == "read_file"
            })
            .expect("Core declares tools.file_system.read_file")
            .description
            .clone();
        (direct, programmatic)
    };

    let native_images = descriptions(&with_read_file(json!({"images": "native"})));
    let documents_and_native_images = descriptions(&with_read_file(
        json!({"documents": "extracted_text", "images": "native"}),
    ));

    assert_eq!(
        native_images,
        (
            format!(
                "{READ_FILE_TEXT_DESCRIPTION} An image file returns the image itself. `offset` and `limit` do not apply to an image file."
            ),
            format!(
                "{READ_FILE_TEXT_DESCRIPTION} An image file returns its media type and size. `offset` and `limit` do not apply to an image file."
            ),
        )
    );
    assert_eq!(
        documents_and_native_images,
        (
            format!(
                "{READ_FILE_TEXT_DESCRIPTION} {READ_FILE_DOCUMENTS} An image file returns the image itself. `offset` and `limit` do not apply to an image file."
            ),
            format!(
                "{READ_FILE_TEXT_DESCRIPTION} {READ_FILE_DOCUMENTS} An image file returns its media type and size. `offset` and `limit` do not apply to an image file."
            ),
        )
    );
}
