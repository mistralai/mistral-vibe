use super::*;

#[test]
fn narration_summarize_params_go_out_camelcase() {
    let params = NarrationSummarizeParams {
        session_id: "s1".into(),
        user_message: "hello".into(),
        assistant_text: "hi there".into(),
        error: None,
        message_id: Some("m1".into()),
    };
    let json = serde_json::to_value(&params).unwrap();
    assert_eq!(json["sessionId"], "s1");
    assert_eq!(json["userMessage"], "hello");
    assert_eq!(json["assistantText"], "hi there");
    assert_eq!(json["error"], Value::Null);
    assert_eq!(json["messageId"], "m1");
}

#[test]
fn narration_summarize_response_tolerates_null_and_missing_summary() {
    let text = r#"{"summary":"all done"}"#;
    let summary = serde_json::from_str::<NarrationSummarizeResponse>(text)
        .unwrap()
        .summary;
    assert_eq!(summary.as_deref(), Some("all done"));
    let none = serde_json::from_str::<NarrationSummarizeResponse>(r#"{"summary":null}"#)
        .unwrap()
        .summary;
    assert!(none.is_none());
    let missing = serde_json::from_str::<NarrationSummarizeResponse>(r#"{}"#).unwrap();
    assert!(missing.summary.is_none());
}
