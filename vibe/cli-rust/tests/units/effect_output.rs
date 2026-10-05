//! Effect result-body formatting: todo buckets, shell transcript, sanitized bodies, edit parsing.

use serde_json::{json, Value};
use vibe_rs::server::effect_output::{format_effect_output, todo_rows, BodyLine};
use vibe_rs::server::FileEditEffectOutput;

fn format(kind: Option<&str>, output: Option<&Value>) -> Vec<String> {
    format_effect_output(kind, output)
        .into_iter()
        .map(|line| line.text)
        .collect()
}

#[test]
fn file_edit_output_tolerates_an_absent_or_null_legacy_pair() {
    for output in [
        json!({"file": "f", "occurrences": [{"startLine": 1, "oldText": "a", "newText": "b"}]}),
        json!({"file": "f", "oldString": null, "newString": null, "occurrences": []}),
    ] {
        let parsed: FileEditEffectOutput = serde_json::from_value(output).unwrap();
        assert_eq!(parsed.old_string, None);
        assert_eq!(parsed.new_string, None);
    }
}

#[test]
fn todo_rows_bucket_statuses_in_widget_order() {
    let output = json!({"todos": [
        {"id": "1", "content": "Write", "status": "completed"},
        {"id": "2", "content": "Run", "status": "in_progress"},
        {"id": "3", "content": "File", "status": "pending"},
        {"id": "4", "content": "Port", "status": "cancelled"},
        {"id": "5", "content": "Stray", "status": "weird"}
    ]});
    let todos = todo_rows(&output);
    let texts: Vec<&str> = todos.iter().map(|row| row.text.as_str()).collect();
    assert_eq!(
        texts,
        ["☐ Run", "☐ File", "☑ Write", "☒ Port"],
        "an unknown status is dropped, like Python's bucket filter"
    );
}

#[test]
fn todo_rows_render_no_todos_when_the_list_is_empty() {
    assert_eq!(todo_rows(&json!({"todos": []}))[0].text, "No todos");
    assert_eq!(todo_rows(&json!({}))[0].text, "No todos");
}

#[test]
fn shell_output_falls_back_to_no_content() {
    let output = json!({"stdout": "", "stderr": "", "output": ""});
    assert_eq!(format(Some("shell"), Some(&output)), ["(no content)"]);
    let blank = json!({"stdout": "\n\n", "stderr": "", "output": ""});
    assert_eq!(format(Some("shell"), Some(&blank)), ["(no content)"]);
    let text = json!({"stdout": "done", "stderr": "", "output": ""});
    assert_eq!(format(Some("shell"), Some(&text)), ["done"]);
    let ansi = json!({"stdout": "\x1b[31mdone\x1b[0m", "stderr": "", "output": ""});
    assert_eq!(format(Some("shell"), Some(&ansi)), ["done"]);
    let streamed = json!({"stdout": "done", "stderr": "err", "output": "streamed"});
    assert_eq!(format(Some("shell"), Some(&streamed)), ["streamed"]);
    let split = json!({"stdout": "out", "stderr": "err", "output": ""});
    assert_eq!(format(Some("shell"), Some(&split)), ["out", "err"]);
    let merged = json!({"stdout": "out\n", "stderr": "err", "output": ""});
    assert_eq!(format(Some("shell"), Some(&merged)), ["out", "err"]);
}

#[test]
fn file_output_is_sanitized_before_stripping_read_line_numbers() {
    let content = "old\r\x1b[31m   1→first\x1b[0m\r\n   2→\t\x1b]0;hidden\x07second\x00\x08\x7f\n";
    let output = json!({"content": content});
    assert_eq!(
        format(Some("file_read"), Some(&output)),
        ["first", "\tsecond"]
    );
    assert_eq!(
        format(Some("file_write"), Some(&output)),
        ["   1→first", "   2→\tsecond"]
    );
    for kind in ["file_read", "file_write"] {
        let controls = json!({"content": "\x1b[2J\x1b[H\x07\x00"});
        assert!(format(Some(kind), Some(&controls)).is_empty());
        assert!(format(Some(kind), Some(&json!({}))).is_empty());
    }
}

#[test]
fn shell_output_sanitizes_merged_and_separate_streams() {
    let stdout = "old\r\x1b[31mout\x1b[0m\r\n";
    let stderr = "\x1b]8;;https://example.com\x1b\\err\x1b]8;;\x1b\\\x07\x00";
    for output in [
        json!({"stdout": stdout, "stderr": stderr}),
        json!({"output": format!("{stdout}{stderr}")}),
    ] {
        assert_eq!(format(Some("shell"), Some(&output)), ["out", "err"]);
    }
}

#[test]
fn generic_output_is_sanitized() {
    let ansi = json!({"result": "\x1b[31mhello\x1b[0m"});
    assert_eq!(format(Some("mcp"), Some(&ansi)), ["result: hello"]);
    let control_only = json!({"content": "\x1b[2K\r"});
    assert_eq!(format(Some("custom"), Some(&control_only)), ["content: "]);
    let bare = json!("\x1b[2K\r");
    assert!(format(Some("custom"), Some(&bare)).is_empty());
}

#[test]
fn subagent_output_is_sanitized() {
    // `TaskResult.response` comes from child `AssistantEvent.content`; Python `_yield_text` sanitizes it.
    let ansi = json!({"response": "\x1b[31mdone\x1b[0m", "completed": true});
    assert_eq!(
        format(Some("subagent"), Some(&ansi)),
        ["response: done", "completed: True"]
    );
    let control = json!("\x1b[2K\r");
    assert!(format(Some("subagent"), Some(&control)).is_empty());
}

#[test]
fn web_search_source_rows_carry_their_url() {
    let output = json!({
        "query": "zidane",
        "sources": [
            {"title": "Zidane - Wikipedia", "url": "https://w.org/z"},
            {"title": "", "url": "https://bbc.com/z"},
            {"title": "", "url": ""},
        ],
    });
    let link = |text: &str, url: &str| BodyLine::source(text.to_owned(), Some(url.to_owned()));
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&output)),
        [
            BodyLine::from("query: zidane".to_owned()),
            BodyLine::from(String::new()),
            BodyLine::from("Sources:".to_owned()),
            link("Zidane - Wikipedia", "https://w.org/z"),
            link("https://bbc.com/z", "https://bbc.com/z"),
        ]
    );
}

#[test]
fn web_search_single_source_has_no_sources_heading() {
    // Python only titles the list when there is more than one source.
    let output = json!({
        "query": "zidane",
        "sources": [{"title": "Zidane", "url": "https://w.org/z"}],
    });
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&output)),
        [
            BodyLine::from("query: zidane".to_owned()),
            BodyLine::from(String::new()),
            BodyLine::source("Zidane".to_owned(), Some("https://w.org/z".to_owned())),
        ]
    );
}

#[test]
fn web_search_query_and_titles_are_sanitized_to_one_row() {
    // Both come from the web: escapes never reach the terminal, a title with
    // newlines stays one bullet, and one that sanitizes away falls back to the URL.
    let output = json!({
        "query": "\x1b[31mzidane\x1b[0m",
        "sources": [
            {"title": "Zidane\n\t- \x1b]0;pwned\x07Wikipedia\r\n", "url": "https://w.org/z"},
            {"title": "\x1b[2K", "url": "https://bbc.com/z"},
        ],
    });
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&output)),
        [
            BodyLine::from("query: zidane".to_owned()),
            BodyLine::from(String::new()),
            BodyLine::from("Sources:".to_owned()),
            BodyLine::source(
                "Zidane - Wikipedia".to_owned(),
                Some("https://w.org/z".to_owned())
            ),
            BodyLine::source(
                "https://bbc.com/z".to_owned(),
                Some("https://bbc.com/z".to_owned())
            ),
        ]
    );
}

#[test]
fn web_search_sources_only_link_http_urls() {
    // Python `link_content` drops the click span for any other scheme.
    let output = json!({
        "query": "zidane",
        "sources": [
            {"title": "Local doc", "url": "file:///etc/passwd"},
            {"title": "FTP mirror", "url": "ftp://mirror.example"},
            {"title": "HTTP source", "url": "HTTP://example.com/s"},
        ],
    });
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&output)),
        [
            BodyLine::from("query: zidane".to_owned()),
            BodyLine::from(String::new()),
            BodyLine::from("Sources:".to_owned()),
            BodyLine::source("Local doc".to_owned(), None),
            BodyLine::source("FTP mirror".to_owned(), None),
            BodyLine::source(
                "HTTP source".to_owned(),
                Some("HTTP://example.com/s".to_owned())
            ),
        ]
    );
}

#[test]
fn web_search_answer_skips_empty_and_trims_trailing_newlines() {
    // Python `if result.answer:` drops an empty answer; `_yield_text` strips
    // the trailing newlines of the "answer: ..." string.
    let output = json!({
        "query": "zidane",
        "answer": "",
        "sources": [],
    });
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&output)),
        [BodyLine::from("query: zidane".to_owned())]
    );

    let multiline = json!({
        "query": "zidane",
        "answer": "first\nsecond\n\n",
    });
    assert_eq!(
        format_effect_output(Some("web_search"), Some(&multiline)),
        [
            BodyLine::from("query: zidane".to_owned()),
            BodyLine::from("answer: first".to_owned()),
            BodyLine::from("second".to_owned()),
        ]
    );
}

#[test]
fn web_fetch_content_trims_outer_newlines_like_yield_text() {
    // Python `_yield_text`: `clean_output(content.strip("\n"))`, dropped when empty.
    let padded = json!({"content": "\n\nThe body.\n\n"});
    assert_eq!(format(Some("web_fetch"), Some(&padded)), ["The body."]);

    let blank = json!({"content": "\n\n"});
    assert!(format(Some("web_fetch"), Some(&blank)).is_empty());

    let ansi = json!({"content": "\n\x1b[31mbold\x1b[0m\n"});
    assert_eq!(format(Some("web_fetch"), Some(&ansi)), ["bold"]);
}
